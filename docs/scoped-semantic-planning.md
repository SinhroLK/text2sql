# Scoped semantic planning (development v3)

Phase 5 review follow-up adds opt-in `semantic-plan-v3` and
`gen001-b7p-composer-v3`. This addresses independent UNION branches and
uncorrelated predicate subqueries before MODEL-001. V1 and V2 remain supported;
the default CLI configuration still selects V1.

## Contract

The envelope contains exactly `plan_version`, `db_id`, `dialect`, `question`,
`root`, and `uncertainties`. `root` is a query scope:

- A `select` has `kind`, globally unique `scope_id`, and the V2 SELECT fields:
  `outputs`, `sources`, `joins`, `filters`, `aggregations`, `group_by`, `having`,
  `ordering`, `limit`, `ties`, and `temporal`. Each SELECT validates its own
  identifiers and connected source graph. V2 join evidence and rationale remain
  required, and inferred relationships remain unverified assumptions.
- A `set` has `kind`, `scope_id`, `operator`, `left`, `right`, `ordering`, and
  `limit`. Operators are `union`, `union_all`, `intersect`, and `except`.
  Both children are full scopes and must project the same number of columns.
  Final ordering uses an explicit output alias from the left branch.
- Every filter and HAVING predicate adds `subquery`. It is `null` except when
  `value_kind="subquery"`, which requires an embedded scope. Scalar and IN
  subqueries project one column. EXISTS predicates have no outer operand columns.

Scopes are limited to depth 4 and 16 total nodes; IDs must be unique. Comparisons
require actual operands and compatible value kinds. Pattern literals must be
text. Aggregate functions are restricted to count, sum, avg, min, max, total,
and group_concat; only count may omit its column, and DISTINCT needs a column.
All filters are conjunctive. Validation does not establish semantic correctness
or guarantee a scalar subquery returns one row.

Self-join aliases, correlated subqueries, derived-table sources, Boolean OR trees,
CTEs, recursion, and fully structured analytic windows are not supported by V3.
Unsupported requirements must be recorded in uncertainties, not hidden in prose.
This is a bounded development contract, not a claim of full development-set
coverage or a completed MODEL-001 readiness gate.

## Integration

`build_semantic_plan_prompt(..., plan_version="semantic-plan-v3")` emits the
contract. Pin `expected_plan_version="semantic-plan-v3"` when resolving a
response, including repairs. Raw plans serialize deterministically; versioned
records retain the nested tree, canonical hashes, and scope-labeled inferred
join assumptions. Repairs cannot switch the expected version.

The existing RET-003 index is reused. Target structural features aggregate
SELECT scopes and set nodes, matching the existing scanner's coarse tags
(including its multiple-SELECT subquery tag for UNION). This does not make the
retrieval signature a full representation of query semantics. Composer value
sampling traverses all scopes and stays limited to required filter columns.

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.generation.cli \
  --config configs/generation/gen001-b7p-composer-v3.toml \
  --plan path/to/raw-v3-plan.json \
  --database path/to/database.sqlite \
  --db-id database_id --question "The exact source question"
```

Composition is offline and reports `provider_called=false`. No model is selected
and no execution-accuracy improvement is claimed. Executable fixtures and failure
cases live in `tests/test_scoped_semantic_plan.py`; nested grounding and composer
version compatibility are covered in `tests/test_b7p_composer.py`.
