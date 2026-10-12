# Where the SQL model fails: saved development evidence

Date: 2026-10-11. Analysis-only review of `recovery-live001`, with a separately
identified partial snapshot of `recovery-grounded002`. No model API calls were
made. Existing predictions, scores, prompts and the active run were not changed.
No accuracy claim is made for revised prompts before their experiment is complete.

## Measured failure distribution

Each arm answers the same 31 development questions. These are 93 outputs, not 93
independent questions.

| Arm | Correct | Executes but wrong result | Rejected | Rejected yet compiles in SQLite |
|---|---:|---:|---:|---:|
| C0: 1024 / low | 7 | 14 | 10 | 1 |
| C1: 3072 / low | 5 | 18 | 8 | 2 |
| C2: 3072 / medium | 9 | 13 | 9 | 6 |
| Total outputs | 21 | 45 | 27 | 9 |

Original generation completed for every output, with no generation failures.
Of the 66 outputs that executed successfully, 45 returned incorrect results. Thus
SQL semantics, not just syntax, is a central failure mode. On C2, 13 of 22 executable
queries were wrong. Increasing output allowance alone did not improve accuracy.

Every rejected SQL string was checked using read-only SQLite `EXPLAIN`. Nine
compile, while eighteen fail compilation. Compilation is **not** proof of correct
results or of compliance with the project security policy. These checks do not
change any historical scores or establish how many additional answers would pass
with a corrected validator.

## Confirmed recurring defects

### 1. Guessing values and representations

C2 `local167` filters `gender = 'Female'`; the actual table contains only `F` and
`M` (366 and 12,152 rows respectively). The result is empty. C2 `local171` uses
`gender = 'Male'` and `state = 'Louisiana'`; states use abbreviations, including
`LA`, which occurs in 759 term rows. This is a failure to connect natural-language
concepts to stored literals, compounded by the old prompt's lack of value evidence.

Airline queries `local009`/`local010` invent `X()` and `Y()` coordinate functions.
SQLite reports these functions missing. The coordinates are actually stored as
text, and the city column contains language-keyed JSON. In `local009`, extracting
`'$'` and comparing the whole JSON object with `'Abakan'` is an additional error;
the English city name needs the corresponding field. Correct trigonometry alone
would not fix that query.

### 2. Incorrect ordering of SQL operations

C2 `local068` filters the final relation to 2022/2023 in the same SELECT that
computes `LAG`. The 2021 baseline is removed before the window is evaluated, so
2022 growth values are NULL. The saved six-row result directly shows this. The
query also chooses month names and rounds to two decimals; these are additional
output-contract choices, not the primary baseline defect.

The overtake queries also filter away rows before computing previous-lap values.
For example, C2 `local356` removes lap 1 and pit-stop rows before `LAG`, so the
previous remaining row need not be the immediately preceding race lap.

### 3. Wrong unit of counting or aggregation

C2 `local309` sums `driver_standings.points` over races. These are cumulative
snapshots: one inspected driver's 2020 sequence is 12, 37, 63, 88, 107, 132, 157,
164. Summing snapshots repeatedly counts earlier points. The analogous constructor
calculation has the same problem. Its final inner join also yields only 67 years,
starting in 1958, whereas the inspected reference variant includes 75 years from
1950. Missing coverage and wrong measures are separate issues.

C2 `local169` uses `COUNT(DISTINCT c.id_bioguide)` from the cohort side of a LEFT
JOIN. It therefore counts members even when no retained term matches. The same
SQL uses invalid date modifiers (`'12-31'`); direct SQLite checks return NULL.
Although the validator blocks this query first, its visible logical defects remain.

C2 `local335` groups points by driver, constructor and year before selecting the
lowest-scoring driver. That splits a driver's season if they switch constructors,
instead of first calculating total points for the driver-season. Such switches
exist in the database. The saved counts differ from the reference; this is not
merely a different tie ordering.

C2 `local286` joins order items to reviews before aggregating item prices. The
database contains orders with multiple reviews, so this join can repeat line-item
amounts. This is a confirmed multiplicity risk in the calculation; its contribution
to every reported seller mismatch has not been separately quantified.

### 4. Omitting the hard part of the requested algorithm

C2 `local277` is asked for a forecast using 36 months of history, seasonality
adjustments and weighted regression. It instead sums observed 2018 sales and
averages the totals. There is no regression or seasonality calculation. The saved
answer is 477.0 versus approximately 277.3343 in the inspected reference variant.
This is not a formatting or rounding issue: it calculates a different measure.

C2 `local273` is asked to calculate FIFO allocation using purchase order, available
inventory and overlap between cumulative quantity ranges. Its SQL simply joins
existing picking records to order lines and divides sums. It does not implement
the requested allocation. An average of individual percentages and a ratio of
sums are also different quantities. The reference's product-name representation
is unusual (numeric-looking values), so output representation needs separate
review rather than being assumed to be solely a model error.

C2 `local356` reduces overtakes to whether a driver's position improved or worsened,
counting each qualifying lap as one event. That cannot distinguish gaining one
place from gaining several or track pairwise passes. It also does not implement
all requested retirement and pit-entry/exit exclusions. The saved output has one
driver; the inspected reference variant has 76.

### 5. Losing track of columns and associated rows

C2 `local170` references `vs.gender`, but its `valid_states` CTE selects only
`state_abbr`. SQLite confirms the missing column. Separately, the query uses
`MIN(term_start)` and `MIN(state)` independently; the alphabetically smallest
state is not necessarily the state represented in the first term. It also uses an
invalid date modifier. One query can contain several defects, so category counts
must not be added as though they were disjoint.

