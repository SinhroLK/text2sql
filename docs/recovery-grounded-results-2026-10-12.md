# Grounded prompt experiment: completed results

Analysis date: 2026-10-12. Compares recovery-grounded002 (grounded-sqlite-v2)
with recovery-live001 on the same 31 development questions in each arm.
Both runs are complete and scored. No model API calls, prediction edits, or
rescoring were performed for this review.

## Main result

The revised C2 configuration scores **13/31 (41.9%)**, versus the original
C2's **9/31 (29.0%)**: a net gain of four questions, or 12.9 percentage points.
Five previously wrong answers became correct; one previously correct answer
became wrong. This is an observed improvement on reused development questions,
not proof of generalization or a new final-test result.

| Configuration | Original correct | Revised correct | Revised executable | Revised generation failures |
|---|---:|---:|---:|---:|
| C0: 1024 completion tokens, low reasoning | 7/31 (22.6%) | 6/31 (19.4%) | 17/31 | 4 |
| C1: 3072 completion tokens, low reasoning | 5/31 (16.1%) | 8/31 (25.8%) | 20/31 | 4 |
| C2: 3072 completion tokens, medium reasoning | 9/31 (29.0%) | 13/31 (41.9%) | 25/31 | 2 |

The predeclared selection rule selects revised C2 over revised C0/C1.
This within-run selection must not be confused with the separate old/new prompt
comparison. Model, temperature, seed, arm settings, gold paths, comparator and
execution limits were checked for comparability; frozen run manifests and
generation/journal/score hashes verify successfully.

All failures count in the denominator. Each arm includes one accepted empty-result
answer (local275); revised C2 therefore has 12 nonempty correct answers, not 13.
The 93 outputs are three answers to each of 31 questions, not 93 independent cases.
The experiment changes instructions, schema presentation and sampled value evidence
together, so it cannot isolate which component caused each gain.

## Where the improvement happened

| Database | Original C2 | Revised C2 |
|---|---:|---:|
| city_legislation | 3/10 | 7/10 |
| oracle_sql | 3/8 | 3/8 |
| f1 | 2/9 | 2/9 |
| Airlines | 0/2 | 0/2 |
| electronic_sales | 0/1 | 0/1 |
| music | 1/1 | 1/1 |

C2 gains: local068, local072, local167, local171, local270.
C2 regression: local269. Thus all net improvement is in city_legislation;
oracle_sql exchanges one success for another.

- local068 now returns correct year-over-year growth, preserving the earlier
  baseline needed for window calculations.
- local167 and local171 now pass, consistent with the intended benefits of
  supplying actual gender/state encodings.
- local072 and local270 now produce queries accepted by the unchanged validator
  and matching reference results. This is changed model output, not a validator fix.
- local269 regresses from 946.0 to 360.63636. The old query restricts roots to final
  packaging combinations. The new query averages over every packaging row,
  including intermediate items and zero-filled leaves. It changes the population
  being averaged.

The airline result needs nuance: C1 gets local009 correct, so the supplied JSON
and coordinate context can support a correct answer. C2 instead loses a column
across a subquery boundary, and local010 truncates. There is no uniformly best
response for every question; choosing arms per question after looking at gold
answers would not constitute a deployable selection method.

## Generation and execution failures

Six of ten generation failures are truncations:

- C0: local009, local010, local068, local072 hit 1024 tokens.
- C2: local010 and local344 hit 3072 tokens; each reports 3070 reasoning tokens.

The other four are C1 local169/local277/local279/local335, recorded as
provider_failure_not_reissued with no HTTP status in the saved journal. These are
not evidence of incorrect generated SQL. The journal does not establish whether
their underlying cause was connectivity, timeout, or a provider-side issue.
They remain failures in the end-to-end score and should not silently be rerun into
this completed experiment.

C0 and C1 each time out executing local170 at the frozen 60-second limit.
C2 has no execution timeouts.

Revised C2's 18 unsuccessful cases consist of 12 result mismatches, four validator
rejections and two truncations. Of its 25 executable queries, 12 (48%) still
return the wrong result. Valid SQL alone remains insufficient.

## Validator versus model errors

All 19 rejected revised outputs were checked with read-only SQLite EXPLAIN.
Seven compile; twelve do not. Compilation is neither a correctness check nor
authorization to bypass project security rules.

Only one of C2's four rejected queries compiles:

| C2 example | Finding |
|---|---|
| local009 | Genuine missing column: outer query reads city, but inner query exposes city_en. |
| local169 | SQLite compiles; validator records unknown_table. This remains a pipeline blocker, with correctness unmeasured. |
| local170 | Genuine aggregate misuse: MIN inside the correlated first-term expression. Also counts the cohort-side key after a LEFT JOIN, regardless of retention matches. |
| local354 | COUNT(DISTINCT ...) OVER is unsupported by SQLite. |

