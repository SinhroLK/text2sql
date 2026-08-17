# Mapping implementation to the thesis

| Project area | Thesis section | Evidence |
|---|---|---|
| Dataset loaders and split policy | Dataset and methodology | dataset version, checksums, split tests |
| Schema serialization/linking | Proposed method | B1/B2/B6 configurations |
| Retrieval and DSPy | Prompt optimization | B3/B4/B5 configurations |
| Validator and refiner | Verification | B7 configuration and repair metrics |
| Guardrails and sandbox | Security | S0/S1 and adversarial evaluation |
| Experiment runner/evaluator | Results | frozen configs, raw JSONL and generated tables |

`DATA-001` evidence is the pinned Spider2-Lite protocol, the database-disjoint split manifest, ADR-003 and `tests/test_dataset_protocol.py`. `DATA-003` evidence is the metadata manifest, ADR-004, checksum-gated loader, preparation CLI and `tests/test_spider2_loader.py`. The methodology chapter must state that the score covers a custom 104-instance SQLite holdout, not the full 547-instance leaderboard setting.
