# Recovery development: first implementation

This page records the first implementation. For current progress, completed family
exclusions, error analysis, request controls, and the paid-call boundary, see the
[offline recovery handoff](free-work-handoff.md).

The 3 October 2026 implementation closes the offline completion and task-document
fixes (`LLM-003`, `CTX-001`) and starts the data-use protocol and schema-family
audit (`DATA-004`, `DATA-005`). It does not establish a new accuracy score.
The original best result remains 34/104; its artifacts and manifests are unchanged.

## Completion handling

The Groq provider accepts candidate text only for a nonempty `stop` completion.
`length` becomes `generation_truncated`; other or missing finish reasons become
`generation_incomplete`; an empty stopped response becomes `generation_empty`.
Rejected text stays in audit metadata, with available token usage, reasoning-token
details, backend fingerprint, and transport-attempt count. The pipeline cannot
select that text, and the generation CLI persists the failure and exits with code 1.

The bounded policy makes no automatic regeneration or token-budget increase after
an incomplete response. Existing transport retries are separate. Full quota,
reservation, retry/resume, and failed-request accounting remain `OPS-001` work.
Completion status does not prove valid SQL or a correct answer.

The request uses `max_completion_tokens`; existing Python/config/CLI names
(`max_tokens`, `--max-tokens`) remain compatible. Groq documents this request field
in its [API reference](https://console.groq.com/docs/api-reference).

## Required task documents

[The document manifest](../configs/development/task-documents-v1.json) pins eight
supplied Markdown files by SHA-256. The loader requires an allowlisted basename,
regular UTF-8 file, matching checksum, and at most 65,536 bytes. It rejects
symlinks, special files, empty/NUL text, and traversal. Combined prompt document
content has the same byte cap.

`Text2SQLPipeline.generate_example(example, database_path, document_store=store)`
loads the example's `external_knowledge` reference and fails before generation if
required context is unavailable. Documents enter a separate JSON prompt section;
their names, hashes and byte counts enter result metadata. The prompt version gains
`+task-documents-v1` when documents are present. `prepare(...)` builds the same
generation input without calling a provider.

The generation CLI also accepts `--document-root`, `--document-manifest`, and
`--external-knowledge`; supply all three together. `--reasoning-effort` is optional.
New benchmark runners must use the example-aware API. Historical frozen runners
retain their original contracts; the new paid baseline runner is not implemented
by this change.

## Reproduce the offline preparation

With the pinned local datasets, retrieval artifacts, and environment installed:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.development.preflight \
  --output-dir artifacts/development/recovery002-offline
```

Choose a new directory each time. The command refuses to overwrite an existing
audit. It inspects database schemas, verifies resource hashes, writes `preflight.json`
and `prompts.jsonl`, and makes no provider calls or prediction-SQL executions.
It reads metadata, schemas and supplied documents; it does not load gold answers.

[The development protocol](../configs/development/recovery-development-v1.json)
allows only the original 31 development IDs for prepared prompts and inventories
all 104 reviewed test IDs separately. It disables paid runs and retrieval, excludes
gold from generation, and records that no fresh holdout has been reserved.

The completed local audit is
[`recovery001-offline/preflight.json`](../artifacts/development/recovery001-offline/preflight.json).
Artifacts are gitignored and can be regenerated. It records source/resource hashes
and Python/SQLite versions, with these results:

- 31 prepared development prompts; seven contain the required task document.
- 30 target schemas compared with 140 training schemas.
- Four exact normalized schema matches and three additional potential overlaps
  between target and retrieval databases.
- One development/test overlap candidate: `E_commerce` / `electronic_sales`.
- 20 target databases without declared foreign keys.
- Zero provider calls; zero prediction-SQL executions; `paid_run_ready: false`.

Schema-name overlap identifies cases for review; it does not prove identical
records or duplicated answers. The report is diagnostic. Enforced family exclusions
and an adjudicated family manifest remain to be implemented before retrieval resumes.

## Validation and next work

Run offline regressions with:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

All 240 offline tests passed on 3 October 2026. Regression coverage includes
incomplete responses and usage retention, document
propagation and invalid-file rejection, schema overlap detection, and development
scope restrictions. The historical final-freeze test checks the archived
`text2sql-final-v2.1` release plus its checksum-pinned generated fixture. A separate
regression proves changed source still fails that gate. The development working
tree intentionally differs from the old release; its manifest is not regenerated.

Next at the end of this first implementation: adjudicate family overlaps and enforce exclusions; finish the versioned
evaluation scope; implement numeric request/token/cost limits and resume accounting;
then run the corrected compact-schema baseline with separate budget/effort ablations.
The semantic-error corpus and planner remain outstanding. A later verified result
can become the primary reported result, with its evaluation scope and earlier
results documented explicitly.
