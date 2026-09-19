# RUN-001 final evaluation freeze

RUN-001 freezes the protocol for the one-time 104-example Spider2-Lite SQLite
held-out evaluation. It does not execute a provider request, read held-out gold
results, or score the test split.

## Final arms

The final generation arms are B0, B1, B2, B4, B6, and B6R. Each has a separate
test-only configuration under `configs/final/`. Every model, prompt, sampling,
retrieval, and schema-linking parameter must equal its frozen development
template; only the experiment ID and split may differ.

B6R is the primary arm because it has the highest frozen development score.
B0/B1 measure schema contribution, B2 measures M-Schema, B4 measures retrieved
demonstrations, B6 preserves the negative pruning result, and B6R measures the
recall-first repair. B3, B5, and B7 remain reported development evidence but are
not repeated on the held-out split. B7 is excluded because its frozen run was
operationally degraded by 26 provider failures. S1 remains the separate
synthetic security evaluation and is not applied to benchmark gold results.

## Statistical protocol

Execution accuracy is the primary metric. Each arm receives a paired percentile
bootstrap 95% confidence interval using 10,000 resamples and seed 20260919.
Predeclared paired comparisons use the two-sided exact McNemar test and Holm
correction across the six-comparison family at alpha 0.05. Secondary reporting
includes executable-query rate and token usage. Security reporting keeps attack
success, unsafe-query, and benign false-positive rates separate from benchmark
accuracy.

The provider is run once per arm with the frozen zero-temperature configuration.
All attempts remain in the denominator. Failed, interrupted, missing, or extra
predictions cannot be replaced after scoring. The report must distinguish
statistical uncertainty from provider nondeterminism and the limited custom
benchmark scope.

## Opening gate

Before any held-out generation:

1. `python -m text2sql.finalization.cli verify` must reproduce the committed
   freeze manifest exactly.
2. The working tree must be clean and the release commit must carry the
   `text2sql-final-v1` tag.
3. All offline tests and dependency checks must pass.
4. A read-only resource preflight must confirm exact coverage of 104 test IDs,
   24 test databases, evaluation metadata, and official result variants.
5. Output paths must be new and arm-specific. Existing predictions and reports
   are immutable.
6. Generation for all arms completes before any held-out scoring command runs.

After the first score is revealed, no prompt, selector, model, policy, or
example-specific rule may change. Any operational failure stays in the primary
result; a separately labelled replication requires a new protocol version and
must preserve the original run.

## RUN-002 script

The executable `scripts/run_final_evaluation.sh` is the only supported
entry point for the final run. It performs the following sequence:

1. run all offline tests and `pip check`;
2. reproduce the RUN-001 manifest and require a clean working tree at the
   `text2sql-final-v1` tag;
3. verify 104 held-out IDs, 24 databases, official result variants, evaluation
   metadata, and the B4 retrieval index;
4. generate B0, B1, B2, B4, B6, and B6R sequentially into per-example JSONL
   checkpoints;
5. verify complete 104-example coverage for every arm before scoring any arm;
6. write immutable reports, paired statistics, CSV output, and a checksum
   manifest for every RUN-002 artifact.

A terminal provider failure is recorded as the arm's attempt for that example
and the script stops immediately. Resolve the operational issue and run the same
command again. It resumes at the next unrecorded example and does not replace
the failed attempt. Existing reports and analysis files must reproduce exactly.

## Running it

From the project root, first run the paid-call-free preflight:

```bash
scripts/run_final_evaluation.sh preflight
```

This command opens and verifies the held-out resources but does not call Groq
or score predictions. Review the `run002_preflight_ready` event and
`artifacts/final/run002-v1/preflight.json`.

Put the key in your own trusted `.env` file or export it in the shell:

```bash
export GROQ_API_KEY="..."
scripts/run_final_evaluation.sh run
```

The full command makes up to 624 initial generation requests before configured
transport retries. It can take a long time and incur provider charges. The
script prints one JSON progress event per retained attempt. If interrupted,
run the same command again to resume the existing checkpoints. An in-flight
request with an unknown outcome is retained as an ambiguous failed attempt and
is never reissued.

Optional environment settings:

```bash
export FINAL_OUTPUT_ROOT="artifacts/final/run002-v1"
export FINAL_REQUEST_DELAY_SECONDS="0"
scripts/run_final_evaluation.sh run
```

Use a positive request delay if required by the active provider tier. Do not
change `FINAL_OUTPUT_ROOT` after starting the run. On completion, the primary
outputs are:

- `artifacts/final/run002-v1/run-manifest.json`;
- `artifacts/final/run002-v1/reports/`;
- `artifacts/final/run002-v1/analysis/final-analysis.json`;
- `artifacts/final/run002-v1/analysis/final-arm-summary.csv`.

## Manifest verification

Verify the deterministic RUN-001 manifest without accessing held-out data:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.finalization.cli verify
```
