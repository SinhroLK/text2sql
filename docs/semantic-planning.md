# SEM-002 typed semantic planning

SEM-002 inserts an explicit relational-intent contract between a natural-language
question and later SQL composition. It is provider-independent and does not
generate or execute SQL.

## Contract

`semantic-plan-v1` is strict JSON with required fields for:

- requested outputs and their aliases;
- source tables and typed foreign-key joins;
- filters, literal/value kinds, aggregations, `GROUP BY`, and `HAVING`;
- ordering, limit, and ties policy;
- temporal grain/window intent;
- recursion and set operations;
- explicit unresolved interpretations.

Responses containing Markdown, trailing prose, missing fields, unknown fields,
non-finite values, or inconsistent typed combinations are rejected. Identifiers
are exact and case-sensitive against the canonical schema evidence.

## Validation boundary

The validator checks database, dialect, and exact question identity; every table
and column reference; declared source coverage; foreign-key-backed joins; full
join-graph connectivity; aggregation aliases and grouping; ordering aliases;
ties requirements; and recursive set shape. `SemanticPlan` v1 intentionally
rejects self joins and joins without a declared foreign-key edge. Those cases
require a new explicit contract rather than a silent relaxation.

An invalid initial response may be passed to one caller-supplied repair callback.
The repair prompt contains only the question, canonical schema, previous plan,
and structured validation issues, and explicitly forbids SQL. A second invalid
response fails closed; there is no retry loop.

## Provenance

A successfully resolved plan produces a `semantic-plan-record-v1` payload with:

- the complete typed plan;
- a deterministic SHA-256 of its canonical JSON;
- the canonical schema-evidence SHA-256;
- attempt count and repair status;
- the initial structured issues when a repair occurred.

`ValidatedSemanticPlan.prediction_metadata()` provides the exact metadata block
that GEN-001 must attach to every future prediction. SEM-002 itself performs no
provider call and reads no Spider2 development or test examples.

Validate and hash a plan file against a local SQLite schema with:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.planning.cli \
  --plan path/to/plan.json \
  --database path/to/database.sqlite \
  --db-id database_id \
  --question "The exact source question"
```

The offline fixtures cover valid and invalid joins, aggregation/grouping,
ordering/ties, temporal intent, recursive/set-operation shape, deterministic
hashing, strict parsing, the one-repair ceiling, and the CLI. They establish the
contract and validator behavior, not Text-to-SQL accuracy. RET-003 now consumes
the plan's structural shape, and the offline GEN-001 composer revalidates and
embeds the complete record. MODEL-001 and the live 31-example B7P development
run remain necessary before any accuracy claim.