The remaining compilable rejections are C0 local070/local169/local344 and C1
local070/local344/local355. Some involve deliberate comment policy; compilation
alone does not establish a validator bug.

Consequently, fixing validation alone cannot account for most C2 failures.
Even if local169 eventually passes scoring, that would add only one success
among C2's currently rejected outputs.

## Remaining semantic failures

| C2 example | Observed issue and confidence |
|---|---|
| local168 | Uses EXISTS to average distinct eligible jobs, rather than repeated skill matches. Averaging population/skill selection needs comparison against the multiple accepted variants; exact cause unresolved. |
| local269 | Confirmed regression: averages all packaging entries instead of final roots. |
| local272 | Still collapses order lines into product totals before FIFO allocation; loses explicitly requested per-line sequencing. Saved output has five rows. Omission confirmed; isolated correction not tested. |
| local273 | Now implements cumulative-range overlap and per-order percentages, a substantive change from the original simplified join. Still mismatches. Ordering among repeated order lines and reference product-name/percentage conventions need review; do not claim FIFO is entirely absent. |
| local277 | Now attempts weighted regression, but uses quantity itself as weight and one seasonality factor per product. Returns 253.875; accepted variants include 277.3343, 266.2247324863794 and 417.450560094757. Required method/seasonality interpretation remains unresolved; it no longer merely sums observed annual sales. |
| local279 | Confirmed date-format mismatch: sales dates are YYYY-MM-DD, while the recursive join compares YYYY-MM. Sales fail to match and become zero via IFNULL. Output is 300/70 versus reference 330/76; this defect may not be the only issue in the recurrence. |
| local286 | Separates sales from reviews, removing the previous direct duplication of sales by review joins. Still differs in metric choices: category rank uses revenue, packing time uses shipping deadline minus purchase, and reviews are item-weighted. Full reference attribution unresolved. |
| local309 | Still sums cumulative driver/constructor standings over races and inner-joins year coverage. Confirmed repeated semantic mistake. |
| local335 | Now selects the constructor with the lowest positive aggregate points, instead of identifying the requested lowest-scoring driver and their constructor. Also groups driver seasons by constructor. The output mismatch is substantive. |
| local336 | Now implements pairwise position changes and first-lap handling, unlike the old approximation. Pit/retirement classification remains simplified, including only same-lap pit checks; results still mismatch. |
| local355 | Now explicitly finds missed rounds and filters annual missing counts. Still mismatches; the exact remaining reference interpretation has not been isolated. |
| local356 | Still counts laps with position improvements/losses, not pairwise passes. Filters rows before LAG and incompletely handles pit/retirement events; still returns only Markus Winkelhock. |

These observations come from saved SQL/results, task questions, the earlier audit,
and a bounded read-only check of monthly_sales date values. They distinguish visible
defects from unproved explanations; no manually corrected predictions were scored.

## Recommended next steps

1. Retain revised C2 as the current development candidate. Do not spend further
   calls on the 1024-token configuration without a specific hypothesis.
2. Fix validator CTE handling in a separately versioned pipeline, with offline
   regression tests and all existing security checks retained. Evaluate saved
   predictions under that new policy in a separate output directory.
3. Build small offline fixtures for date joins, cumulative versus incremental
   measures, final hierarchy roots, per-order FIFO and pairwise event counting.
   These are targeted checks of recurring semantics, not benchmark answer hints.
4. Inspect reference ambiguity in forecasting, salary averaging and picking
   conventions before blaming every mismatch on one SQL detail.
5. Design one controlled next experiment around concise calculation planning and
   SQL checking. A larger completion budget may reduce truncations but cannot
   fix wrong measures; it also needs a free-tier quota check before preparation.
   Provider failures need explicit accounting in a new run, not edits to this one.
6. Reserve a genuinely untouched evaluation for a frozen final candidate. These
   reused development questions cannot establish the final accuracy claim.

No paid step is required for the offline work above. The user runs any future
model API experiment. Accounting ceilings in run metadata are not billing receipts.

## Reproduction

Machine-readable evidence and the audit script are in
artifacts/development/recovery-grounded-analysis-2026-10-12/.

Run from the project root:

    PYTHONPATH=src .venv/bin/python artifacts/development/recovery-grounded-analysis-2026-10-12/reproduce-audit.py

The script verifies both source experiments, computes paired changes and runs
EXPLAIN on rejected revised queries. It writes only its separate audit.json.
