**Project review — 2 October 2026**

The project has a useful, reproducible research foundation, but its generation methods still leave substantial task interpretation to one short model completion. The largest remaining accuracy problem is semantic correctness: selecting the right entities, joins, grouping level, denominators, and output structure. There are also concrete implementation problems worth fixing before another paid experiment. Improving these is a plausible route to stronger results; the present evidence cannot establish a future accuracy percentage.

This review covers the active generation, schema, retrieval, optimization, refinement, evaluation, security, finalization, and application paths; their configurations and tests; and the completed development/final artifacts. Legacy notebooks and the root application are distinguished from the evaluated pipeline. It is an accuracy-focused engineering and methodology review, not an exhaustive security audit of every dependency or a manual adjudication of every benchmark answer.

**Evidence and verification**

The completed final run is `artifacts/final/run002-v2`, using freeze `run001-final-evaluation-v2.1`. Earlier v1 and partial pre-v2.1 artifacts are not the current final result.

| Arm | Method | Correct / 104 | Accuracy | Executable / 104 |
| --- | --- | ---: | ---: | ---: |
| B0 | Question only | 4 | 3.85% | 5 |
| B1 | Complete compact schema | 25 | 24.04% | 80 |
| B2 | Complete M-Schema with sample values | 28 | 26.92% | 83 |
| B4 | M-Schema plus retrieved examples | 30 | 28.85% | 81 |
| B6 | Pruned linked M-Schema | 10 | 9.62% | 83 |
| B6R | Complete compact schema plus linked detail | 34 | 32.69% | 83 |

Source: [final arm summary](../artifacts/final/run002-v2/analysis/final-arm-summary.csv). The [review audit](../artifacts/reviews/project-review-2026-10-02/audit.json) contains derived per-example diagnostics and report hashes.

B6R's 70 failures consist of **49 executable result mismatches and 21 execution errors**. Of those 21 errors, ten are unknown columns, one is an ambiguous column, six are incomplete SQL, three are other syntax errors, and one is an invented function. Eight result mismatches return no rows; sixteen return a different number of rows from the stored reference variant. These are diagnostic observations, not a complete semantic classification.

Validation performed during this review:

- All **216 offline tests passed** using `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`.
- `pip check`, the v2 freeze verification, and the initial `git diff --check` passed.
- Independently applied the pinned upstream `compare_pandas_table` function to the saved generated rows and pandas-loaded official gold CSV variants. **All 624 saved arm/example outcomes agreed.** Only that function was loaded through Python AST extraction, avoiding upstream module-level cloud/logging side effects. This was a comparator and CSV-loading cross-check, not a fresh execution of all SQL.
- Reproduced acceptance of a truncated provider response with an offline transport fixture.
- Inspected schema signatures for all 30 SQLite databases and compared them with the Spider 1 training schemas.
- No paid generation was run, no existing predictions or scores were edited, and no benchmark-specific corrective SQL was implemented.

The evaluator is not demonstrably suppressing a large amount of correct performance. Its column-vector semantics intentionally match the official benchmark, including permissive column matching and NULL normalization. Preserve that metric for comparability; use any stricter row-preserving check as an additional diagnostic.

**1. High priority: completion truncation is accepted as successful generation**

Evidence: [provider adapter](../src/text2sql/providers/groq.py), particularly response parsing around line 230; [B6R configuration](../configs/final/final002-b6r-test.toml), lines 9–11; and [final generation](../src/text2sql/finalization/runner_v2.py), around line 375.

All final arms use `max_tokens = 1024` and `reasoning_effort = "low"`. Seven B6R predictions reach exactly 1,024 output tokens. All seven fail execution, and six fail with `incomplete input`: `local060`, `local063`, `local258`, `local259`, `local299`, and `local302`. The seventh, `local073`, has a syntax error and also ends mid-query. The adapter ignores `choices[].finish_reason`, then the final runner accepts any nonempty selected SQL. An offline response containing `finish_reason="length"` and `SELECT column_name FROM` was accepted, with no completion status retained.

