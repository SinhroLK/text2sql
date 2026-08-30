# LINK-001 and LINK-002 schema linking experiments

## Status

LINK-001 is **DONE**. Its frozen B6 checkpoint contains 31/31 development
predictions and a complete EVAL-003 report. B6 scored 1/31 (3.23%), so the
aggressive linked-only context did not improve accuracy. The result is retained
as evidence rather than rewritten.

LINK-002 is **DONE**. Its separately versioned B6R recall repair covers all
31 development IDs and scores 6/31 (19.35%) with 25/31 executable outputs. It
keeps all columns for selected tables and adds the complete compact schema as a
fallback identifier inventory. The 104-example test split remains sealed.

## Purpose

B2 sent every table, column, key, and allowed representative value to the model.
It scored 2/31 and used 107,586 input tokens, compared with B1 at 5/31 and
51,287 input tokens. LINK-001 tested whether reducing irrelevant schema context
could lower cost and distraction while preserving needed identifiers. B6 used
18,287 input tokens but scored only 1/31, showing that the v1 pruning removed
useful context on this development set. LINK-002 tests the narrower hypothesis that linked detail can remain useful when
identifier recall is protected. B6R improves over B1 by one correct example and
over B6 by five, so the repair helps, but 19.35% remains a weak absolute result.

The linker consumes only the original development question, canonical target
schema, bounded read-only representative values already allowed by SCHEMA-002,
and a frozen policy. It does not consume gold SQL, gold results, test questions,
provider output, or retrieval examples.

## Versions and frozen policies

The linker version is **extractive-lexical-v1**. The linked prompt version is
**exp003-linked-mschema-v1**. The file
**configs/experiments/exp003-b6.toml** freezes:

| Setting | Value |
|---|---:|
| Maximum directly selected tables | 4 |
| Maximum lexical/context columns per table | 12 |
| Minimum context columns per selected table | 4 |
| Minimum extraction score | 4 |
| Representative-value matching | enabled |
| Foreign-key closure | enabled |
| No-match fallback | complete schema |
| M-Schema examples per column | 3 |
| Maximum sample text length | 50 |
| Rows scanned per column | 24 |

B6 isolates linking by applying LINK-001 directly to the B2 M-Schema
arm while keeping model and generation settings unchanged. The original roadmap
described a later cumulative B5+B6 system; after retrieval and DSPy exist, that
combination requires a separately versioned configuration rather than silently
changing this frozen prototype.

B6R uses **exp004-recall-linked-mschema-v1** and
**configs/experiments/exp004-b6r.toml**. It raises the direct-table limit from
4 to 8, retains every column in selected tables, and supplies the complete
compact schema before the detailed linked M-Schema. The linked detail is a
priority hint, not an allowlist. B6R also explicitly requires one read-only
SQLite SELECT, forbids QUALIFY and dummy placeholder queries, and asks for
qualified ambiguous columns. Model, temperature, seed, reasoning effort,
output cap, sampling limits, evaluator, and development IDs remain unchanged.

## Extraction algorithm

The **link_schema** function:

1. Validates the canonical schema, question, policy, and example keys.
2. Splits snake_case, punctuation, and camelCase identifiers into case-folded
   tokens and applies deterministic singular normalization.
3. Removes only low-information words for linker scoring. The LLM still receives
   the original unchanged question.
4. Scores table and column token/phrase matches independently.
5. Adds a score when an allowed representative value occurs in the question.
6. Prevents generic columns such as id, name, date, and description from
   independently selecting unrelated tables.
7. Prevents a relationship column such as customer_id from selecting a table
   unless the complete ID phrase is mentioned.
8. Keeps at most four directly selected tables, ranked by integer score and
   canonical name.
9. Adds deterministic shortest-path connector tables in the undirected FK graph.
10. Includes lexical columns, all primary keys, required FK endpoints, and
    bounded minimum context columns.
11. Rebuilds and validates a canonical subset in original order.
12. Filters sampled values to selected columns and renders linked M-Schema.
13. Uses the complete schema when nothing reaches the threshold, protecting
    recall instead of guessing a table.

