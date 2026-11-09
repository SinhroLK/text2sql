# Calculation-plan experiment v3: preregistration

This is a reused-development engineering experiment, not a final accuracy test.
The sole generation intervention replaces the seven-item internal checklist in
`grounded-sqlite-v2` with a shorter calculation-plan-and-trace instruction block.
Hypothesis: explicitly mapping a measure to its population, inputs, grain and
calculation dependencies helps the model implement the requested calculation.
The shorter block also leaves more input-quota headroom. These two properties of
the instruction replacement cannot be separated by this experiment.

All 31 questions use C2: `openai/gpt-oss-120b`, 3072 completion tokens, medium
reasoning, temperature 0, seed 42. Exact schema, values, documents, question and
runtime context are copied from completed `recovery-grounded002`; no resampling,
retrieval, examples, gold-derived per-question hints, repair or arm routing.
Output stays SQL only. No external plan output or extra model call is requested.
The two earlier truncations consumed 3070 reasoning tokens each; concise internal
planning is a hypothesis, not a guarantee of fewer reasoning tokens or truncations.

Use `safe001-sqlite-v2` for both candidate scoring and the saved C2 comparison.
The separately versioned validator audit scores newly accepted saved predictions
without modifying them and retains other historical outcomes. This is a historical
paired control, not a contemporaneous randomized comparison: model backend drift
and generation variability remain possible confounders. Do not attribute changes
from the old validator to the prompt intervention.

Primary outcome: correct/31 with all generation, validation and execution failures
in the denominator. Report paired gains/losses against saved grounded002 C2 under
v2, database/family breakdown, nonempty correct, executable wrong, truncations,
reasoning/output/input tokens, and provider failures. Keep the same comparator,
reference variants, 60-second execution limit and result/resource limits.

Development promotion requires at least three net new correct answers, no more
than two losses, and net improvement in at least two database families. Otherwise
retain v2 C2 as the development candidate; do not select arms per question.
No claim of statistical confirmation is planned from 31 reused cases. Complete
all generation before scoring. Interrupted/failed requests remain accounted for;
only the existing journal's explicit-429 retry policy applies. Do not silently
rerun failures or change the prompt/budget after observing outcomes.

Preparation checks 3072 and 4096 against the locally recorded 7200-token/minute
safe scenario (ordinary-text count + 256 template allowance + 15% margin).
Retain 3072 regardless of whether 4096 fits, to isolate the instruction change.
Current account quota/billing is not inferred from these local estimates. The
user runs generation on their Free organization; no billing, upgrades or paid
fallbacks are enabled or proposed.

Before any new final accuracy claim, reserve a new evaluation set from data that
has never been used in prompts, diagnostics, references, selection or tuning.
Both the existing 31 development and reviewed 104 test questions are excluded.
Audit database/schema families against all development, reviewed-test and
retrieval families and exclude overlaps, including near-duplicate schemas.
Prefer newly collected questions on new database families with independently
adjudicated executable references and a predeclared ambiguity/exclusion policy.
An independent custodian should lock question/reference hashes before access.
Freeze the candidate, comparator, security/resource policy, failure accounting,
sample size and paired statistical analysis before one-time evaluation. No such
untouched set is currently reserved: until then, report development results only.
