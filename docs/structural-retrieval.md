# RET-003 structural retrieval

RET-003 is an offline, deterministic retrieval layer derived only from the
checksum-verified Spider 1.0 training index. It adds SQL-shape evidence to the
lexical TF-IDF signal used by B4; it does not call a provider, generate SQL, or
evaluate Spider2 outcomes.

## Frozen artifacts

The policy is frozen in
`configs/retrieval/ret003-structural-v1.toml`. It binds the exact RET-001 index
and manifest hashes, requires 7,000 source entries, and preserves the existing
zero-overlap Spider2 ID/database/normalized-question audit. The generated
contract is
`configs/retrieval/ret003-structural-manifest-v1.json`.

Build and verify the artifact with:

```bash
PYTHONPATH=src python3 -m text2sql.retrieval.structural_cli
```

The build writes ignored runtime files under
`artifacts/retrieval/spider1-train-structural-v1/`. Each structural record stores
only its RET-001 retrieval ID/source ordinal, normalized skeleton, skeleton
hash, operator signature, and SQL character count. It does not duplicate the
source question or SQL. Loading recomputes every record from the verified base
index and rejects manifest, checksum, order, or derivation drift.

The frozen 7,000-entry structural artifact SHA-256 is
`e4a8407153f6cfefa48e2ffd28102908ebb2c57ca089efa5c03d218fa849952d`.
Its generated manifest SHA-256 is
`d6156bf6f274599c8a38bc452de86d7b80cf322f9615a2efc4fd215726d5914a`.

## Extraction and ranking

The standard-library scanner removes SQL comments and abstracts string/numeric
literals and identifiers. It retains query-shape tokens and derives join count,
subquery, CTE/recursive, aggregation, `GROUP BY`, `HAVING`, window, set
operation, ordering, limit, temporal, and distinct features. This is a lexical
SQL scanner rather than a full dialect parser; its deterministic tags are
retrieval hints, not SQL-validity or semantic-correctness claims.

`QuestionPlanHybridSelector` maps a validated `SemanticPlan` to the same
signature and combines:

- 45% corpus-fitted question TF-IDF cosine similarity;
- 55% structural similarity, consisting of 70% operator-tag Jaccard and 30%
  join-count proximity.

Tie-breaking is deterministic: total score, structural score, question score,
then retrieval ID. Each selected demonstration records the raw question and
structure scores, weighted components, total, matched/missing/extra tags, and
target/candidate join counts.

The frozen policy returns at most three demonstrations, rejects an individual
SQL over 2,000 characters, caps combined demonstration SQL at 4,500 characters,
and requires a structural score of at least 0.30. If nothing meets the
structural threshold, the selector returns an empty demonstration list instead
of falling back to the full B4 context.

## Audit and boundaries

`build_per_target_retrieval_audit` emits one record per validated plan with the
target ID/database, normalized-question hash, plan hash, schema-evidence hash,
target signature, every selected retrieval ID, and all score components. It
accepts only `fixture` or `development` scope and explicitly rejects `test`.
Gold SQL and result correctness are not inputs.

Current per-target coverage is fixture-backed because GEN-001 has not yet
produced the 31 frozen development plans. GEN-001 must run this same audit while
building its checkpoint and freeze the resulting 31-target artifact before a
paid B7P evaluation. RET-003 therefore establishes retrieval reproducibility
and leakage safety, not an accuracy improvement.

## Limitations

- Identifier abstraction intentionally discards schema-name similarity from
  the SQL skeleton; question TF-IDF remains the lexical component.
- The scanner approximates SQL structure and can miss unusual SQLite syntax.
- Spider 1.0 query shapes may not cover every Spider2 enterprise pattern.
- Structural similarity does not prove that a demonstration has the right
  schema, business meaning, or output semantics.