The direct-table limit can be exceeded only by connector tables, which are
recorded separately.

## Audit contract

Each B6 or B6R generation keeps the full canonical schema hash and adds the linked
schema hash, complete policy, direct/closure tables, scores and reasons, selected
identifiers, original/selected counts, reduction ratios, and fallback decision.
The experiment report aggregates fallback count, total reductions, and p50/p95
selected table and column counts.

Checkpoints remain bound to the exact TOML SHA-256. A changed policy cannot
silently resume an older B6 experiment.

## Offline 31-example result

The provider-free audit ran over exactly 31 development examples and six
development databases. It made no Groq calls and did not load the test split.

| Measure | Full B2 context | Linked B6 context | Reduction |
|---|---:|---:|---:|
| Repeated per-question tables | 751 | 124 | 83.49% |
| Repeated per-question columns | 4,569 | 524 | 88.53% |
| Prompt characters | 313,515 | 55,733 | 82.22% |
| Whitespace-token proxy | 30,835 | 6,723 | 78.20% |

Additional results:

- full-schema fallbacks: 0/31;
- selected tables p50/p95: 4/5;
- selected columns p50/p95: 17/23;
- minimum/maximum selected tables: 2/6;
- minimum/maximum selected columns: 5/24;
- six examples added connector tables through FK closure.

The deterministic artifact is
**artifacts/reports/exp003-b6-linking-audit.json**. Its current SHA-256 is
**93d85cbdd41a5cf595576a9ed7e925ff3ccd253e5f285be14b8c72a4153ba238**.

The whitespace-token count is a provider-independent proxy, not Groq tokenizer
usage. B6 actual usage and accuracy are recorded below; B6R still requires a
live run.

## Frozen live B6 result

The completed B6 checkpoint covers all 31 development IDs. EVAL-003 reports
1/31 correct (3.23%), 27/31 executable SQL outputs, 18,287 input tokens and
6,929 output tokens. The sole reported match, `local275`, is a dummy empty query
that happened to match an empty gold result, so it is not evidence of useful
semantic generation. The B6 prediction SHA-256 is
**05dad4186f7e16d7a23116e4ecec9a3cf8dbc0e9932f8682079ad7e240ac04df**
and the report SHA-256 is
**838a2631bebdd22db54270b14d05d62dce1e579d464d6cc8678081159e8d9d20**.

Compared with B1, B6 saved 64.34% of input tokens but lost four correct
examples. Inspection showed missing linked tables or columns in those cases,
including `cities.insert_date`, `alien_data.aggressive`,
`packaging_relations`, and `picking_line`. This evidence motivated B6R.

## B6R provider-free audit

B6R selected 215/751 repeated tables and 1,764/4,569 repeated columns across
31 development examples, with no full-schema fallback. Selected table p50/p95
is 8/10 (FK closure can exceed the eight direct-table cap); selected column
p50/p95 is 62/103. Its hybrid prompts total 290,853 characters, 7.23% below
full B2, while the whitespace-token proxy is 36,056, 16.93% above B2 because
the prompt contains both compact identifiers and linked detail.

The audit SHA-256 is
**b4c75877aef0d7f0617de70891874aac1c074e3f0c495326e382ccb13b1b4c18**.
This is an engineering context measurement; the live accuracy result follows.

## Frozen live B6R result

The completed checkpoint covers 31/31 development IDs. EVAL-003 reports 6/31
correct (19.35%), 25/31 executable, 19 result mismatches, and 6 execution
errors. Usage is 94,140 input and 15,806 output tokens; latency is 71,597 ms
total with p50/p95 2,174/3,335 ms. At the recorded model rates, estimated token
cost is USD 0.023605.

Correct IDs are `local171`, `local202`, `local270`, `local274`, `local275`, and
`local310`. Five produce non-empty correct results. `local275` matches an empty
reference result, but unlike B6 it uses a plausible domain query rather than a
dummy placeholder. No generated query contains `QUALIFY` or the forbidden dummy
pattern.

