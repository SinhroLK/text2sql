# Architecture

Phase 0 implements the narrow path:

```text
question + SQLite database
        -> read-only schema inspection
        -> deterministic baseline prompt
        -> mock provider
        -> GenerationResult
        -> stdout and optional JSONL audit record
```

Generated SQL is not executed by the generation pipeline. The target offline/online architecture and full roadmap are maintained in `docs/project-plan-roadmap.md`.

## Design rules

- Domain objects do not depend on provider SDKs.
- Providers return candidates and usage metadata but never execute SQL.
- Schema inspection opens SQLite in read-only mode.
- Prompt construction preserves the original question.
- Experiment output is append-only JSONL.
- Every future component must be usable from scripts and tests without a notebook kernel.

