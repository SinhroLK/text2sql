# Data policy

Large or licensed datasets and database files are not committed to Git.

Planned layout:

- `raw/` - original downloaded benchmark files;
- `processed/` - normalized examples and metadata;
- `fixtures/` - small synthetic resources used by tests;
- `README.md` - source, version, license, checksum and preparation commands for every dataset.

The original two-column `spider_text_sql.csv` is preserved outside the new pipeline as legacy material. It must not be used for final evaluation because it lacks `db_id`, database schemas and the official split.