Against B1, B6R adds `local171` and `local310`, loses `local068`, and improves
from 5/31 to 6/31 (+3.23 percentage points). Input usage is 83.56% higher than
B1 but 12.50% lower than B2. Compared with B6, it restores five correct
examples at the cost of substantially larger context.

Execution failures are: nested-window misuse (`local068`), two 60-second SQLite
timeouts (`local169`, `local170`), incomplete SQL at the 1,024-token cap
(`local279`), an undefined alias/column (`local311`), and PostgreSQL-only
`generate_series` in SQLite (`local355`).

Prediction SHA-256:
**46664f819ce0d8faa2fe377babda3a6555df38477e0570415df146345df26577**.
Report SHA-256:
**aaa130ec8e2f606058c413ac9434f49b9af618ec2fb5da45214f4896bd7802fc**.

## Schema precision, recall, and F1

**evaluate_schema_linking** calculates case-insensitive table and column
precision, recall, and F1 when trusted required-schema annotations exist. Tests
verify these metrics on synthetic fixtures.

Precise aggregate recall cannot be reported for 30 of 31 real development
examples because their reference SQL is unavailable and result CSVs do not
identify required schema elements. Deriving labels from B1/B2 model output would
be circular. The project therefore reports fixture recall/F1, real reduction,
and EVAL-003 correctness without fabricating a real recall number.

## Offline audit command

    PYTHONPATH=src .venv/bin/python -m text2sql.experiments.linking_cli \
      --experiment-config configs/experiments/exp003-b6.toml \
      --output artifacts/reports/exp003-b6-linking-audit.json

Installed equivalent:

    text2sql-audit-linking

The report contains per-example hashes and selection decisions, but not raw
question text.

## Tests

    PYTHONPATH=src .venv/bin/python -m unittest \
      tests.test_schema_linker \
      tests.test_linking_audit \
      tests.test_pipeline \
      tests.test_baseline_experiment \
      -v

The complete suite is currently 91/91.

## Re-score completed B6 and B6R

Re-running frozen B6 uses its completed checkpoint and should make no provider
requests:

    PYTHONPATH=src .venv/bin/python -m text2sql.experiments.cli \
      --experiment-config configs/experiments/exp003-b6.toml \
      --predictions artifacts/experiments/exp003-b6-predictions.jsonl \
      --report artifacts/reports/exp003-b6-report.json

Run the B6R offline audit without GROQ_API_KEY:

    PYTHONPATH=src .venv/bin/python -m text2sql.experiments.linking_cli \
      --experiment-config configs/experiments/exp004-b6r.toml \
      --output artifacts/reports/exp004-b6r-linking-audit.json

Re-score the completed B6R checkpoint (no provider calls when all 31 records are present):

    PYTHONPATH=src .venv/bin/python -m text2sql.experiments.cli \
      --experiment-config configs/experiments/exp004-b6r.toml \
      --predictions artifacts/experiments/exp004-b6r-predictions.jsonl \
      --report artifacts/reports/exp004-b6r-report.json

The runner refuses test/unknown IDs and scores only after exact 31-ID coverage.

## Completion criterion

LINK-001 satisfies its completion criterion: the 31-ID B6 checkpoint, EVAL-003
report, comparison, artifact hashes, and negative result are all recorded.

LINK-002 satisfies its completion criterion: B6R covers exactly 31 development
IDs, EVAL-003 produced the scored report, B1/B2/B6 comparisons and artifact
hashes are recorded, and the test split remained unopened.

## Known limitations

- This v1 linker is lexical and cannot reliably resolve arbitrary synonyms.
- Value matching sees bounded samples, not a complete value index.
- Minimum context columns use canonical order without stronger evidence.
- FK closure can add distractor connector tables.
- Full-schema fallback protects recall but removes token savings.
- The policy must not be tuned on the 104-example test split.
- A trained or embedding-based linker must use a new version rather than
  silently changing extractive-lexical-v1.

