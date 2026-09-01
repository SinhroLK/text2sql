from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass

from .index import (
    LoadedRetrievalIndex,
    RetrievalIndexEntry,
    RetrievalIndexError,
    normalize_retrieval_text,
)


RANDOM_RETRIEVAL_VERSION = "random-fixed-v1"
SIMILARITY_RETRIEVAL_VERSION = "tfidf-cosine-v1"


@dataclass(frozen=True)
class RankedRetrievalEntry:
    entry: RetrievalIndexEntry
    rank: int
    score: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "retrieval_id": self.entry.retrieval_id,
            "db_id": self.entry.db_id,
            "score": self.score,
        }


@dataclass(frozen=True)
class RetrievalSelection:
    strategy: str
    entries: tuple[RankedRetrievalEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "k": len(self.entries),
            "selected": [item.to_dict() for item in self.entries],
        }


class FixedRandomSelector:
    version = RANDOM_RETRIEVAL_VERSION

    def __init__(
        self, index: LoadedRetrievalIndex, *, k: int, seed: int
    ) -> None:
        _validate_k(index, k)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise RetrievalIndexError("random retrieval seed must be an integer")
        self.index = index
        self.k = k
        self.seed = seed
        sampled = random.Random(seed).sample(index.entries, k)
        self._selection = RetrievalSelection(
            strategy=self.version,
            entries=tuple(
                RankedRetrievalEntry(entry=entry, rank=rank, score=None)
                for rank, entry in enumerate(sampled, start=1)
            ),
        )

    def select(self, question: str) -> RetrievalSelection:
        if not normalize_retrieval_text(question):
            raise RetrievalIndexError("retrieval question must not be empty")
        return self._selection


class TfidfCosineSelector:
    version = SIMILARITY_RETRIEVAL_VERSION

    def __init__(self, index: LoadedRetrievalIndex, *, k: int) -> None:
        _validate_k(index, k)
        self.index = index
        self.k = k
        document_frequency: Counter[str] = Counter()
        for entry in index.entries:
            document_frequency.update(set(entry.question_tokens))
        total = len(index.entries)
        self._idf = {
            token: math.log((total + 1) / (frequency + 1)) + 1.0
            for token, frequency in document_frequency.items()
        }
        self._vectors = tuple(
            _tfidf_vector(entry.question_tokens, self._idf)
            for entry in index.entries
        )
        self._norms = tuple(_norm(vector) for vector in self._vectors)

    def select(self, question: str) -> RetrievalSelection:
        tokens = tuple(normalize_retrieval_text(question).split())
        if not tokens:
            raise RetrievalIndexError("retrieval question must not be empty")
        query_vector = _tfidf_vector(tokens, self._idf)
        query_norm = _norm(query_vector)
        scored: list[tuple[float, str, RetrievalIndexEntry]] = []
        for entry, vector, document_norm in zip(
            self.index.entries, self._vectors, self._norms, strict=True
        ):
            score = _cosine(
                query_vector, query_norm, vector, document_norm
            )
            scored.append((score, entry.retrieval_id, entry))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return RetrievalSelection(
            strategy=self.version,
            entries=tuple(
                RankedRetrievalEntry(
                    entry=entry,
                    rank=rank,
                    score=round(score, 12),
                )
                for rank, (score, _, entry) in enumerate(
                    scored[: self.k], start=1
                )
            ),
        )


def build_retrieval_selector(
    index: LoadedRetrievalIndex,
    *,
    strategy: str,
    k: int,
    seed: int | None,
) -> FixedRandomSelector | TfidfCosineSelector:
    if strategy == RANDOM_RETRIEVAL_VERSION:
        if seed is None:
            raise RetrievalIndexError("random retrieval requires a seed")
        return FixedRandomSelector(index, k=k, seed=seed)
    if strategy == SIMILARITY_RETRIEVAL_VERSION:
        if seed is not None:
            raise RetrievalIndexError("TF-IDF retrieval does not use a seed")
        return TfidfCosineSelector(index, k=k)
    raise RetrievalIndexError(f"unknown retrieval strategy: {strategy!r}")


def _validate_k(index: LoadedRetrievalIndex, k: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise RetrievalIndexError("retrieval k must be a positive integer")
    if k > len(index.entries):
        raise RetrievalIndexError("retrieval k exceeds index size")


def _tfidf_vector(
    tokens: tuple[str, ...], idf: dict[str, float]
) -> dict[str, float]:
    counts = Counter(tokens)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {
        token: (count / total) * idf[token]
        for token, count in counts.items()
        if token in idf
    }


def _norm(vector: dict[str, float]) -> float:
    return math.sqrt(sum(value * value for value in vector.values()))


def _cosine(
    left: dict[str, float],
    left_norm: float,
    right: dict[str, float],
    right_norm: float,
) -> float:
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    dot = sum(value * right.get(token, 0.0) for token, value in left.items())
    return dot / (left_norm * right_norm)
