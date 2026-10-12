"""Durable, bounded request accounting; no networking or implicit retries."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from text2sql.providers import GroqProviderError


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Limits:
    max_calls: int
    max_total_tokens: int
    tokens_per_minute: int
    tokens_per_day: int
    max_attempts_per_example: int
    max_cost_usd: str
    input_usd_per_million: str
    output_usd_per_million: str

    def __post_init__(self) -> None:
        for key in ("max_calls", "max_total_tokens", "tokens_per_minute",
                    "tokens_per_day", "max_attempts_per_example"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError(f"{key} must be a positive integer")
        for key in ("max_cost_usd", "input_usd_per_million", "output_usd_per_million"):
            value = Decimal(getattr(self, key))
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{key} must be finite and positive")

    def cost(self, inputs: int, outputs: int) -> Decimal:
        return (Decimal(inputs) * Decimal(self.input_usd_per_million)
                + Decimal(outputs) * Decimal(self.output_usd_per_million)) / 1_000_000


class DeferredRequest(RuntimeError):
    def __init__(self, seconds: float):
        self.seconds = seconds
        super().__init__(f"Request deferred for {seconds:.3f} seconds; no provider call")


class RequestJournal:
    """Reserve every attempt before invocation; replay results, never ambiguous calls.

    Caller supplies a single-attempt function (provider max_retries=0), a pinned
    identity covering source/config/resources, and verified token reservations.
    Quotas are local to this locked run, not account-wide guarantees.
    """
    def __init__(self, path: Path, identity: dict, limits: Limits, *, clock=time.time):
        self.limits, self.clock = limits, clock
        ids = identity.get("expected_ids")
        if (not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids)
                or len(ids) != len(set(ids))):
            raise ValueError("Journal requires unique expected IDs")
        self.ids = set(ids)
        self.identity = {"run": identity, "limits": asdict(limits)}
        self.events: list[dict] = []
        self.previous = "0" * 64
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.stream.seek(0)
            for line in self.stream:
                record = json.loads(line)
                if (record["previous"] != self.previous
                        or record["sha256"] != digest([self.previous, record["data"]])):
                    raise ValueError("Request journal integrity mismatch")
                self.previous = record["sha256"]
                self.events.append(record["data"])
            first = {"event": "identity", "identity": self.identity}
            if self.events and self.events[0] != first:
                raise ValueError("Request journal resume identity mismatch")
            if not self.events:
                self._append(first)
        except BaseException:
            self.stream.close()
            raise

    def close(self) -> None:
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _append(self, data: dict) -> None:
        hashed = digest([self.previous, data])
        self.stream.write(json.dumps({"previous": self.previous, "sha256": hashed,
                                      "data": data}, sort_keys=True, allow_nan=False) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.previous = hashed
        self.events.append(data)

    def call(self, example_id: str, prompt: str, *, input_tokens: int,
             max_completion_tokens: int, invoke) -> dict:
        if example_id not in self.ids:
            raise ValueError("Request outside development scope")
        for count in (input_tokens, max_completion_tokens):
            if type(count) is not int or count <= 0:
                raise ValueError("Token reservations must be positive integers")
        request_hash = digest([prompt, input_tokens, max_completion_tokens])
        reservations = [e for e in self.events if e["event"] == "reserve"]
        by_number = {e["number"]: e for e in reservations}
        for event in self.events:
            if event["event"] == "outcome":
                result = event["result"]
                if result.get("input_tokens", 0) + result.get("output_tokens", 0) > by_number[event["reservation"]]["tokens"]:
                    raise ValueError("Observed usage exceeded its reservation; stop before further calls")
        previous = [e for e in reservations if e["example_id"] == example_id]
        now = self.clock()
        if not math.isfinite(now) or now < 0:
            raise ValueError("Invalid clock")
        if previous:
            if any(e["request_sha256"] != request_hash for e in previous):
                raise ValueError("Resumed request changed")
            last = previous[-1]
            outcomes = [e for e in self.events if e["event"] == "outcome"
                        and e["reservation"] == last["number"]]
            if not outcomes:
                return {"status": "interrupted", "error_code": "ambiguous_call_not_reissued"}
            outcome = outcomes[-1]["result"]
            if outcome["status"] != "known_rejected":
                return outcome
            if len(previous) >= self.limits.max_attempts_per_example:
                return {"status": "failed", "error_code": "retry_limit"}
            if now < outcome["retry_at"]:
                raise DeferredRequest(outcome["retry_at"] - now)
        tokens = input_tokens + max_completion_tokens
        if tokens > min(self.limits.tokens_per_minute, self.limits.tokens_per_day):
            raise ValueError("Oversized single request; waiting cannot fix this")
        cost = self.limits.cost(input_tokens, max_completion_tokens)
        if (len(reservations) >= self.limits.max_calls
                or sum(e["tokens"] for e in reservations) + tokens > self.limits.max_total_tokens
                or sum((Decimal(e["reserved_usd"]) for e in reservations), Decimal(0))
                + cost > Decimal(self.limits.max_cost_usd)):
            raise ValueError("Call, token, or cost budget exhausted")
        outcomes = {e["reservation"]: e["result"] for e in self.events
                    if e["event"] == "outcome"}
        def charge(event, window):
            # Minute admission retains maximum reservations. Daily accounting
            # settles only responses with trustworthy, positive reported usage.
            result = outcomes.get(event["number"], {})
            inputs, outputs = result.get("input_tokens"), result.get("output_tokens")
            if (window == 86402 and result.get("status") in ("complete", "failed")
                    and type(inputs) is int and inputs > 0
                    and type(outputs) is int and outputs >= 0):
                return inputs + outputs
            return event["tokens"]
        delays = []
        for window, cap in ((62, self.limits.tokens_per_minute),
                            (86402, self.limits.tokens_per_day)):
            active = sorted((e for e in reservations if e["time"] + window > now),
                            key=lambda e: e["time"])
            used = sum(charge(e, window) for e in active)
            for event in active:
                if used + tokens <= cap:
                    break
                delays.append(event["time"] + window - now)
                used -= charge(event, window)
        if delays:
            delay = max(delays)
            self._append({"event": "defer", "example_id": example_id,
                          "time": now, "seconds": delay})
            raise DeferredRequest(delay)
        number = len(reservations) + 1
        self._append({"event": "reserve", "number": number, "example_id": example_id,
                      "request_sha256": request_hash, "time": now, "tokens": tokens,
                      "reserved_usd": str(cost)})
        try:
            response = invoke()
            complete = (response.metadata.get("completion_complete") is True
                        and response.metadata.get("finish_reason") == "stop"
                        and len(response.candidates) == 1 and bool(response.candidates[0].strip()))
            result = {"status": "complete" if complete else "failed",
                      "candidates": list(response.candidates) if complete else [],
                      "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                      "metadata": response.metadata}
        except GroqProviderError as error:
            # Only explicit HTTP 429 rejection is safe to issue again. Timeouts,
            # 5xx and other ambiguous failures consume the reservation permanently.
            delay = error.retry_after_seconds
            delay = max(62.0, delay) if isinstance(delay, (int, float)) and math.isfinite(delay) else 62.0
            result = ({"status": "known_rejected", "error_code": "rate_limit",
                       "retry_at": self.clock() + delay} if error.status_code == 429 else
                      {"status": "failed", "error_code": "provider_failure_not_reissued",
                       "http_status": error.status_code})
        except Exception:
            result = {"status": "failed", "error_code": "ambiguous_failure_not_reissued"}
        self._append({"event": "outcome", "reservation": number, "result": result})
        return result
