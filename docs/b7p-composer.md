# GEN-001 offline B7P composer contract

**Version update:** the v1 contract below remains preserved. Opt-in `gen001-b7p-composer-v2.toml` consumes v2 join evidence; it is an offline development revision pending relational-scope work and MODEL-001. See [review follow-up](review-followup-2026-09-05.md).

The provider-independent portion of GEN-001 is implemented as
`gen001-b7p-composer-v1`. It constructs one deterministic prompt from four
verified inputs:

1. the exact target question and canonical SQLite schema;
2. B6R recall-first schema evidence;
3. a checksum-valid and schema-valid SEM-002 plan;
4. the bounded RET-003 structural retrieval selection.

This milestone does not call a model, generate SQL, execute SQL, or score a
benchmark. GEN-001 remains in progress until MODEL-001 chooses a frozen model
and the resulting B7P arm covers and evaluates all 31 development targets.

## Frozen contract

The configuration is
`configs/generation/gen001-b7p-composer-v1.toml`, SHA-256
`6b60bdf833ab6c90cc16471b8b218df24087c4ea02faeaf4239de43ee0ef89ee`.
It checksum-binds:

- frozen B6R config SHA-256
  `2bd504ab72119273cec01cdaeaef8664a32e3b949581a4061057cd221b552e6b`;
- RET-003 config, manifest, index ID, index artifact, extraction version and
  retrieval strategy;
- SEM-002 plan and record versions;
- the B6R sampling and recall-linking policy;
- exactly one output candidate, temperature `0`, seed `42`, reasoning effort
  `low`, 1,024 maximum output tokens, retry/timeout limits, and prompt/input
  character bounds.

The model is intentionally recorded as `pending-model001`. MODEL-001 must use
this exact prompt and runtime contract for at most three predeclared models,
then freeze the selected model before the first B7P development checkpoint.

## Schema and value evidence

The composer mechanically verifies that its M-Schema sampling and linking
parameters equal the frozen B6R configuration. The prompt includes:

- the complete compact schema containing every target identifier;
- linked detailed M-Schema as priority context, not an allowlist;
- no M-Schema sample values by default.

Representative values are sampled and included only when the validated plan
contains a target filter whose value kind is `literal`, `literal_list`, `range`,
or `relative_time`. Only those filter columns appear in the selective grounding
section, with a maximum of 16 columns. If the plan requires no such grounding,
the database value sampler is not called.

## Prompt and audit boundary

The prompt contains canonical plan JSON, zero to three RET-003 demonstrations,
the full compact schema, linked detailed M-Schema, selective grounding and the
exact question. It instructs the future model to return exactly one read-only
SQLite `SELECT`, optionally prefixed with `WITH`, without Markdown, comments,
alternatives or reasoning. SAFE-001 will later enforce these SQL restrictions
with an AST; the current prompt instruction is not a security validator.

Before composition, the implementation rejects:

- a raw/unvalidated plan or a plan-record hash mismatch;
- question, database, dialect, canonical-schema hash or semantic validation
  mismatch;
- B6R, RET-003 config/manifest/artifact/version drift;
- retrieval selections outside the frozen RET-003 contract;
- oversized plans, demonstration questions or final prompts.

The audit record stores the composer/config/prompt hashes, schema and linked
schema hashes, complete validated plan record, linking and retrieval audits,
grounded column/value counts and value-payload hash, runtime policy and all
dependency identities. It records `provider_called=false` and does not store
sample values separately.

Compose an offline prompt after preparing RET-001/RET-003 artifacts:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.generation.cli \
  --plan path/to/semantic-plan.json \
  --database path/to/database.sqlite \
  --db-id database_id \
  --question "The exact source question" \
  --prompt-output artifacts/prompts/b7p.txt \
  --audit-output artifacts/reports/b7p-composer-audit.json
```

The CLI validates the plan against the database, verifies both retrieval
indexes and their frozen manifests, writes the optional prompt/audit files, and
performs no provider call.

## Fixture evidence and remaining work

Six tests cover dependency drift, deterministic prompt construction, exact plan
and schema provenance, selective value grounding, and JOIN, nested
aggregation/subquery, temporal-window, set-operation and recursive plan shapes.

The next work is the review follow-up linked above, before MODEL-001.
After model selection, GEN-001 still needs an exact
31-development-ID checkpoint, a 31-target plan/retrieval/composer audit,
EVAL-003 scoring, and the predeclared promotion decision. The 104-example test
split remains sealed.


The opt-in [V3 scoped planning contract](scoped-semantic-planning.md) adds independent
set branches and uncorrelated subqueries. V1/V2 remain available; MODEL-001 is
still pending the review gate.
