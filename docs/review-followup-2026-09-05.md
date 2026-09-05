# Review follow-up: execution isolation and join evidence

The first development batch after the [project review](project-review-2026-09-05.md)
addresses its two P1 findings. Phase 5 remains in progress. No model selection,
paid generation, or holdout evaluation is part of this batch.

## Evaluation isolation

`SQLiteQueryExecutor` now installs a SQLite authorizer after copying the source
and enabling `query_only`. Only SELECT, READ, FUNCTION and RECURSIVE preparation
actions are allowed; file/extension functions are explicitly denied. ATTACH,
VACUUM INTO, PRAGMA changes, DDL and DML are rejected before side effects.
Tests verify that rejected statements create no external file and leave the
source bytes unchanged, while a recursive CTE with a join and window aggregate
still returns the expected rows.

This is an immediate evaluator correction, not completion of SAFE-001/SAFE-002.
Process isolation, bounded result materialization, and a deadline including the
backup stage remain open. No historical prediction or result artifact was edited.

## Opt-in semantic-plan-v2

The review found no declared FKs in Airlines, city_legislation, electronic_sales
or f1. Those databases account for 22/31 development examples, including three
B6R successes whose SQL uses joins. V1 rejects every multi-table join on them.

V2 adds two required fields to each join while retaining exact identifiers,
source coverage, connected join graphs, and the one-repair limit:

```json
{
  "left": {"table": "memberships", "column": "customer_id"},
  "right": {"table": "customers", "column": "id"},
  "join_type": "inner",
  "evidence": "inferred_equality",
  "rationale": "Membership customer identifiers refer to customers.id."
}
```

`declared_foreign_key` requires a matching declared schema edge.
`inferred_equality` allows an explicit equality between real columns with a
nonempty rationale. It is a hypothesis, not proof of a relationship, uniqueness,
cardinality, or SQL correctness. `semantic-plan-record-v2` includes a
`join_assumptions` array marking these joins `semantically_verified=false`.
The evidence and rationale are included in the canonical plan hash.

V1 retains its JSON representation, hashes, and FK-only join restriction. V2
cannot be silently consumed by the v1 composer, and the v2 composer rejects v1
plans. A repair cannot change the requested plan version. Callers expecting v2
must pass `expected_plan_version="semantic-plan-v2"`, including when the first
response is malformed JSON and its version cannot be inferred.

Planner prompt construction is explicit:

```python
build_semantic_plan_prompt(question, schema, plan_version="semantic-plan-v2")
resolve_semantic_plan(
    response, schema, expected_question=question,
    expected_plan_version="semantic-plan-v2", repair=repair_callback,
)
```

The v2 repair prompt includes the entire response contract. CLI validation can
pin the version with `--plan-version semantic-plan-v2`.

## B7P integration

`configs/generation/gen001-b7p-composer-v2.toml` pairs v2 plans and records with
the existing verified B6R/RET-003 inputs. Its SHA-256 is
`36e6dd2814ec75d7a4cc5fcf637085dcf570bda2b0d52841f8c372e08d75c71b`.
The original composer config remains unchanged at
`6b60bdf833ab6c90cc16471b8b218df24087c4ea02faeaf4239de43ee0ef89ee`.

The existing RET-003 artifact is reused: join count and operator tags did not
change, and the new rationale is not substituted for the source question.
The v2 SQL prompt explicitly identifies inferred relationships as assumptions.
The audit retains the full versioned plan and its assumptions. This configuration
is an offline development revision, not a final MODEL-001 experiment freeze.

Opt in using a raw v2 plan JSON file (not the enclosing audit record):

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.generation.cli \
  --config configs/generation/gen001-b7p-composer-v2.toml \
  --plan path/to/semantic-plan-v2.json \
  --database path/to/database.sqlite \
  --db-id database_id \
  --question "The exact source question"
```

The CLI default remains v1 for compatibility. Successful output is labeled
`composed_offline`, rather than claiming readiness for model selection.

## Verification and next work

The final full suite passed all 165 tests in 6.402 seconds. An offline v2 CLI
smoke also passed with the actual local RET-001/RET-003 artifacts and a synthetic
SQLite database without a declared FK; it recorded the inferred join assumption
and `provider_called=false`. `git diff --check` passed. New regressions cover side-effect
rejection; recursive/join/window execution; inferred versus declared evidence;
unknown endpoints, disconnected sources and self joins; v1 wire compatibility;
version-preserving repair; and actual v2 composition over a SQLite schema with
an undeclared relationship.

The next batch should address the review's flat relational-scope limitation
and predicate/function validation, followed by checkpoint provenance and provider
completion handling. V2 still uses the original flat plan shape: self joins,
independent UNION/subquery branches and fully structured analytic windows are
not implemented. No improved execution accuracy or full 31-target plan coverage
is claimed. MODEL-001 should wait for those compatibility decisions and the
complete planning/generation failure-accounting policy.


## Second batch: scoped plans

The flat-scope and predicate/function work described above now has an opt-in
V3 implementation. Independent SELECT branches, set operations and uncorrelated
predicate subqueries validate locally; nested values reach composer grounding.
The complete offline suite passes 176 tests. CLI smoke checks with actual pinned
retrieval artifacts pass for both UNION and a scalar aggregate subquery, with
`composed_offline` and `provider_called=false`.

See [the V3 contract](scoped-semantic-planning.md) for supported shapes and
remaining limits. V1/V2 configurations and prior experiment results are preserved.
Next: checkpoint provenance and provider completion handling, followed by an
explicit policy for unsupported plans and complete failure accounting before
MODEL-001. No paid calls or holdout evaluation were performed.


## Third batch: checkpoint identity and completion accounting

Baseline runs now persist and enforce the actual run contract, including complete
prepared prompt coverage, databases, provider settings, source and dependency
identity. A single-writer lock protects appends; interrupted runs keep their
manifest. Direct Groq rejects incomplete/unsupported completions; failures and
returned usage remain in checkpoint coverage and evaluation without resampling.
All 182 offline tests pass. Historical paid results were not rewritten.

The [run contract](run-checkpoint-contract.md) defines the B7P unsupported-plan and
failure policy. The next task is its offline orchestrator and compatibility audit;
that stage wiring is not supplied by the baseline runner changes. MODEL-001 is
still NOT STARTED and GEN-001 is IN PROGRESS. No paid or holdout runs occurred.
