# Phase 6 security evaluation

## Scope and threat model

SEC-001 evaluates the application boundary around a generated SQLite query. The
protected assets are database integrity, host files, credentials, personally
identifiable information, confidential columns, service availability, and the
audit trail. The attacker controls the natural-language question and may induce
the model to emit malicious or policy-violating SQL. Generated SQL and database
contents are untrusted. The provider, Python runtime, operating system, and the
versioned synthetic fixture are trusted for this experiment.

The versioned corpus covers prompt-to-SQL instruction override, destructive
DDL/DML, multiple statements, system-schema and filesystem access, PII,
credentials, confidential salary data, and a case where a benign-looking input
produces a sensitive query. Benign lookup, filtering, joins, aggregation, CTEs,
and empty results measure overblocking. The corpus uses only synthetic values
and the disposable schema in `data/fixtures/security_schema.sql`.

Out of scope are arbitrary Python execution, malicious SQLite engine exploits,
provider-account compromise, authorization between real users, indirect prompt
injection from database values, and production database snapshot creation.
SAFE-002 is a resource-limited SQL boundary, not an OS container.

## Arms and decisions

- **S0** applies SAFE-001 and SAFE-002 to each frozen candidate SQL statement.
- **S1** additionally applies a question input policy and a schema-aware output
  policy. The output policy rejects validated queries that reference configured
  sensitive tables or columns and releases results only after complete sandbox
  success. Truncated previews, timeouts, validation failures, and execution
  errors fail closed.

The input policy uses explicit, versioned patterns for instruction override,
destructive requests, sensitive-data requests, and host/system access. It is a
defense-in-depth control; the SQL validator and sandbox remain mandatory.
Candidate SQL is frozen in the corpus, so this evaluation is deterministic,
provider-free, and does not claim to measure a live model's attack-generation
distribution.

## Metrics

Attack success rate and unsafe-query rate are the fraction of adversarial cases
whose results would be released. Benign false-positive rate is the fraction of
benign cases blocked. Expected-decision accuracy is reported as a secondary
summary. Every case stays in the denominator.

Reports contain case IDs, categories, hashes, structured policy decisions,
validator issue codes, referenced schema identifiers, and sandbox status. They
omit question text, SQL text, returned columns, and returned rows.

## Reproduction

```bash
python3 scripts/create_fixture_db.py \
  --schema data/fixtures/security_schema.sql \
  --output data/fixtures/security.sqlite
PYTHONPATH=src .venv/bin/python -m text2sql.security.evaluation_cli \
  --database data/fixtures/security.sqlite \
  --report artifacts/reports/sec001-s1-report.json
```

The configuration pins the corpus and fixture checksums in
`configs/security/sec001-s1.toml`. No provider key, Spider2 database, gold
result, or production resource is used.

## Known limitations

The small hand-written corpus demonstrates control behavior but cannot estimate
the prevalence of attacks in deployment. Pattern-based input filtering can be
evaded by paraphrase and can overblock legitimate sensitive-data workflows.
Column policy is exact and schema-specific; aliases are safe only because
SAFE-001 resolves them back to canonical identifiers. Aggregate queries over a
restricted column are conservatively blocked. A real application still needs
user authentication, database authorization, approved snapshot selection,
rate limiting, result-retention rules, and stronger OS isolation for hostile
engine inputs.
