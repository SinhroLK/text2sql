# REF-001: bounded B7 selection and repair

Status: `DONE`. The complete 31-example development run and independently verified EVAL-003 report exist. B7 scores 5/31 versus B6R 6/31; provider failures limit interpretation. See [results and operational caveats](b7-results.md). No B7 accuracy improvement is claimed.

## Frozen experiment

`configs/experiments/exp007-b7.toml` binds the exact B6R configuration checksum and introduces a separate B7 arm. Historical B0–B6R/B5 prompts, provider paths, checkpoints, and scores are unchanged. B7P and typed semantic planning remain deferred.

The first candidate uses the B6R prompt unchanged. Candidate two adds a fixed instruction to check output grain, join multiplicity, filters, and aggregation. Candidate three adds a fixed instruction to consider CTE/subquery structure, SQLite compatibility, and ordering. These are generation strategies, not a separate semantic-planning model. They retain the frozen model, temperature, seed, low reasoning effort, and 1,024-token output limit. There are at most three initial calls and one repair call per question.

The B7 sandbox overrides only the source-image and memory budgets: 160 MiB and 512 MiB respectively. The Airlines, electronic_sales, and f1 development databases exceed SAFE-002's default 64-MiB image budget. All six are standalone snapshots. The 10-second worker deadline, 1,000-row result cap, and 1-MiB result-data cap remain enforced.

External-knowledge documents are not added in this arm, preserving the B6R input context for this selection/repair experiment.

## Validation and ranking

`RefinementPipeline` accepts question/schema/context, a provider, and a sandbox; it has no evaluator or gold-result input. It records each candidate's SQL, generation finish reason, usage, validation, sandbox audit, result signature, and selection rank.

Nonempty text is insufficient: only `finish_reason="stop"` is treated as a complete generation. Partial `length` responses remain auditable but are not executed. Exact token-based SQL duplicates reuse diagnostics and do not get extra votes. Literal values remain part of candidate identity; the validator's literal-redacted AST hash is deliberately not used for deduplication.

Unique candidates are ranked lexicographically by:

1. Sandbox outcome: complete success, truncated preview, execution/resource failure, validation rejection, then generation failure.
2. SAFE-001 validity and successful EXPLAIN preparation. The latter is SAFE-001's existing `EXPLAIN QUERY PLAN` check, not an independent semantic signal.
3. Agreement with other distinct successful queries producing the same ordered row values and column count. Empty results get no consensus bonus. Rows are not included in the candidate audit, only their signature and count.
4. Coverage of question words that also appear in schema identifiers, using validated referenced identifiers.
5. Earlier candidate ID as a stable tie-breaker.

Only a complete sandbox success can be returned as the selected SQL. Truncated previews, timeout results, and invalid SQL are never fallback answers. These are proxy signals: execution and consensus do not prove that SQL answers the question correctly. Initial ranking and final ranking evidence are retained separately.

## One repair at most

Repair is considered only if no initial candidate completed successfully. Eligible targets are identifier/syntax/SQLite validation errors, validated execution errors/timeouts, or truncated generation with partial SQL. Policy violations such as writes/extensions, empty provider responses, and resource/truncation previews are not repair targets.

The repair prompt receives the question/schema context, failed SQL clearly marked as untrusted, and structured status/error codes. It does not receive raw SQLite exception text, gold answers, or result rows. The repair passes through the same completion, duplicate, validation, and sandbox checks. There is no recursive repair loop. If no usable candidate remains, the workflow records `no_usable_candidate` and returns no SQL.

## Commands

Provider-free resource and token audit:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.refinement.cli preflight \
  --output artifacts/reports/ref001-b7-audit.json
```

After choosing a budget and configuring `GROQ_API_KEY` in the environment, start or resume the paid development run:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.refinement.cli run \
  --run-dir artifacts/experiments/exp007-b7 \
  --max-cost-usd 1.00
```

The CLI does not automatically load `.env`. The installed entry point is `text2sql-refine-b7`. Reusing the same directory resumes only an identity-compatible run; choose a new directory for an independent experiment.

