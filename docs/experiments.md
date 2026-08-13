# Experiment protocol

## Current experiment

`phase0-smoke-v1` is an infrastructure smoke test, not a research result. It uses a deterministic mock provider and a synthetic SQLite database.

## Frozen-evaluation rules for future phases

1. Keep train, development and evaluation databases disjoint.
2. Build retrieval indexes only from the training split.
3. Never optimize prompts on final evaluation answers.
4. Persist model ID, parameters, prompt hash, schema hash and dataset version.
5. Use execution-based evaluation as the primary correctness metric.
6. Treat network errors separately from semantic errors.
7. Run every compared configuration over exactly the same example IDs.

## Pending decision

The Phase 1 decision record must select the exact Spider2-Lite scope and development dataset before a real model is called.

