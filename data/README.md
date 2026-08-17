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

Local layout implemented by `DATA-003`:

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

The JSONL checksum must equal `4ba48916576fbd60311a2478c6d4550b5d8cf3fcbc512457ea493b5941ca009d`. Other required checksums are in the TOML protocol.

Prepare the metadata-only dataset:

```bash
PYTHONPATH=src python -m text2sql.datasets.cli
```

Equivalent installed command:

```bash
text2sql-prepare-spider2
```

The loader performs these checks before producing output:

1. validates the frozen TOML protocol and split-manifest checksum;
2. validates `spider2-lite.jsonl` SHA-256 before parsing it;
3. checks all 547 IDs and the pinned 205 BigQuery / 207 Snowflake / 135 SQLite counts;
4. requires exact coverage of the 135 frozen SQLite IDs;
5. verifies that every ID belongs to the expected development or test database;
6. rejects duplicate IDs and fields named `sql`, `query`, `gold_sql` or `gold_query`;
7. compares the generated manifest with
   `configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json`.

Successful preparation writes:

- `examples.jsonl` — 135 normalized metadata-only `Text2SQLExample` records,
  ordered by ID; expected SHA-256
  `9951e147543c819597dec0336c486612171e36c73ddc5b7e8b387e6f20b6c9f0`;
- `dataset-manifest.json` — source identity, platform/split/database counts,
  output hash and explicit anti-leakage flags.

`DATA-003` intentionally does not download or load SQLite database files and does
not evaluate SQL. The official database archive/files and their checksums become
part of the execution environment in `EVAL-001`.

Rules:

- do not commit `data/raw/`, `data/processed/`, cloud credentials or database credentials;
- do not change upstream gold/evaluator files;
- do not use Spider2 gold SQL as prompt, training or retrieval content;
- do not inspect the 104 test outcomes during development;
- verify the upstream license and preserve its notices when distributing derived metadata;
- call the result a custom SQLite split, not the full Spider2-Lite score.
