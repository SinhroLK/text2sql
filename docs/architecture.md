# Architecture

The current implementation supports baseline, full-schema, M-Schema, and linked and hybrid linked-M-Schema generation over the frozen Spider2-Lite SQLite development protocol.

```text
question + SQLite database
        -> read-only canonical schema inspection
        -> bounded read-only representative-value sampling
        -> extractive-lexical-v1 schema linker
             -> lexical identifier/value scores
             -> bounded direct table/column selection
             -> primary/foreign-key retention and FK path closure
             -> full-schema fallback when no table reaches threshold
        -> linked M-Schema prompt (B6)
          or complete compact schema + linked detail (B6R)
        -> provider candidate + usage metadata
        -> append-only prediction checkpoint and linking audit
        -> separate EVAL-003 read-only execution/result comparison
```

B0, B1 and B2 bypass the linker and preserve their frozen prompt contracts.
B6 sends only the linked M-Schema subset. Its completed 1/31 result exposed
identifier-recall failures. B6R is a separately frozen repair: generation keeps
the full canonical schema, the prompt contains a complete compact identifier
inventory, and linked M-Schema supplies priority detail with all columns from
selected tables. The generation pipeline never executes generated SQL; evaluation remains a separate protected boundary.

## Schema-linking boundary

The linker consumes only the original question, canonical schema metadata and representative values already permitted by the M-Schema sampling policy. It never reads reference SQL, official result CSVs or the closed test split. Its output contains the filtered canonical schema plus a complete audit of scores, reasons, selected identifiers, closure tables, counts and reduction ratios.

Joinability is protected by retaining primary keys, foreign-key endpoints and deterministic shortest foreign-key paths between directly selected tables. If no table reaches the frozen score threshold, the full schema is used. This is a recall-safe engineering fallback, not proof that every relevant table was selected.

Provider-free audits compare full B2 with linked B6 or hybrid B6R context on
development metadata. B6 accuracy is frozen at 1/31 and B6R at 6/31 after separate EVAL-003 scoring.
The next architectural increment is train-only retrieval; neither result permits
opening the test split. Fixture annotations are the only current source of linker precision/recall/F1.

## Design rules

- Domain objects do not depend on provider SDKs.
- Providers return candidates and usage metadata but never execute SQL.
- Schema inspection and representative-value sampling open SQLite read-only.
- Prompt construction preserves the original question exactly.
- Frozen baseline arms cannot silently inherit linking behavior.
- Linker policy and version are configuration data recorded in every B6 or B6R result.
- Experiment checkpoints are append-only JSONL and resume without repeating completed IDs.
- Development and test coverage is enforced by the dataset/evaluation boundaries.
- Every component is callable from modules, scripts and tests without a notebook kernel.

The target offline/online architecture, task dependencies and remaining safety/retrieval work are maintained in `docs/project-plan-roadmap.md`.

