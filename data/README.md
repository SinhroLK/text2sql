# Data policy

Large or licensed datasets and database files are not committed to Git.

Planned layout:

- `raw/` - original downloaded benchmark files;
- `processed/` - normalized examples and metadata;
- `fixtures/` - small synthetic resources used by tests;
- `README.md` - source, version, license, checksum and preparation commands for every dataset.

The original two-column `spider_text_sql.csv` is preserved outside the new pipeline as legacy material. It must not be used for final evaluation because it lacks `db_id`, database schemas and the official split.

## Frozen Spider2-Lite source

`DATA-001` does not commit benchmark databases. It freezes their identity in:

- `configs/datasets/spider2-lite-sqlite-v1.toml`;
- `configs/datasets/spider2-lite-sqlite-split-v1.json`.

The source is the official `xlang-ai/Spider2` repository at commit `cafb867313aab4e674652054198f383cf4018943`. Never ingest mutable `main` directly.

Expected local layout for `DATA-003`:

```text
data/
├── raw/spider2/
│   └── spider2-lite/                 # exact pinned upstream files
├── processed/spider2-lite-sqlite-v1/ # normalized metadata produced by loader
└── fixtures/                         # small committed tests only
```

Acquisition procedure:

```bash
git clone https://github.com/xlang-ai/Spider2.git data/raw/spider2
git -C data/raw/spider2 checkout cafb867313aab4e674652054198f383cf4018943
sha256sum data/raw/spider2/spider2-lite/spider2-lite.jsonl
```

The JSONL checksum must equal `4ba48916576fbd60311a2478c6d4550b5d8cf3fcbc512457ea493b5941ca009d`. Other required checksums are in the TOML protocol. The separate official SQLite database archive/files must also be checksummed in the ingestion manifest created by `DATA-003`.

Rules:

- do not commit `data/raw/`, `data/processed/`, cloud credentials or database credentials;
- do not change upstream gold/evaluator files;
- do not use Spider2 gold SQL as prompt, training or retrieval content;
- do not inspect the 104 test outcomes during development;
- verify the upstream license and preserve its notices when distributing derived metadata;
- call the result a custom SQLite split, not the full Spider2-Lite score.