Re-score an already completed run without credentials or provider calls:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.refinement.cli score \
  --run-dir artifacts/experiments/exp007-b7
```

Preflight prepares only the frozen development examples, verifies their official evaluation resources, probes every database in SAFE-002, checks input-token budgets, and hashes contexts, source code, dependencies, database images, and evaluation resources. The CLI requires exactly 31 development IDs. Runtime selection never receives the gold values used later for scoring.

## Cost, pacing, and recovery

The run allows at most 124 provider requests, with no hidden SDK retries. Each request must fit a 6,000-token input budget plus the inherited 1,024-token output cap, below the existing 7,200-token safe per-minute envelope. The live adapter spaces requests by 62 seconds. Budget for roughly 1.5–2.2 hours of pacing for 93–124 requests, plus sandbox/scoring time; this is not a completion-time guarantee. Account quotas or shared traffic can still produce rate limits.

Each call reserves the full input/output cost envelope before invoking the provider, including failures with ambiguous billing. At the recorded on-demand rates ($0.15 input/$0.60 output per million tokens), 124 full reservations total $0.1877856. The configured spending cap is enforced against those conservative reservations. This is a local estimate, not a provider-side billing guarantee; prices and token estimates can change. Rates were checked on 13 September 2026 in [Groq's model documentation](https://console.groq.com/docs/model/openai/gpt-oss-120b). Account limits are described in [Groq's rate-limit documentation](https://console.groq.com/docs/rate-limits).

`calls.jsonl` contains an identity header, hash-chained durable reservations, and responses. A file lock prevents concurrent writers. The identity includes source/dependency versions, configuration/model, context/data/resource hashes, expected IDs, rate assumptions, and the chosen cost cap. Mismatch, corruption, unknown IDs, and changed prompts fail closed. Reservations are flushed before a request; responses are flushed before use. Completed questions are separately checksummed in `predictions.jsonl`.

Resume reuses recorded responses without issuing the same call again. A reservation without a response becomes an explicitly failed `interrupted_call_not_reissued` attempt. Provider failures and rate limits are recorded as generation failures rather than silently retried; inspect them when interpreting a run. Oversized repair inputs are rejected before spending. Completed question decisions are preserved; an interrupted question may rerun its sandbox checks, so nondeterministic execution can change its repair path, in which case a changed cached prompt fails closed.

## Reporting and interpretation

For automatic quota pauses and retries, use the [unattended run script](b7-auto-run.md).

Once all decisions are durable, the evaluation phase scores complete successful candidates using EVAL-003 and writes `report.json`. It reports first-pass, selection-only, and final accuracy, initial oracle coverage at three candidates, repair success, per-database final counts, tokens, elapsed candidate-workflow time, request counts, and reserved costs. Oracle coverage is a diagnostic upper bound, not a runtime selection rule or an unbiased sampled Pass@k estimate.

Every metric retains all development questions in its denominator. Refused, incomplete, failed, or truncated candidates count as failures and are not sent through evaluation as executable answers. Thus first-pass is measured under B7's safety/resource policy; direct comparison with historical B6R includes this policy difference. Preflight/context-preparation time is outside the recorded per-question workflow duration, while provider pacing waits are included. Repair success means the repaired candidate passes EVAL-003, not merely that it executes.

Preserve the 104-example test split. Completion requires the 31/31 prediction checkpoint and scored report regardless of whether B7 improves on B6R's recorded 6/31 development result.

## Offline verification

`tests/test_refinement.py` covers distinct strategies, deterministic ties, duplicate and empty-result voting, literal identity, successful/failed single repair, unsafe and truncated responses, budget enforcement, drift/corruption/concurrency rejection, interrupted-call accounting, completed-run resume, provider-free scoring, and SDK completion metadata. `tests/test_refinement_quota.py` adds quota persistence, daily/minute waits, invalid-budget/ledger checks, and 429 retries. The complete suite has 193 passing tests.
