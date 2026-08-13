# Architecture decision records

## ADR-001 - Standard-library-only Phase 0

- **Date:** 2026-08-13
- **Status:** accepted
- **Problem:** The initial project depended on API keys, a real MySQL database and mutable notebook state.
- **Decision:** Phase 0 uses only the Python standard library, a deterministic mock provider and a synthetic SQLite fixture.
- **Reason:** The repository must install and test without secrets or external services before research components are added.
- **Consequences:** Phase 0 results are infrastructure checks and must not be reported as model performance.

## ADR-002 - No SQL execution in Phase 0

- **Date:** 2026-08-13
- **Status:** accepted
- **Problem:** The original demo executed unvalidated model output.
- **Decision:** The new CLI generates and records SQL but cannot execute it.
- **Reason:** Execution becomes available only after AST validation, allowlists and a read-only sandbox are implemented.
- **Consequences:** Execution Accuracy is deferred until the official evaluator is implemented.

