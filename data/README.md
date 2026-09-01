# Data policy

Large or licensed datasets and database files are not committed to Git.

Planned layout:

- `raw/` - original downloaded benchmark files;
- `processed/` - normalized examples and metadata;
- `fixtures/` - small synthetic resources used by tests;
- `README.md` - source, version, license, checksum and preparation commands for every dataset.

The original two-column `spider_text_sql.csv` is preserved outside the new pipeline as legacy material. It must not be used for final evaluation because it lacks `db_id`, database schemas and the official split.

## Frozen Spider 1.0 retrieval source

RET-001 uses only `train_spider.json` from the official Spider 1.0 archive.
The official Yale LILY page distributes the archive under CC BY-SA 4.0. The
archive, source files and generated retrieval artifact are not committed.

Pinned identity:

- official page repository commit: `08abddcedadc43a59e516c9c55b971b8a8ffcd4e`;
- Spider code repository commit: `b7b5b8c890cd30e35427348bb9eb8c6d1350ca7c`;
- official archive SHA-256: `00636695dabed6b5f4b8328a16b13e069a2f16591d5efcce57660669c85b121b`;
- `train_spider.json` SHA-256: `c43d0d72e59e1a9e1a60837da9bf70d5a6277226bdb7f634d544f380646f527a`;
- `tables.json` SHA-256: `61bb20aa401f03164e2d7f3b16509b7b5f79cc9c943ca7bd159046df1159e2ed`.

Place the archive at `data/raw/spider1/spider_data.zip` and extract
`spider_data/train_spider.json` and `spider_data/tables.json` below that
directory. Then run:

```bash
PYTHONPATH=src python -m text2sql.retrieval.cli
```

The frozen config and output contract are
`configs/datasets/spider1-train-retrieval-v1.toml` and
`configs/datasets/spider1-train-retrieval-manifest-v1.json`. The generated
index contains 7,000 records from 140 training databases and has SHA-256
`82ee39e03792647fa7efeddf1fcd293ca068f0fc879d9a88cc27c8546550389e`.
Its firewall checks all 135 Spider2 IDs/questions and 30 databases and records
zero overlaps. Spider `dev.json`, `test.json`, Spider2 examples and the
legacy CSV are not retrieval sources.

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
not evaluate SQL. EVAL-002 expects the separately downloaded official databases
under `spider2-lite/resource/databases/spider2-localdb/` and authorized protected
reference SQL under the separate `data/private/spider2-lite/gold/sql/`. See
`docs/spider2-evaluation-runner.md` for the exact six development databases,
missing references, hashes and CLI.

Rules:

- do not commit `data/raw/`, `data/processed/`, cloud credentials or database credentials;
- do not change upstream gold/evaluator files;
- do not use Spider2 gold SQL as prompt, training or retrieval content;
- do not inspect the 104 test outcomes during development;
- verify the upstream license and preserve its notices when distributing derived metadata;
- call the result a custom SQLite split, not the full Spider2-Lite score.
