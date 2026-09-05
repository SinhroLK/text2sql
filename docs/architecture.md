# Architecture

The current implementation supports baseline, full-schema, M-Schema, few-shot M-Schema, and linked and hybrid linked-M-Schema generation over the frozen Spider2-Lite SQLite development protocol. It also provides a verified Spider 1.0 train-only retrieval index, deterministic B3/B4 selectors, provider-free per-target retrieval audits, and a provider-independent typed semantic-plan boundary for the future B7P arm.

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
          or full M-Schema + three verified Spider 1.0 demonstrations (B3/B4)
        -> frozen DSPy signature/MIPROv2-compiled instruction (B5)
        -> provider candidate + usage metadata
        -> append-only prediction checkpoint and linking/retrieval audit
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
RET-001 freezes 7,000 Spider 1.0 training demonstrations from 140 databases.
Its loader verifies source and artifact checksums and rejects Spider2 ID,
database, or normalized-question overlap. RET-002 adds fixed seeded random B3
selection and deterministic TF-IDF cosine B4 selection. Both use the same
few-shot M-Schema prompt and record every selected retrieval ID, database, rank,
and score. Provider-free audits cover all 31 development targets; completed
live results are B3 4/31 and B4 5/31. Neither retrieval work nor the B6R result permits
opening the test split.

DSPY-001 wraps the complete frozen B4 context in a typed `B5TextToSQL`
signature. MIPROv2 optimizes instructions only, using a database-disjoint 21/10 partition of the 31 development examples.
Its metric receives only official materialized-result execution correctness;
extra bootstrapped and labeled DSPy demonstrations are fixed at zero because
they would duplicate entire B4 contexts; the three inherited Spider 1.0
retrieval examples remain inside each context. Program state and
manifest hashes are verified on load. A process-wide rolling limiter reserves
LiteLLM-counted input plus maximum output tokens for every task and prompt-model
call, reconciles successful reservations with observed usage, and honors
structured or parsed Groq retry delays without logging provider identifiers.
The paid compile and exact 31-example development run are complete. MIPROv2
selected the original/default instruction with 2/10 validation accuracy; B5
scored 4/31 with 28/31 executable queries and therefore remains below B4 at
5/31 and B6R at 6/31. SEM-001 then checksum-verifies and joins the B1, B6R,
B4, and B5 artifacts into an exact 31-example paired matrix. Its frozen labels
cover all 27 B5 failures without provider calls, gold SQL, or Spider2 test
examples. SEM-002 now defines a strict `semantic-plan-v1` JSON contract, exact
schema and foreign-key/JOIN-graph validation, at most one plan-only correction,
and deterministic plan plus schema-evidence hashes. The validated record exposes
the metadata GEN-001 must attach to each prediction. It is not wired into the
frozen B0-B6R arms and does not generate SQL or make provider calls. The next
Phase 5 implementation is structural retrieval (RET-003), before B7P integration
or another paid run.
Fixture annotations are the only current source of linker precision/recall/F1.

## Semantic-planning boundary

```text
question + canonical schema evidence
        -> semantic-planner-v1 prompt (no SQL)
        -> strict semantic-plan-v1 JSON parser
        -> identifier + FK join graph + relational-shape validation
        -> optional single plan-only correction
        -> semantic-plan-record-v1
             -> canonical plan SHA-256
             -> canonical schema-evidence SHA-256
             -> attempts, repair state, and structured initial issues
```

The boundary fails closed on Markdown/prose, contract drift, target identity
mismatch, unknown identifiers, joins outside declared foreign keys, disconnected
sources, inconsistent grouping/aliases/ties, and invalid recursive shape. V1
does not support self joins or undeclared-FK joins; extending those cases requires
a new version. See `docs/semantic-planning.md`.

## Design rules

- Domain objects do not depend on provider SDKs.
- Providers return candidates and usage metadata but never execute SQL.
- Schema inspection and representative-value sampling open SQLite read-only.
- Prompt construction preserves the original question exactly.
- Frozen baseline arms cannot silently inherit linking behavior.
- Linker policy and version are configuration data recorded in every B6 or B6R result.
- Experiment checkpoints are append-only JSONL and resume without repeating completed IDs.
- Development and test coverage is enforced by the dataset/evaluation boundaries.
- Retrieval artifacts must verify as Spider 1.0 train-only before any selector can consume them.
- B3/B4 selectors and index identities are frozen in config; every target records its exact selection.
- B5 optimizer budgets, development folds, DSPy version, B4 config, and output state are checksum-bound.
- DSPy, LiteLLM, and Optuna versions are validated before any paid optimizer request.
- B5 prompt and task calls share one frozen rolling TPM budget and expose sanitized wait statistics.
- B5 LM recovery caches are unique per fresh run and reusable only through an
  explicit, identity-compatible, time-bounded resume.
- Cache keys retain complete messages, model settings and stochastic rollout
  identity; only complete successful responses are persisted.
- Cache corruption, concurrent resume and identity/resource drift fail closed;
  final 31-example B5 generation remains uncached.
- Optimization gold results are loaded only for explicitly allowed development IDs.
- Semantic plans are strict, versioned, schema-bound records and may be repaired at most once before SQL composition.
- Every component is callable from modules, scripts and tests without a notebook kernel.

The target offline/online architecture, task dependencies and remaining safety/retrieval work are maintained in `docs/project-plan-roadmap.md`.
