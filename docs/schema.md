# Schema representations

The project has three separate, deterministic schema stages:

- SCHEMA-001 canonical JSON is the internal, versioned source of truth used for validation and audit hashes.
- SCHEMA-002 M-Schema is the model-facing B2 prompt representation derived only from that canonical snapshot.
- LINK-001 derives a question-specific canonical subset and linked M-Schema for B6.
- LINK-002 adds the B6R recall policy and hybrid full-compact/linked-detail prompt.

The completed B1 simple-schema serializer remains unchanged so B1 results stay reproducible.

## Canonical representation (SCHEMA-001)

`SchemaSnapshot` contains a database ID, dialect, and deterministically ordered tables. Each table contains columns in database ordinal order and foreign keys in `(constraint_id, sequence)` order.

Columns retain names, declared types, nullability, SQL defaults, ordinal positions, and primary-key positions. Foreign keys retain source/target identifiers and composite-key ordering.

`inspect_sqlite_schema` opens SQLite databases read-only and validates every snapshot. Validation rejects duplicate identifiers, unstable table/FK ordering, and relationships that reference nonexistent tables or columns. `serialize_canonical_schema` emits compact, key-sorted JSON with `schema_version = 1`; `canonical_schema_sha256` hashes that UTF-8 representation.

## M-Schema representation (SCHEMA-002)

`serialize_mschema` emits the XiYan-compatible sections used by B2:

```text
〖DB_ID〗 fixture
〖Schema〗
# Table: customers
[
(customer_id:INTEGER, Primary Key, Examples: [1, 2]),
(name:TEXT, Examples: ["Alice", "Bob"])
]
〖Foreign keys〗
orders.customer_id=customers.customer_id
```

The serializer includes canonical table/column names, simplified declared types, primary keys, foreign keys, and optional representative values. It rejects unknown example keys, control characters in identifiers, unsupported values, non-finite numbers, and attempts to attach examples to sensitive columns.

The format is based on `PAPER-XIYAN-001` and the official `CODE-XIYAN-MSCHEMA-001` implementation recorded in `docs/sources-and-references.md`. Our implementation is intentionally local and minimal; it does not copy the upstream class or require its extra dependencies.

## Representative-value policy

`sample_sqlite_mschema_values` opens each database with SQLite `mode=ro` and enables `PRAGMA query_only`. The frozen B2 policy is:

- at most 3 distinct examples per column;
- scan at most 24 non-NULL rows per column;
- omit text longer than 50 characters;
- omit blobs and non-finite numbers;
- omit columns whose normalized names indicate passwords, credentials, tokens, contact/address/birth data, payment-card data, SSNs, or IBANs;
- JSON-escape strings before prompt insertion and explicitly tell the model that values are untrusted literals.

Rows are scanned in primary-key order, or `rowid` order when no primary key exists, and are deduplicated in memory. This bounds work without whole-column sorts. The pipeline caches sampled values by resolved database path, canonical-schema hash, and immutable sample policy, so multiple questions against one database reuse exactly the same context.

The sampler is a research safeguard, not a general sensitive-data classifier. Production or private databases require an explicit allowlist or disabling examples with `examples_per_column = 0`.

## B2 integration and status

The `mschema` prompt variant is versioned as `exp002-mschema-v1`. Generation records include the canonical schema hash, prompt hash, schema representation, and exact sample policy. The frozen configuration is `configs/experiments/exp002-b2.toml`.

SCHEMA-002 is `DONE`. The controlled 31-example B2 run scored 2/31 (6.45%) versus B1 at 5/31 (16.13%); full M-Schema more than doubled input tokens and did not improve accuracy. The negative result motivates selective schema linking rather than sending every table and representative value unchanged.

## Verification

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_mschema tests.test_baseline_experiment -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

The tests cover PK/FK rendering, injection-safe string escaping, sensitive-column omission, read-only sampling, invalid example rejection, B2 prompt/report plumbing, and frozen M-Schema hashes for all six development databases.


## Linked M-Schema representation (LINK-001)

The **linked_mschema** prompt variant is versioned as
**exp003-linked-mschema-v1**. It runs the deterministic
**extractive-lexical-v1** linker over the full canonical schema and bounded
SCHEMA-002 samples, then serializes only selected tables, columns, keys,
relationships, and values.

The linker scores lexical table/column matches and allowed representative values,
suppresses generic-column false positives, retains primary/FK columns, and adds
shortest FK connector paths. A no-match question uses the complete schema as a
recall-safe fallback. The full canonical schema hash remains the database
identity; a separate linked-schema hash records the selected subset.

The provider-free 31-example audit selected 124/751 repeated tables and
524/4,569 repeated columns. Prompt characters fell by 82.22%, and the
provider-independent whitespace-token proxy fell by 78.20%. No full-schema
fallback was used. These numbers measure context reduction, not Execution
Accuracy.

LINK-001 and LINK-002 are **DONE**. Frozen B6 scored 1/31, confirming
that the linked-only subset was too aggressive. Recall-repaired B6R scored 6/31
with 25/31 executable outputs and improved by one example over B1. B6R keeps
all columns in selected tables and adds the complete compact identifier inventory
before linked M-Schema detail. Exact real aggregate schema recall cannot be
claimed because protected reference SQL is unavailable for 30 examples; fixture
annotations test precision/recall/F1 behavior instead.

See **docs/schema-linking.md** for the algorithm, frozen policy, audit hash,
commands, limitations, and Definition of Done.

Additional verification:

    PYTHONPATH=src .venv/bin/python -m unittest \
      tests.test_schema_linker \
      tests.test_linking_audit \
      tests.test_pipeline \
      tests.test_baseline_experiment \
      -v

The complete offline suite is currently 91/91.
