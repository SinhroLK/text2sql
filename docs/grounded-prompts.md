# Grounded SQLite prompts

**Current status (2026-10-12):** grounded002 is complete: C0 6/31, C1 8/31, C2 13/31. The commands below describe the historical v2 experiment; do not resume it under changed source. The next prepared run is [calculation004](validator-semantic-handoff-2026-10-12.md), with a [frozen protocol](calculation-experiment-2026-10-12.md). Original prompts/results remain unchanged.

The next prompt experiment uses `grounded-sqlite-v2`, implemented in
`src/text2sql/prompting/grounded.py`. The user runs all model API calls. The agent
may prepare prompts, inspect local databases, run mocks/tests, and score locally.

## What changed

The old prompt was a short SQL instruction plus schema and optional task documents.
The replacement adds:

- A SQL-only, read-only output contract with no comments or Markdown.
- A short correctness checklist covering output grain, joins and duplicate counts,
  first/last attributes, baseline rows for window functions, interval endpoints,
  zero-count periods, real division, denominators, and CTE output columns.
- The full schema in a compact format. Column types, nullability, primary-key
  positions and declared foreign-key groups are retained. No tables are removed.
- A heuristic focus list, explicitly described as a hint rather than a restriction.
  Directly named entity tables remain eligible even when another table has more
  matching columns. This prevents losing legislator value evidence when the question
  also strongly matches the terms table.
- Observed values for up to 12 nonsensitive columns across up to five focus tables,
  with up to three values per column. They include SQLite storage types and whether
  the non-null vocabulary was actually exhausted. Samples are never presented as a
  complete vocabulary when sampling stopped early.
- The current SQLite version and a selected function availability list. For example,
  the airline database declares `point` but stores coordinate strings; its actual
  examples and the absence of `X`/`Y` functions are now visible to the model.
- Full, checksum-verified task documents. Values, documents and the question are
  encoded as data with instructions not to treat embedded text as new commands.
  This is defensive prompting, not a guarantee against prompt injection.

Preparation uses no reference SQL or gold answer contents. The instructions were
informed by failures on the already reviewed development set, so this remains
explicit development work, not an untouched-test result. No SQL examples copied
from reference answers are inserted into prompts.

## Sampling and limits

Sampling uses a read-only SQLite connection, safely quoted schema identifiers,
fixed query templates, a 200,000 VM-step and 0.25-second bound per column, and a
128-row fallback when the distinct scan reaches a limit. Samples of sensitive
columns are excluded using the existing project policy. Long/control-character
values are omitted rather than truncated into misleading literals. The audit
records each column's sampling status and whether its vocabulary is exhaustive.

All 31 inputs are token-counted with the existing pinned tokenizer. The next run
keeps the same model, temperature, seed, reasoning settings, execution policy and
quota allowances as the previous experiment. Preparation fails if any request is
oversized; it does not silently remove schema or task documents. Token reservations
include the existing chat allowance and margin and remain estimates.

## Version and run history

- `recovery-live001`: completed original experiment, unchanged.
- `recovery-grounded001`: interrupted v1 diagnostic. Two connection failures and
  one completed model response were recorded. It is not an accuracy experiment.
  Its original prompt source is archived in `prompt-source-snapshot.json`.
  Do not resume it with the newer source.
- `recovery-grounded-mock001`: v1 mock rehearsal; not model evidence.
- `recovery-grounded002`: fresh v2 experiment, prepared with zero API calls.
- `recovery-grounded-mock002`: v2 offline rehearsal with networking blocked.

The latest builder is opt-in through `GroundedSQLPipeline` and the preparation
command below. Historical builders and the existing CLI defaults are retained for
reproducibility; running the old `prepare` command would prepare the old baseline.
Use the new preparation command for revised prompts.

## Run commands

`recovery-grounded002` is already prepared. **The user runs** generation from the
project directory, with the key in the existing `.env`:

```bash
set -a
source .env
set +a

PYTHONPATH=src .venv/bin/python -m text2sql.development.run generate \
  --run-dir artifacts/development/recovery-grounded002 \
  --allow-api --confirm-free-plan --wait
```

This is the full 93-request matched C0/C1/C2 experiment. It can span daily quota
windows. It will not change billing or enable a paid fallback. Resume the same
command and directory after interruption; leave the frozen source unchanged.
The local quota journal does not account for unrelated account usage.

After all generation is complete, scoring is local:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.development.run score \
  --run-dir artifacts/development/recovery-grounded002
```

For a future fresh directory, prepare offline with:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.development.prompt_run \
  --source-run artifacts/development/recovery-live001 \
  --run-dir artifacts/development/CHOOSE-A-NEW-DIRECTORY
```

The manifest records the parent run, exact prompts, settings and resource hashes.
`prompt-audit.json` records value evidence and token counts; `budget.json` records
all request reservations. `example-prompt.txt` contains an exact full prompt.

For application code:

```python
from text2sql.prompting.grounded import GroundedSQLPipeline

pipeline = GroundedSQLPipeline(provider)
prepared = pipeline.prepare(question, database_path, db_id=db_id, documents=documents)
# prepared.generation_input.prompt is reviewable without a model call.
# Only the user initiates pipeline.generate(...) or another model API call.
```

## What testing establishes

Tests cover real coded values beyond the first sample rows, incomplete vocabularies,
sampling bounds, identifier/URI quoting, sensitive-column exclusion, document
integrity, data/instruction separation, database immutability, runtime capabilities,
pipeline delivery, baseline preservation, and rejecting oversized preparations.
Synthetic SQLite cases verify the window/baseline, date-construction and zero-period
patterns in the instructions. An optional full local development test checks all
31 schemas/documents, quotas and the known value-grounding failures without network
access. The mock rehearsal checks all 93 requests and repeat-run idempotence.

The completed checks passed **302 tests**, including 17 new prompt/preparation tests.
All 31 prompts fit: the largest reserved request is 7,176 tokens against the
recorded 7,200-token allowance. The v2 mock completed 93 requests with networking
blocked; its second invocation left the journal byte-for-byte unchanged.

These checks establish prompt construction and execution-path correctness. **They
do not establish higher model accuracy.** That requires the user-run experiment.
The existing SQL validator is unchanged; previously identified false rejections
remain a separate investigation, and prompt changes cannot guarantee their removal.
