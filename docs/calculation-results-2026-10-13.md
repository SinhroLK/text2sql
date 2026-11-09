# Calculation-planning experiment: completed analysis

`recovery-calculation004` scores **12/31 (38.7%)**, versus grounded002 C2's
**13/31 (41.9%)** under the common validator-v2 policy. The candidate is **not
promoted**. Retain grounded-sqlite-v2 C2 (3072 completion tokens, medium reasoning).
This is a negative development result for replacing the detailed checklist with
a shorter internal calculation-plan-and-trace block, not evidence that every form
of semantic planning is ineffective.

## Paired result and preregistered decision

| Metric | Grounded002 C2, validator v2 | Calculation004 C2 |
|---|---:|---:|
| Correct / all questions | 13/31 | 12/31 |
| Nonempty correct | 12 | 11 |
| Executable, complete results | 25 | 22 |
| Executable wrong | 12 | 10 |
| Validation rejections | 3 | 6 |
| Execution timeouts | 1 | 2 |
| Generation failures | 2 truncations | 1 exhausted rate-limit retry |

Both arms include the accepted empty-result case local275. Fewer executable wrong
answers does not establish better semantics when more answers fail to execute.

There is one gain, **local009**, and two losses, **local072/local171**: net **-1**.
The predeclared gate requires net >=3, losses <=2, and positive net change in at
least two database families. Only the loss-count condition passes. The sole
improved family is Airlines; city_legislation loses two answers. The generated
report's `selected_arm: C2` identifies the only arm in this run and **does not mean
that calculation004 passes promotion over grounded002**.

| Database | Before | After |
|---|---:|---:|
| Airlines | 0/2 | 1/2 |
| city_legislation | 7/10 | 5/10 |
| oracle_sql | 3/8 | 3/8 |
| f1 | 2/9 | 2/9 |
| electronic_sales | 0/1 | 0/1 |
| music | 1/1 | 1/1 |

## What changed in the answers

- **local009 gain:** the query consistently exposes/uses `city_en` across CTEs
  and computes the route distance. It returns 3484.150460009601 and matches the
  pinned comparator. The preceding grounded answer had a missing outer column.
- **local072 loss:** the query applies ROW_NUMBER to insertion rows without first
  selecting distinct dates. The gaps-and-islands calculation therefore has the
  wrong grain for consecutive days. It also returns country name `iran` instead
  of the previous code `ir`. Saved output is January 3–8, proportion 0; the previous
  accepted output is January 20–22, proportion 0.2. The construction differences
  are confirmed; no isolated corrected prediction was scored to apportion causes.
- **local171 loss:** the query computes first_start only from Louisiana terms,
  then checks service against terms in any state. The previous accepted query
  computes the first term over all terms and restricts active service to Louisiana.
  Counts fall by one at elapsed years 31–36. This is a confirmed population/time
  definition change, not a gender/state-code failure. The wording's interpretation
  still needs to be distinguished from the benchmark's accepted convention.
- **local272:** now attempts individual line ranges and overlap allocation, but
  references `s.id` when its CTE exposes `inv_id`. Thus an apparent algorithmic
  improvement is blocked by a genuine scope error; semantic correctness unmeasured.
- **local286:** an outer aggregate references `r.review_score`, although only the
  derived alias `dr` is in scope. SQLite confirms the missing column.
- **local010:** now completes generation, but malformed CAST expressions contain
  `AS REAL AS dep_lon` without closing CAST. SQLite reports syntax error near AS.
- **local354:** now executes, replacing an earlier unsupported DISTINCT-window
  construction, but still mismatches. Valid SQL has not solved its result semantics.
- **local169/local170:** both reach the unchanged 60-second execution timeout.
  local170 previously failed validation. A timeout provides no correctness result.

All six rejected candidates were checked with read-only EXPLAIN. local010/272/286
are genuine SQL errors. local279/344/355 compile but violate the deliberate
no-comments policy. They are **not newly discovered validator parser bugs**.
Comments were not stripped, predictions were not repaired, and these queries were
not executed or scored outside the security policy.

## Generation and tokens

All 30 returned model answers have `finish_reason=stop`; none is truncated.
The prior two truncated questions, local010/local344, now produce complete answers
but fail syntax/comment validation, so completion gains did not yield correctness.

local309 exhausted the allowed two attempts: journal reservations 24 and 25 both
record `known_rejected` / `rate_limit`; the saved generation ends as `retry_limit`.
There is no SQL to assess. This is an operational failure, not a demonstrated SQL
error or truncation. It remains in the denominator. Its preceding grounded answer
was already wrong, so it does not explain either observed correct-to-wrong loss.
There were 32 reserved API attempts for 31 questions, not 31 successful answers.
Do not reopen the completed run to silently replace this failure.

| Recorded response usage | Before | After |
|---|---:|---:|
| Input tokens | 74,372 | 63,654 |
| Completion tokens | 56,541 | 50,183 |
| Reasoning tokens, included in completion | 47,331 | 39,074 |

These are reported response totals, not billing receipts or a clean per-question
causal comparison: the failed candidate question reports zero tokens and no model
response. The shorter instruction block and generation variability also differ.
The observations support less recorded reasoning and no observed truncation in
this run, but do not establish that planning intrinsically improves efficiency.

## Verification, limits and next action

The analysis verifies frozen manifests/resources, generation against hash-chained
journal outcomes, score checkpoint hashes/coverage, and the completed validator-v2
control audit. Model, seed, temperature, completion/reasoning settings, gold
inventory, comparator and execution limits match. Exact prompts equal the
predeclared instruction transformation; context is unchanged. All 31 outcomes count.

The control audit reexecuted newly accepted old predictions and retained unaffected
historical outcomes. It is not a simultaneous fresh control run. Fingerprints vary
within and between experiments; equal model ID and seed do not establish an
unchanged backend or deterministic outputs. With 31 reused development questions,
this cannot support a final accuracy or statistically confirmed causal claim.

Next: retain grounded v2 C2 and close this experiment as negative. Do not run a
larger allowance or relax comment/security rules to rescue this completed result.
The next useful offline roadmap work is SEM-002's explicit, checkable semantic
plan contract, especially grain, population/first-term versus active-term filters,
and projected-column consistency. The internal prompt instruction tested here is
not that typed implementation. OPS-001 also retains the rate-limit exhaustion
finding; changes to pacing/retry policy would need a separate run identity.
No new model run is prepared or requested by this analysis. An untouched,
family-audited holdout is still required before any new final accuracy claim.

Machine-readable evidence and the reproduction script are under
`artifacts/development/recovery-calculation-analysis-2026-10-13/`.
To reproduce without altering completed runs, use a fresh output path:

```bash
PYTHONPATH=src .venv/bin/python \
  artifacts/development/recovery-calculation-analysis-2026-10-13/reproduce-audit.py \
  --output /tmp/calculation-audit-new.json
```

No model API calls, prediction/reference edits, rescoring, commits or pushes were
performed during this analysis. Source and preregistration files remain frozen.
