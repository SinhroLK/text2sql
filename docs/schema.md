# Canonical schema model

SCHEMA-001 defines a versioned, immutable representation of an inspected database schema. It is the shared input for prompt serializers, future schema linking, SQL validation, and audit hashes.

## Representation

`SchemaSnapshot` contains a database ID, dialect, and deterministically ordered tables. Each table contains columns in database ordinal order and foreign keys in `(constraint_id, sequence)` order.

The canonical column metadata includes:

- name and declared data type;
- nullability and SQL default expression;
- ordinal position;
- primary-key membership and position, including composite keys.

Foreign keys include their source and target identifiers plus constraint and sequence positions, preserving composite relationships.

## Guarantees

`inspect_sqlite_schema` opens SQLite databases read-only and validates every snapshot before returning it. Validation rejects duplicate table or column names, unstable ordering, nonexistent FK source columns, and nonexistent FK target tables or columns.

`serialize_canonical_schema` emits compact, key-sorted JSON with `schema_version = 1`. `canonical_schema_sha256` hashes that UTF-8 representation. The six development-database hashes are frozen by `tests/test_canonical_schema.py`, so schema drift is detected automatically.

The earlier `serialize_simple_schema` format remains unchanged because it defines the completed B1 prompt. SCHEMA-002 will consume the canonical model to produce the separate M-Schema representation used by B2.

## Verification

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_canonical_schema -v
```

The test suite covers composite PK/FK metadata, defaults, versioned serialization, deterministic hashes for all six development databases, and rejection of nonexistent identifiers.
