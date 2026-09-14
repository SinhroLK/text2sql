# B7 development result and REF-001 closeout

The completed `exp007-b7-gpt-oss-120b-v1` run covers all 31 frozen development
IDs across six databases. Independent provider-free scoring validated the
current source/configuration/resource identity, journal hash chain, prediction
checksums, exact ID coverage, and reproduced all evaluation results. The
original report is preserved at `artifacts/experiments/exp007-b7/report.json`.
The 104-example test split was not evaluated.

## Results

| Measurement | Correct | Accuracy |
|---|---:|---:|
| Historical B6R | 6/31 | 19.35% |
| B7 first candidate | 4/31 | 12.90% |
| B7 selection only | 5/31 | 16.13% |
| B7 final (including repair) | 5/31 | 16.13% |
| B7 initial-candidate oracle at three | 5/31 | 16.13% |

Selection gains `local269` over B7 first-pass and loses no first-pass successes.
All five questions with a correct initial candidate were selected correctly;
the observed ranker is therefore not the bottleneck on this run. This does not
establish general ranker quality or rule out gains from better generation.
Three repairs were attempted; none was correct under EVAL-003.

Compared with historical B6R, B7 gains `local269` but loses `local171` and
`local270`. Correct final IDs are `local202`, `local269`, `local274`, `local275`,
and `local310`. B6R remains the best recorded development arm. A one-question
difference on this small development set is not a statistical superiority claim;
B7 also uses different execution safety/resource limits.

Per-database final counts: Airlines 0/2, city_legislation 1/10,
electronic_sales 0/1, f1 1/9, music 0/1, oracle_sql 3/8.
Twenty-three questions have a usable selected SQL query; eight have none.

## Operational limitations

This is a completed but operationally degraded run, not a clean estimate of
multi-candidate generation quality. There are 96 logical call reservations,
95 recorded responses, and one interrupted call deliberately not reissued.

| Candidate outcome | Count |
|---|---:|
| Complete sandbox success (not necessarily correct) | 47 |
| Generation/provider failure | 26 |
| Validation rejection | 19 |
| Timeout | 3 |
| Memory/resource limit | 1 |

The 26 generation failures comprise 21 `provider_rate_limit`, four
`provider_failure`, and one `interrupted_call_not_reissued`. All remain in the
31-question denominator; do not drop them or replace their predictions after
seeing evaluation results. Validation diagnostics include eight SQLite
validation errors, five unsupported statements, four unknown columns, one
unknown table, and one ambiguous column. These are diagnostic codes, not a
gold-informed semantic error classification.

The quota ledger contains only 25 reservations (175,600 reserved tokens), while
the call journal contains 96. Thus the ledger does not demonstrate quota-aware
execution throughout the run. Persisted rate-limit failures are consistent with
the non-auto-quota provider path; the artifacts do not establish the exact
restart command. Do not describe this as a fully quota-managed successful run.

Recorded usage is 191,575 input and 32,789 output tokens. The conservative cost
reservation is $0.1453824, not an invoice or exact bill. Recorded candidate
workflow time is 5,465,198 ms (about 91.1 minutes), not total wall-clock time
across restarts and pauses. Offline re-scoring changes only the transient
`generation.replayed_calls` counter from 2 to 0; all other report fields match.
The original report retains the original counter.

## Completion and next steps

REF-001 meets its documented completion criteria: implementation and safety
tests, audited candidate/selection/repair decisions, complete development
checkpoint, and scored report. Completion does not imply accuracy improvement.
All 193 offline tests pass. Raw artifacts are preserved; file-level SHA-256
checksums are recorded in `configs/experiments/exp007-b7-artifacts.sha256`.

Next is roadmap Phase 6: define a threat model, version benign/adversarial
fixture cases, evaluate input/output policies (S0/S1), and report attack success,
unsafe-query rate, and benign false positives. Use disposable fixtures, never
production databases. Do not open the test split or launch another paid run as
part of this closeout.

Before any future paid B7 replication, make quota mode part of the recorded run
identity, audit transport attempts and quota waits, and test safe recovery from
known-rejected 429 requests. Use a separately versioned experiment/run directory
and retain this negative result. Such changes are follow-up work, not edits to
this frozen run. Semantic planning/B7P remains deferred under the roadmap.
