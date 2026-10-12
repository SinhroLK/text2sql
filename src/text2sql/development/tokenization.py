"""Local o200k text counting with an explicitly estimated chat allowance."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

VOCABULARY_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
VOCABULARY_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
# The public o200k regex from tiktoken_ext/openai_public.py. Harmony shares these
# ordinary-text ranks; user text is encoded as text, never as control tokens.
PATTERN = "|".join([
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r"\p{N}{1,3}", r" ?[^\s\p{L}\p{N}]+[\r\n/]*", r"\s*[\r\n]+", r"\s+(?!\S)", r"\s+",
])


def load_local_counter(path: Path):
    import tiktoken
    from tiktoken.load import load_tiktoken_bpe
    # Check before constructing the tokenizer. Missing data never triggers fetch.
    if hashlib.sha256(path.read_bytes()).hexdigest() != VOCABULARY_SHA256:
        raise ValueError("Local tokenizer vocabulary checksum mismatch")
    encoding = tiktoken.Encoding(name="recovery-o200k-ordinary-v1", pat_str=PATTERN,
                                mergeable_ranks=load_tiktoken_bpe(str(path), expected_hash=VOCABULARY_SHA256),
                                special_tokens={})
    return lambda text: len(encoding.encode_ordinary(text))


def reserve_input_tokens(text_tokens: int) -> int:
    if type(text_tokens) is not int or text_tokens < 0:
        raise ValueError("Text token count must be a nonnegative integer")
    # This allowance is an engineering estimate, not a provider-template proof.
    return math.ceil((text_tokens + 256) * 1.15)
