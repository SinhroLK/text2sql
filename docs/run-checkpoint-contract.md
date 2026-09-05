# Run identity and completion accounting

Implemented on 2026-09-05 for the baseline experiment runner and direct Groq
pipeline, as Phase 5 review follow-up. This is not a completed B7P live runner.
Historical predictions and scores are unchanged; their original completion policy
must remain identifiable when comparing arms.

## Resume contract

Before the first provider call, the runner prepares every development prompt and
writes `<predictions>.run.json`. Each new prediction carries
`schema_version=2` and the same `run_contract_sha256`. The contract binds:

- Exact questions, development membership, database IDs, schema hashes, prompt
  hashes/versions, sampling/linking settings and retrieval audit.
- Database file hashes, dataset/evaluation manifests, experiment config hash,
  actual provider/model and exposed runtime settings, including endpoint.
- Package source hashes, canonical dependency files, Python/SQLite versions and
  installed Groq, HTTPX, Pydantic, DSPy, LiteLLM and Optuna versions.

The manifest also records Git revision and dirty state when available. Source
hashes detect implementation drift even within one dirty revision. Git metadata
is audit context; documentation-only changes do not alter the compatibility hash.
Provider credentials are not included.

Resume reconstructs all inputs before any provider call, checks the original
manifest and every completed record, and verifies duplicate SQL fields agree.
Completed and terminally failed examples are both skipped. A change to a pending
example also invalidates resume. Cached sample values are cleared between runs.
SQLite inputs must be frozen files without a live WAL; the runner does not lock
external database writers. Hashing database files adds an offline preflight cost.

An advisory Linux file lock on `<predictions>.lock` excludes other cooperating
writers. The lock file stays on disk; ownership ends when the process exits.
Malformed JSONL tails, malformed/missing manifests, and old checkpoints without
provenance are rejected. Nothing is silently truncated, inferred, or upgraded.
Preserve the original artifacts and use a new output path for a changed contract.
A transport interruption before the first prediction still leaves the manifest,
so changing the inputs cannot silently turn that interrupted run into a new run.

## Completion policy

The direct Groq adapter accepts exactly one nonempty text choice with
`finish_reason="stop"`, without tool calls or refusal. It retains the finish
reason on success. Truncation, missing/unknown finish reason, multiple choices,
empty choices/text, tool output and refusal become terminal completion failures.
The pipeline records `incomplete_completion`, `empty_completion`, or
`unsupported_completion`, the safe finish-reason value, and returned token usage.
It does not execute or expose rejected text as a SQL candidate.

Each terminal failure occupies its original example ID, has empty generated SQL,
and is checkpointed once. The evaluator accepts that empty value only through an
explicit versioned failure record; SQLite rejects it before execution. It counts
as incorrect in the fixed denominator. Reports include `failed_completions` and
include failed-completion token usage. Resume does not sample another answer.

Transport/API failures keep the existing bounded retry behavior and interrupt
an unfinished run after retries are exhausted. Invalid JSON responses also abort.
They do not produce a completed score or remove examples. Resume uses the same
contract; usage unavailable from a failed transport is not invented. Full
per-transport-attempt telemetry remains future work.

## B7P failure policy and remaining integration

For the upcoming planner → one repair → composer → SQL runner:

1. Preserve all 31 development IDs in the denominator. A required construct
   unsupported by the frozen plan version is `unsupported_plan`; do not drop the
   ID, simplify the question, or silently substitute a baseline query.
2. Permit only the predeclared one schema/format repair. An unresolved invalid
   plan is a terminal planning failure; no SQL-provider call follows it.
3. Apply the same terminal SQL-completion policy above. Ordinary completed but
   incorrect SQL remains an execution/evaluation failure, not a provider failure.
4. Keep planner, repair and SQL-stage attempts, usage and lineage separately.
   Infrastructure interruptions leave the run incomplete, not selectively scored.

These B7P planning failure codes and stage accounting are the required policy;
they are not yet implemented by a live orchestrator. Next implement and test that
orchestrator offline, including an unsupported-plan compatibility audit, then
freeze MODEL-001 candidates and budgets. MODEL-001 remains NOT STARTED and GEN-001
remains IN PROGRESS. No paid calls or holdout evaluation were performed here.

Verification: 182 offline tests pass, including prompt/question/database/runtime
drift, legacy records, inconsistent failure state, first-call interruption,
concurrent writers, no-retry truncation, failed usage, and real evaluation with a
failed completion retained in the denominator.