Other arms contain missing aliases/columns, an ambiguous `race_id`, invalid
aggregate/window usage and Markdown fences. These are genuine compilation or
output-format failures, distinct from the executable wrong-result cases.

## What belongs to the pipeline rather than the model

C2 has six rejected queries that SQLite can compile:

| Example | Recorded rejection | Interpretation |
|---|---|---|
| local072 | unsupported_statement | Parser/statement-classification problem; SQLite accepts the SELECT/CTEs. |
| local169 | unknown_table | Validator mistakes the CTE `first_terms` for a missing base table. |
| local270 | unsupported_statement | SQLite accepts the recursive CTE query; classifier rejects it. |
| local279 | comments_not_allowed | Explicit project policy, not SQLite syntax invalidity. |
| local336 | unsupported_statement | Statement classification differs from SQLite. |
| local344 | comments_not_allowed | Explicit project policy, not SQLite syntax invalidity. |

The comment cases require a deliberate policy/normalization decision, not bypassing
security. The CTE false rejection and statement classification require validator
review. Fixing these does not guarantee their SQL will return correct answers:
`local169` and `local336`, for example, also have visible semantic defects.
No rejected query was executed outside the established scoring sandbox during
this analysis; only its compilation was tested.

## C2 failure inventory and confidence

This covers all 22 C2 failures. “Confirmed” means supported by the saved SQL,
reported output, a compilation check, or a bounded database fact check. It does
not mean an isolated correction has been proved sufficient to make the answer pass.

| Example | Main observed finding | Confidence / limit |
|---|---|---|
| local009 | Missing Y/X functions; whole-JSON city comparison | Confirmed |
| local010 | Missing Y/X functions | Confirmed; distance/bucket semantics not fully audited |
| local068 | Baseline filtered before LAG | Confirmed |
| local072 | SQLite-compilable query rejected by statement classifier | Confirmed pipeline blocker; result correctness unmeasured |
| local167 | Wrong gender literal produces no rows | Confirmed |
| local168 | Job-title substring filter and salary averaging grain | Suspected; several accepted reference variants, exact cause unresolved |
| local169 | CTE false rejection; cohort-side counting; invalid date modifier | Confirmed multiple defects |
| local170 | Missing CTE column; independent minima; invalid date modifier | Confirmed multiple defects |
| local171 | Wrong gender and state literals | Confirmed |
| local270 | SQLite-compilable query rejected by statement classifier | Confirmed pipeline blocker; hierarchy semantics unmeasured |
| local272 | Product-level aggregation discards order-line sequencing | Confirmed omission; isolated cause of allocation mismatch unproved |
| local273 | Required FIFO/overlap computation omitted | Confirmed; output representation also needs review |
| local277 | Historical sales substituted for a regression forecast | Confirmed |
| local279 | SQL comments rejected by project policy | Confirmed blocker; inventory recurrence not fully audited |
| local286 | Join can multiply item values; metric interpretation differs | Multiplicity confirmed; full attribution unresolved |
| local309 | Cumulative standings summed; incomplete year coverage | Confirmed |
| local335 | Driver-season points split by constructor | Confirmed grain discrepancy; switches exist |
| local336 | Classifier rejection; lap filter before LAG; oversimplified event counts | Confirmed multiple defects |
| local344 | Comments rejected; incomplete event classification suspected | Policy confirmed; full semantic attribution unresolved |
| local354 | Output representation and row coverage differ among variants | Exact cause unresolved; eight accepted variants prevent a simple shape-only diagnosis |
| local355 | Gap-level calculation using drives; no annual total-missed filter | Observed construction; complete mismatch attribution unresolved |
| local356 | Position-change proxy and incomplete exclusions | Confirmed |

## Early signal from revised prompts — not final accuracy

At the captured snapshot, `recovery-grounded002` has 15 recorded outcomes: 11
complete generations and four failures, all in C0 with `finish_reason=length` and
an output cap of 1,024 tokens. They are `local009`, `local010`, `local068`, and
`local072`. Their recorded reasoning-token counts are 689, 719, 602 and 573.
Thus a substantial portion of the completion budget is consumed before the final
SQL is finished. This is an output-budget interaction with the revised task, not
a network error and not evidence of a wrong completed result.

Partial SQL shows the airline examples using English JSON fields and parsing
coordinate strings instead of X/Y, but the outputs were truncated. That is evidence
of a changed approach, not proof of a correct final answer. The larger-budget C1/C2
arms remain necessary to distinguish correctness from completion limits. Do not
change the frozen active experiment midway through generation.

## Priorities supported by this analysis

1. Separate validator false rejections and comment-policy friction from model
   correctness. Keep corrections versioned and preserve existing scores.
2. Measure whether value grounding and runtime context in the new prompts reduce
   wrong literals and unavailable-function errors.
3. Track completion limits separately from accuracy, especially for C0. Fitting
   within the input/minute quota does not guarantee sufficient completion tokens.
4. Add general planning checks for the requested measure, entity grain, joins,
   chronological order and required algorithm. A fluent long query is not evidence
   that it implements the question.
5. Consider bounded execution feedback/repair only after the validator is reliable.
   Compilation catches missing columns/functions; it cannot detect a plausible but
   incorrect forecast, denominator or event definition. Any model-based repair is
   another API request for the user to run.

Artifacts: `artifacts/development/recovery-error-analysis-2026-10-11/audit.json`
records source checksums, per-output SQL hashes, all compilation diagnostics and
the exact prefix hash/length of the partial new-run snapshot. This diagnostic
material is analysis-only and must not be fed into generation as reference answers.
