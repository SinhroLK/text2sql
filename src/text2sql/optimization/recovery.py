from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

import dspy
from dspy.clients.cache import Cache
from dspy.clients.disk_serialization import DeserializationError

from text2sql.observability import append_jsonl


RECOVERY_SCHEMA_VERSION = 1
RUN_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}")


class B5RecoveryError(RuntimeError):
    """A B5 recovery artifact is unsafe, corrupt, stale, or incompatible."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _jsonable_response(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable_response(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable_response(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable_response(model_dump(mode="json"))
        except TypeError:
            return _jsonable_response(model_dump())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable_response(to_dict())
    raise B5RecoveryError(
        "Refusing to cache a provider response with an unsupported value type: "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


def _response_fingerprint(value: Any) -> tuple[str, bytes]:
    payload = _canonical_bytes(_jsonable_response(value))
    return hashlib.sha256(payload).hexdigest(), payload


def _usage_tokens(value: Any) -> int:
    usage = value.get("usage") if isinstance(value, Mapping) else getattr(
        value, "usage", None
    )
    if not isinstance(usage, Mapping):
        model_dump = getattr(usage, "model_dump", None)
        usage = model_dump() if callable(model_dump) else {}
    if not isinstance(usage, Mapping):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    if all(
        isinstance(item, int) and not isinstance(item, bool) and item >= 0
        for item in (prompt, completion)
    ):
        return prompt + completion
    return 0


class StrictRunCache(Cache):
    """Run-scoped DSPy cache with restricted loading and an integrity ledger."""

    def __init__(
        self,
        directory: Path,
        *,
        run_identity_sha256: str,
        size_limit_bytes: int,
        forbidden_values: tuple[str, ...] = (),
    ) -> None:
        self.run_identity_sha256 = run_identity_sha256
        self.root = directory.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.ledger_path = self.root / "cache-ledger.json"
        self._ledger_lock = threading.RLock()
        self._forbidden_values = tuple(
            item.encode("utf-8") for item in forbidden_values if item
        )
        self._hits = 0
        self._misses = 0
        self._writes = 0
        super().__init__(
            enable_disk_cache=True,
            enable_memory_cache=True,
            disk_cache_dir=str(self.root / "entries"),
            disk_size_limit_bytes=size_limit_bytes,
            memory_max_entries=10_000,
            restrict_pickle=True,
        )
        self._ledger = self._load_ledger()

    def _empty_ledger(self) -> dict[str, Any]:
        return {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "run_identity_sha256": self.run_identity_sha256,
            "entries": {},
        }

    def _load_ledger(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return self._empty_ledger()
        try:
            ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise B5RecoveryError("B5 cache ledger is unreadable or invalid") from error
        if (
            not isinstance(ledger, dict)
            or ledger.get("schema_version") != RECOVERY_SCHEMA_VERSION
            or ledger.get("run_identity_sha256") != self.run_identity_sha256
            or not isinstance(ledger.get("entries"), dict)
        ):
            raise B5RecoveryError("B5 cache ledger identity does not match the run")
        return ledger

    def _write_ledger(self) -> None:
        _atomic_json(self.ledger_path, self._ledger)

    def get(
        self,
        request: dict[str, Any],
        ignored_args_for_cache_key: list[str] | None = None,
    ) -> Any:
        try:
            key = self.cache_key(request, ignored_args_for_cache_key)
        except Exception as error:
            raise B5RecoveryError("B5 cache request cannot be hashed safely") from error
        with self._ledger_lock:
            entry = self._ledger["entries"].get(key)
            raw = self.memory_cache.get(key)
            if raw is None:
                try:
                    raw = self.disk_cache.get(key)
                except DeserializationError as error:
                    raise B5RecoveryError(
                        f"B5 cache entry {key} failed restricted deserialization"
                    ) from error
                except Exception as error:
                    raise B5RecoveryError(
                        f"B5 cache entry {key} could not be read"
                    ) from error
            if raw is None:
                if isinstance(entry, dict) and entry.get("status") == "pending":
                    del self._ledger["entries"][key]
                    self._write_ledger()
                    self._misses += 1
                    return None
                if entry is not None:
                    raise B5RecoveryError(
                        f"Committed B5 cache entry {key} is missing or was evicted"
                    )
                self._misses += 1
                return None
            if not isinstance(entry, dict):
                raise B5RecoveryError(
                    f"Unindexed B5 cache entry {key} would contaminate this run"
                )
            fingerprint, _ = _response_fingerprint(raw)
            if fingerprint != entry.get("response_sha256"):
                raise B5RecoveryError(f"B5 cache entry {key} failed integrity validation")
            if entry.get("status") not in {"pending", "committed"}:
                raise B5RecoveryError(f"B5 cache entry {key} has an invalid state")
            if entry.get("status") == "pending":
                entry["status"] = "committed"
                entry["recovered_after_interrupted_write"] = True
                self._write_ledger()
            self.memory_cache[key] = raw
            self._hits += 1
            response = copy.deepcopy(raw)
            original_tokens = int(entry.get("provider_tokens", 0))
            if isinstance(response, dict):
                response["usage"] = {}
                response["cache_hit"] = True
                response["b5_cached_provider_tokens"] = original_tokens
            elif hasattr(response, "usage"):
                try:
                    response.usage = {}
                except Exception:
                    object.__setattr__(response, "usage", {})
                object.__setattr__(response, "cache_hit", True)
                object.__setattr__(
                    response, "b5_cached_provider_tokens", original_tokens
                )
            else:
                raise B5RecoveryError(
                    "B5 cache response cannot carry safe replay metadata"
                )
            hidden = getattr(response, "_hidden_params", None)
            if isinstance(hidden, dict):
                hidden["response_cost"] = None
            return response

    def put(
        self,
        request: dict[str, Any],
        value: Any,
        ignored_args_for_cache_key: list[str] | None = None,
        enable_memory_cache: bool = True,
    ) -> None:
        del enable_memory_cache
        try:
            key = self.cache_key(request, ignored_args_for_cache_key)
        except Exception as error:
            raise B5RecoveryError("B5 cache request cannot be hashed safely") from error
        fingerprint, serialized = _response_fingerprint(value)
        if any(secret in serialized for secret in self._forbidden_values):
            raise B5RecoveryError("Refusing to cache a provider response containing an API key")
        with self._ledger_lock:
            existing = self._ledger["entries"].get(key)
            if isinstance(existing, dict):
                if existing.get("response_sha256") != fingerprint:
                    raise B5RecoveryError(
                        "The same B5 cache key produced different responses within one run"
                    )
                return
            self._ledger["entries"][key] = {
                "status": "pending",
                "response_sha256": fingerprint,
                "provider_tokens": _usage_tokens(value),
                "created_at": _timestamp(),
            }
            self._write_ledger()
            try:
                stored = self.disk_cache.set(key, value)
            except Exception as error:
                raise B5RecoveryError(f"B5 cache entry {key} could not be written") from error
            if not stored or key not in self.disk_cache:
                raise B5RecoveryError(f"B5 cache entry {key} was not durably written")
            self.memory_cache[key] = value
            self._ledger["entries"][key]["status"] = "committed"
            self._write_ledger()
            self._writes += 1

    def stats(self) -> dict[str, Any]:
        entries = self._ledger["entries"].values()
        return {
            "cache_entries": len(self._ledger["entries"]),
            "cache_hits_this_invocation": self._hits,
            "cache_misses_this_invocation": self._misses,
            "cache_writes_this_invocation": self._writes,
            "cached_provider_tokens_original_total": sum(
                int(entry.get("provider_tokens", 0))
                for entry in entries
                if isinstance(entry, dict)
            ),
        }


def _new_run_id(now: datetime | None = None) -> str:
    moment = now or _utc_now()
    random_suffix = os.urandom(6).hex()
    return f"run-{moment.strftime('%Y%m%dt%H%M%Sz')}-{random_suffix}"


def _validated_run_id(value: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(value):
        raise B5RecoveryError(
            "B5 recovery run ID must contain only lowercase letters, digits, '.', '_' or '-'"
        )
    return value


def _acquire_run_lock(run_dir: Path) -> BinaryIO:
    lock_path = run_dir / "run.lock"
    if lock_path.is_symlink():
        raise B5RecoveryError("B5 recovery lock must not be a symbolic link")
    lock_stream = lock_path.open("a+b")
    os.chmod(lock_path, 0o600)
    try:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        lock_stream.close()
        raise B5RecoveryError(
            "B5 recovery run is already active in another process"
        ) from error
    return lock_stream


def _release_run_lock(lock_stream: BinaryIO) -> None:
    if not lock_stream.closed:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        lock_stream.close()

@dataclass
class B5RecoverySession:
    checkpoint_root: Path
    run_id: str
    run_dir: Path
    identity: dict[str, Any]
    identity_sha256: str
    state: dict[str, Any]
    cache: StrictRunCache
    _lock_stream: BinaryIO

    @classmethod
    def open(
        cls,
        checkpoint_root: str | Path,
        *,
        identity: dict[str, Any],
        cache_size_limit_bytes: int,
        resume_max_age_hours: int,
        resume_run_id: str | None = None,
        forbidden_values: tuple[str, ...] = (),
        now: datetime | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> "B5RecoverySession":
        current = now or _utc_now()
        root = Path(checkpoint_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        identity_sha256 = canonical_sha256(identity)
        if resume_run_id is None:
            run_id = _new_run_id(current)
            run_dir = root / run_id
            run_dir.mkdir(mode=0o700)
            lock_stream = _acquire_run_lock(run_dir)
            state = {
                "schema_version": RECOVERY_SCHEMA_VERSION,
                "run_id": run_id,
                "status": "running",
                "created_at": _timestamp(current),
                "updated_at": _timestamp(current),
                "resume_count": 0,
                "identity_sha256": identity_sha256,
                "identity": identity,
                "metric_records": 0,
            }
            _atomic_json(run_dir / "run-state.json", state)
            _atomic_json(
                root / "latest-run.json",
                {"run_id": run_id, "identity_sha256": identity_sha256},
            )
            event = {
                "event": "b5_recovery_run_started",
                "run_id": run_id,
                "resume_hint": f"--resume-run-id {run_id}",
            }
        else:
            run_id = _validated_run_id(resume_run_id)
            run_dir = root / run_id
            if not run_dir.is_dir() or run_dir.is_symlink():
                raise B5RecoveryError(f"B5 recovery run does not exist: {run_id}")
            state_path = run_dir / "run-state.json"
            if not state_path.is_file() or state_path.is_symlink():
                raise B5RecoveryError(f"B5 recovery run does not exist: {run_id}")
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise B5RecoveryError("B5 recovery state is unreadable or invalid") from error
            if (
                not isinstance(state, dict)
                or state.get("schema_version") != RECOVERY_SCHEMA_VERSION
                or state.get("run_id") != run_id
                or state.get("identity_sha256") != canonical_sha256(
                    state.get("identity")
                )
                or state.get("identity_sha256") != identity_sha256
                or state.get("identity") != identity
            ):
                raise B5RecoveryError(
                    "B5 recovery identity mismatch; start a new run instead of reusing this cache"
                )
            if state.get("status") == "completed":
                raise B5RecoveryError("B5 recovery run is already completed")
            try:
                created = datetime.fromisoformat(
                    str(state["created_at"]).replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError) as error:
                raise B5RecoveryError("B5 recovery creation timestamp is invalid") from error
            age_hours = (current - created).total_seconds() / 3600
            if age_hours < 0 or age_hours > resume_max_age_hours:
                raise B5RecoveryError(
                    "B5 recovery run is outside the allowed resume window; start a new run"
                )
            state["status"] = "running"
            state["updated_at"] = _timestamp(current)
            lock_stream = _acquire_run_lock(run_dir)
            state["resume_count"] = int(state.get("resume_count", 0)) + 1
            _atomic_json(state_path, state)
            event = {
                "event": "b5_recovery_run_resumed",
                "run_id": run_id,
                "resume_count": state["resume_count"],
            }
        try:
            cache = StrictRunCache(
                run_dir / "lm-cache",
                run_identity_sha256=identity_sha256,
                size_limit_bytes=cache_size_limit_bytes,
                forbidden_values=forbidden_values,
            )
        except BaseException:
            _release_run_lock(lock_stream)
            raise
        session = cls(
            root,
            run_id,
            run_dir,
            identity,
            identity_sha256,
            state,
            cache,
            lock_stream,
        )
        if event_sink is not None:
            event_sink(event)
        return session

    def close(self) -> None:
        """Release the single-process run lease and close the disk cache."""

        try:
            self.cache.disk_cache.close()
        finally:
            _release_run_lock(self._lock_stream)

    @property
    def mipro_log_dir(self) -> Path:
        target = self.run_dir / "mipro"
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        return target

    def record_metric(self, record: dict[str, object]) -> None:
        sequence = int(self.state.get("metric_records", 0)) + 1
        payload = {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "run_id": self.run_id,
            "identity_sha256": self.identity_sha256,
            "sequence": sequence,
            "recorded_at": _timestamp(),
            **record,
        }
        append_jsonl(self.run_dir / "metric-progress.jsonl", payload)
        self.state["metric_records"] = sequence
        self.state["updated_at"] = payload["recorded_at"]
        _atomic_json(self.run_dir / "run-state.json", self.state)

    def mark(self, status: str, **details: Any) -> None:
        if status not in {"running", "interrupted", "failed", "completed"}:
            raise ValueError("invalid B5 recovery status")
        self.state["status"] = status
        self.state["updated_at"] = _timestamp()
        self.state.update(details)
        _atomic_json(self.run_dir / "run-state.json", self.state)

    def assert_no_secret(self, secret: str | None) -> None:
        if not secret:
            return
        needle = secret.encode("utf-8")
        for path in self.run_dir.rglob("*"):
            if path.is_file() and needle in path.read_bytes():
                raise B5RecoveryError(
                    f"Refusing B5 recovery artifact containing an API key: {path.name}"
                )

    @contextmanager
    def activated_cache(self) -> Iterator[None]:
        previous = dspy.cache
        dspy.cache = self.cache
        try:
            yield
        finally:
            dspy.cache = previous

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "run_id": self.run_id,
            "identity_sha256": self.identity_sha256,
            "resume_count": int(self.state.get("resume_count", 0)),
            "cache_scope": "single-explicit-run",
            "cache_reuse_semantics": "continuation-not-independent-rerun",
            "cache_deserialization": "restricted",
            **self.cache.stats(),
        }