For a new experiment, record finish reason, completion usage details when available, and backend fingerprint. Distinguish a complete model answer from a partial output. Test a larger completion budget, initially 4,096, with a separately controlled reasoning-effort comparison. If 4,096 still truncates, measure that and consider 8,192. These are proposed trial settings, not validated optima. Groq now documents `max_completion_tokens` as the replacement for deprecated `max_tokens`, and warns that 1,024 can be too low for complex reasoning. [Groq API reference](https://console.groq.com/docs/api-reference), [reasoning documentation](https://console.groq.com/docs/reasoning).

Define any budget escalation or regeneration rule before running it, retain attempts, and cap its cost. A token-limited model response is a generation failure, distinct from a rejected transport request. Merely making these seven queries complete does not establish that they will be correct.

Check each proposed request against the account's actual token limits first. The recorded experiments encountered an 8,000-token/minute tier; larger prompts plus a larger reserved completion can require a different quota tier or a smaller request. Waiting between calls does not solve an individually oversized request. Share token-aware scheduling with the v2 retry policy and include waits in cost/latency reporting.

**2. High priority: required external knowledge is loaded as metadata but never reaches the model**

Evidence: [dataset loader](../src/text2sql/datasets/spider2_lite.py), around line 260; [pipeline interface](../src/text2sql/pipeline.py), around line 50; [final runner](../src/text2sql/finalization/runner_v2.py), around line 375.

The loader preserves `external_knowledge`, but the generation interface takes the question, schema, and demonstrations without this document content. This affects seven development examples and six final examples. Four of the six final examples are currently incorrect under B6R. The omission is therefore real but does not explain all 70 failures.

For example, `local003` explicitly requests the RFM definition document. The model uses three buckets and returns numeric segment codes. The supplied `RFM.md` defines five-level scores and named segments with specific criteria. The answer cannot reliably follow an unseen definition. Spider 2 explicitly describes these files as resources required to answer the associated questions. [Official dataset description](https://github.com/xlang-ai/Spider2/tree/main/spider2-lite).

Add a checked, bounded document loader and a distinct prompt section for the supplied document. Include document hashes in run identity. Apply the same document-availability policy to comparable arms. These supplied task documents are different from gold SQL and gold result files, which must remain outside generation.

**3. Highest priority for substantial gains: explicitly plan the meaning of the query**

Evidence: [prompt builders](../src/text2sql/prompting/builder.py), [pipeline](../src/text2sql/pipeline.py), and the B6R prediction/report pairs.

The prompts mostly request SQL directly. They do not produce an inspectable representation of the requested entity, unit of observation, grouping levels, filters, qualifying population, output columns, or tie rules. Examples from the observed failures:

| Example | Observed failure | General capability needed |
| --- | --- | --- |
| `local029` | Counts by order-level `customer_id`, then displays `customer_unique_id`; the returned leaders each have one order | Resolve entity identity before grouping |
| `local008` | Requires a row to simultaneously hold all four batting maxima, producing no result | Separate the requested rankings and distinguish season rows from career totals |
| `local021` | Averages qualifying match scores, rather than total runs for the qualifying strikers | Separate eligibility filtering from final aggregation |
| `local024` | Averages matches represented by batting rows, omitting other matches played | Define the denominator and preserve zero-contribution cases |
| `local003` | Invents RFM grouping instead of using its supplied definition | Bind calculations to task documentation |
| `local025` | Returns one aggregate row while the reference contains match-level details and an aggregate | Make the requested output structure explicit; flag ambiguity during analysis |

Implement a compact structured plan containing output grain, entity keys, tables and joins, filters, intermediate aggregates, final calculation, ordering/ties, and expected output shape. Generate SQL from that plan and check it against the original question. A plan is useful only if its fields affect generation or validation; adding generic “think carefully” text is unlikely to fix this systematically.

Create a development error corpus before optimizing the planner. Distinguish confirmed failures from ambiguous natural-language/reference cases. Annotate development questions independently of model output where possible. Store human diagnostic labels outside inference inputs. The roadmap's deferred SEM-001/SEM-002 work addresses this gap more directly than further checkpoint or reporting features.

**4. High priority: schema linking overweights irrelevant numeric values and lacks real join information**

Evidence: [linker](../src/text2sql/schema/linker.py), especially `_value_score` at line 233 and fallback at line 416; [schema inspector](../src/text2sql/schema/inspector.py).

The value matcher grants a score of 15 when a sampled value such as `1` occurs anywhere in the question, including a date. It can therefore rank Boolean flags and small counters above relevant domain concepts. In `local002`, the linked context highlights `leads_closed`, `order_payments`, and `order_reviews` for a toy-sales forecasting question. The audit records Boolean/counter sample matches as the reason. B6R retains every identifier, but misplaced priority context can still distract it; its answer here is `SELECT * FROM leads_closed LIMIT 0`.

The full-schema fallback only activates when no table passes the score threshold. It does not catch a confidently wrong selection; it activated zero times for final B6R. B6 loses all 18 examples that B2 gets right and B6 gets wrong, with no opposite wins. Aggressive pruning is demonstrably harmful in this experiment.

Also, **20 of the 30 actual SQLite databases have no declared foreign keys**. These include E_commerce, Brazilian_E_Commerce, IPL, and bank_sales_trading. A shortest-path algorithm over declared foreign keys cannot discover their undeclared relationships. Current fixture tests give it explicit relationships that often do not exist in the benchmark metadata.

Keep the complete compact schema as a stable starting point. Suppress or strongly downweight isolated Boolean/small-numeric value matches unless the question grounds them to a relevant column. Audit join-key coverage and table recall on annotated development cases. Add relationship metadata from available documentation or validated inference using names, types, uniqueness, and value overlap; do not turn every similarly named column into a trusted join. Test full compact schema plus selective useful values against the duplicated compact-plus-detail layout.

**5. High priority: value sampling supplies examples, but does not ground the question's actual predicates**

Evidence: [M-Schema sampler](../src/text2sql/schema/mschema.py), around lines 93–145.

The sampler scans the first 24 non-NULL rows in primary-key/rowid order and keeps up to three unique short values. These are deterministic, but not necessarily representative or relevant to the user's question. It supplies no category dictionary, range summary, or targeted lookup. The model consequently guesses literal spellings and date representations. For example, `local019` assumes an exact title value `'NXT'` and returns no rows; this is a candidate for value-grounding investigation, not proof of the only underlying error.

Add bounded, question-directed value lookup, small categorical dictionaries where appropriate, date-format/range summaries, and column descriptions. Preserve the existing restrictions on sensitive values. Share this context between planning and SQL generation, and measure its added token cost. Do not enlarge every column's sample list indiscriminately.

**6. High priority: the final generation path does not use the validator or repair loop**

Evidence: [pipeline result construction](../src/text2sql/pipeline.py) records `validation_status="not_implemented"`; [refinement](../src/text2sql/refinement/core.py) is a separate path.

Final B6R is a single generation followed by evaluation. It does not repair the ten unknown-column failures, the ambiguous column, or the nonexistent `sorted_letters` function before submitting a result. Integrate one bounded validation/execution repair pass into a new method, with useful sanitized diagnostics such as the invalid identifier, its scope, and legal alternatives. The current B7 repair prompt passes generic issue codes, leaving out much of this actionable context.

For semantic correctness, an executable query still needs checks against the plan: required output fields, grouping keys, denominator, join multiplicity, ranking scope, and filter values. The existing B7 starts repair only when its best initial candidate does not execute successfully, so it cannot address most observed B6R failures.

Use a consistent, documented execution policy. The evaluator has a 60-second query timeout and unbounded result fetching; B7 uses a 10-second sandbox with row/byte caps. A resource-limit rejection must remain distinguishable from incorrect SQL. Preflight snapshot and result sizes before transferring the sandbox policy to all test databases.

**7. Medium priority: retrieval measures wording similarity, with little support for complex SQL structure**

Evidence: [TF-IDF selector](../src/text2sql/retrieval/selector.py), around line 75; [few-shot prompt](../src/text2sql/prompting/builder.py), around line 82.

B4 retrieves three Spider 1 training examples using question-token cosine similarity. It does not explicitly retrieve matching aggregation stages, window patterns, cohort filtering, or output grain. Demonstrations contain the source question and SQL but no compact source schema explanation. On this final run B4 improves B2 by two examples, without a significant adjusted difference.

After fixing baseline context and generation, compare hybrid lexical/semantic retrieval with SQL-structure diversity and a prompt-token budget. Target patterns such as “qualify entities first, aggregate their complete histories second” and “aggregate before joining.” Training demonstrations need verified answers and a clear provenance policy. Do not retrieve the reviewed test questions or their corrective SQL. A semantic retriever is a hypothesis to test, not an established improvement over this baseline.

**8. High priority for claims: database-name checks miss schema-family overlap**

Evidence: [retrieval firewall](../src/text2sql/retrieval/index.py), around lines 254–256 and 332–336.

The firewall excludes matching database names and normalized questions, but names alone do not establish unseen schemas. Comparing normalized table/column name pairs found:

| Spider 2 database | Spider 1 training database | Schema signature overlap |
| --- | --- | --- |
| Baseball | baseball_1 | Identical: 352 pairs |
| Pagila | sakila_1 | Identical: 89 pairs |
| sqlite-sakila | sakila_1 | Identical: 89 pairs |
| music | chinook_1 | Identical: 64 pairs |

The verified retrieval index contains 82 baseball_1, 82 sakila_1, and 84 chinook_1 examples. This is not merely a theoretical possibility: final B4 retrieves baseball_1 for both Baseball questions, and retrieves sakila_1 for both Pagila questions and several sqlite-sakila questions. There are additional partial overlaps, including f1/formula_1 and EU_soccer/soccer_1. Within the custom development/test split, electronic_sales and E_commerce share approximately 65.8% Jaccard overlap of normalized table/column pairs.

These checks establish schema overlap, **not identical database contents or duplicated target answers**. They do not explain low accuracy. They do weaken broad “unseen database” or “zero overlap” claims and can confound retrieval comparisons. Introduce a schema-family/provenance audit, then either exclude matching families or explicitly report a setting that allows them. Group related databases when making a future development/holdout split. Do not quietly remove difficult cases or revise old denominators.

**9. Medium priority: the existing DSPy and B7 results do not support scaling them unchanged**

Evidence: [DSPy config](../configs/optimization/dspy001-b5.toml), [B5 module](../src/text2sql/optimization/b5.py), and [B7 results](b7-results.md).

B5 tunes an instruction over a frozen B4 context, using three instruction candidates and five trials. Its ten-example validation partition contains nine f1 questions and one electronic_sales question. It selected the default instruction and scored 4/31 in development. This is a small, narrow optimization trial; it neither establishes that DSPy is ineffective nor provides a strong basis for investing in a larger run before fixing task context and representation.

B7 has 26 generation/provider failures among its recorded candidates and scores 5/31. Its initial-candidate oracle also scores 5/31: the ranker picked all available correct candidates in this particular degraded run. There is little evidence that changing the ranker alone would help. The final B1/B2/B4/B6R outputs have a combined oracle of **44/104 (42.31%)**. That number uses gold only as a retrospective diagnostic and is not an achievable deployed selector score.

Improve single-candidate quality first. Then test two or three deliberately different plans, deduplicate candidates, and use execution plus semantic checks for selection. Retry known rejected provider calls using the v2 operational policy. Resume behavior and quota handling should be shared across providers/runners instead of implemented differently in B5, B7, and final generation.

**10. Medium priority: experimental comparisons need cleaner controls and broader diagnostics**

B6R adds complete compact context, a different read-only instruction, explicit SQLite rules, and an anti-placeholder instruction compared with earlier prompts. Its improvement cannot be attributed exclusively to one schema-linking repair. Future ablations should share a common task/SQLite instruction and vary one component at a time.

The final results are stronger than the old 6/31 development result, but B6R exceeds B1 by nine examples, or 8.65 percentage points, with **Holm-adjusted p = 0.1471**. The current analysis therefore does not establish superiority over B1 at the declared familywise threshold. Its reported question-bootstrap accuracy interval is 24.04–41.35%.

Questions are clustered within 24 test databases. Keep the existing frozen analysis, and add per-database results and a database/family-cluster bootstrap as a separately labeled sensitivity analysis for new claims. Current B6R database-macro accuracy is 39.13%, versus question-micro accuracy of 32.69%; neither should replace the other opportunistically. IPL scores 1/11 and bank_sales_trading 2/15, together contributing 23 failures. These groups deserve diagnostic attention, but not test-specific prompt exceptions.

For future runs, report accuracy, execution rate, truncation rate, failure category, nonempty/empty correctness, context size, model usage, provider retries, repair success and regressions, cost, and total wall-clock latency including quota waits. The final v2 journals contain 253 retry-wait events, while generation summaries record zero persisted provider failures. Do not attribute the latest run's wrong answers to the old v1 transport-failure problem.

**11. Medium priority: passing infrastructure tests is not the same as demonstrating semantic quality**

Retain the useful coverage, manifests, resource checks, safety tests, and resumability. Add meaningful tests for the actual gaps: a truncated nonempty provider response; document propagation; schema linking with misleading small numbers and absent foreign keys; a customer with multiple customer IDs; one-to-many join fanout; zero-contribution rows in averages; and multi-stage eligibility/aggregation. These should use independent synthetic data or development cases with verified expectations.

Keep semantic regression data separate from the final holdout. Add upstream-comparator parity cases for mixed-type CSV columns, numeric-looking strings, and NULL values: the local loader coerces each cell individually, whereas pandas infers types by column. That implementation difference caused **no score changes in the checked final artifacts**, so it is a robustness gap rather than an explanation for this result.

**12. Lower priority for benchmark accuracy, high priority before a demo: application and security claims are disconnected from final generation**

The root [app.py](../app.py) is a preserved MySQL/Gradio prototype using a different model and direct SQL execution, with its validator call commented out. The root `schema.json` is empty. The [app README](../app/README.md) correctly says the new application is not implemented. It is not an entry point for demonstrating evaluated B6R behavior.

Build the demo around one shared new-pipeline configuration, validator, and bounded executor. Display the executed SQL, result, and a clear failure/abstention state. Move or clearly label obsolete root entry points and update stale documentation claiming the test set is still sealed. Preserve original experiment records.

The security evaluation is valuable evidence about 16 fixed synthetic cases, with six benign and ten adversarial cases. It uses supplied candidate SQL, not model-generated attack responses, so 0/10 attacks released is not a general prompt-injection robustness estimate. Expand it with paraphrases, data/document injections, legitimate questions near keyword filters, and eventual end-to-end model tests. Measure security-induced accuracy loss separately. Do not weaken guardrails merely to inflate executable rate.

`requirements.lock` pins five direct dependencies but is not a complete transitive lock. Record the full resolved environment, Python/SQLite versions, and provider fingerprint for future reproducibility. This is useful maintenance work after the accuracy fixes, not the likely source of the present low scores.

**Recommended implementation and experiment order**

| Stage | Work | Gate before spending more |
| --- | --- | --- |
| 1 | Preserve current run; designate reused data and reserve a fresh, family-audited holdout; build a development error table | Written split, metric, budget, and attempt-selection policy |
| 2 | Record completion status; correct document propagation; establish compact full-schema baseline and completion budget | Offline regression checks pass; provider probe confirms metadata; no silent partial acceptance |
| 3 | Compare larger completion budget at fixed reasoning effort, then reasoning effort at fixed budget, on development data | Lower truncation plus measured accuracy/cost change across databases |
| 4 | Add structured semantic planning, relevant values, and verified join context in separately measured steps | Gains include nonempty correct answers; record lost as well as gained cases |
| 5 | Add one bounded repair; then structure-aware retrieval or diverse candidates if remaining errors justify it | Repair gains exceed regressions; no gold enters selection; acceptable cost |
| 6 | Freeze a compact baseline, the best new method, and essential ablations; run a new final evaluation | Exact coverage, frozen policy, complete attempts, independently checked scoring |

Start model-setting experiments on the existing 31-example development set as engineering selection; it is already heavily reused. Compare paired outputs and failure causes, rather than promoting a method for one extra success. Keep model settings fixed for architectural ablations. Only after these repairs should a broader model comparison or a larger DSPy search consume substantial budget.

A useful early milestone is complete, well-grounded single-query generation with fewer binding errors. A later milestone is an accuracy gain spread across databases that survives the frozen evaluation protocol. No specific 50%, 70%, or 90% result is established by this review. Even hypothetically correcting all 21 B6R execution errors while leaving its semantic mismatches unchanged would yield only 55/104 (52.88%). Material gains beyond that require solving executable-but-wrong queries.

**How to present a new final result**

A newer, genuinely improved run can be the final system presented in the thesis. Keep the current run as the original baseline or an earlier experiment, with its artifacts intact. Once its 104 examples influence implementation or model selection, a repeat on those same examples is a reused-set evaluation, not a newly untouched holdout.

For a fresh generalization claim, reserve independently sourced and verified unseen questions/databases, with schema-family checks, before further optimization. If collecting a fresh holdout is infeasible, report the repeat transparently as post-evaluation improvement on the existing set and limit the claim. If the intended application serves simpler SQL than Spider 2, add a separately named representative workload with a predeclared sampling rule; do not present its higher score as an improvement on Spider 2.

The most worthwhile next work is the completion/document fix followed by the semantic error corpus and planner. The project already records experiments carefully. Its next investment should improve what the model knows and how it constructs the answer.
