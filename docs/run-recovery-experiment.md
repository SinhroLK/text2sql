# Run the recovery experiment on Groq Free

For the revised prompts, use [the grounded prompt experiment](grounded-prompts.md).
The instructions below describe the original baseline. The user runs all model API commands.

The runnable command is `python -m text2sql.development.run`. It generates all
31 development questions for C0, C1 and C2, then scores them locally in a separate
step. It does not touch the reviewed test split or upgrade your account.

Use the existing project environment and downloaded datasets. In your Groq console,
confirm that your API key belongs to a **Free** organization and check its
[account limits](https://console.groq.com/settings/limits). Defaults are 8,000 tokens
per minute and 200,000 per day, with 10% reserved as a margin. The CLI cannot
independently inspect billing status: `--confirm-free-plan` is your confirmation,
not automatic account verification. A key belonging to a paid organization could
incur that organization's normal charges.

## 1. Prepare locally

```bash
cd /home/sinhro/Documents/master_rad/text2sql
PYTHONPATH=src .venv/bin/python -m text2sql.development.run prepare \
  --run-dir artifacts/development/recovery-live001
```

This makes **zero API calls**. It prepares all prompts, checks quota estimates and
database sizes, and freezes code/config/environment/database/document/evaluator
hashes. Gold CSV files are hashed for later scoring; their contents do not enter
generation prompts. `run.json` and `budget.json` record the settings.

If your account limits differ, supply them on this preparation command using
`--tokens-per-minute N --tokens-per-day N`. An oversized request stops preparation;
the command never drops context to make it fit. Each run keeps its prepared limits.

Prepare once. If this directory already contains your run, skip preparation and
resume it. Keep the code, resources, and environment unchanged until it finishes.

## 2. Set your API key in your terminal

```bash
set -a
source .env
set +a
```

Your key is already in the project's `.env`. These commands export its variables
in the current terminal. The runner reads `GROQ_API_KEY`; it does not automatically
source `.env` or save the key in run artifacts.

### Daily quota correction for recovery-live001 (3 October)

The initial scheduler paused after 47/93 requests because it counted 179,268
maximum reserved tokens against its 180,000-token daily allowance. Groq reported
98,649 tokens, with no recorded HTTP 429 responses. Daily accounting now settles
responses with valid usage against that usage; unknown/interrupted calls retain
their full reservation. Minute quotas and overall token/cost budgets still use
full reservations. The next request always reserves its maximum token allowance.

The existing run has a `quota-accounting-amendment.json` recording the two changed
scheduler source hashes and the original journal prefix. Its original `run.json`,
prompts, model settings and all 47 answers are preserved. Original scheduler source
is retained in `quota-original-sources.json`. Resume this same directory; do not
prepare another run. A temporary-copy simulation verified all 47 outcomes replay
without invocation and `C1:local273` passes admission at the original pause time.
No API calls were made during this correction. Real account limits can still
require waiting; the local journal cannot account for other account usage.

## 3. Generate with the free account

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.development.run generate \
  --run-dir artifacts/development/recovery-live001 \
  --allow-api --confirm-free-plan --wait
```

**This command makes real model requests.** No JSON enable flags need editing.
It uses the prepared 93-request design: C0 1,024/low, C1 3,072/low, C2 3,072/medium.
Request reservations, usage and outcomes are saved in `requests.jsonl`. All arms
share the same 186-attempt and 1.5M-token limits. The historical artificial $3
accounting ceiling remains a conservative token-use guard, not a fee or permission
to use a paid account.

`--wait` keeps the process running through rate-limit pauses, including daily
quota waits. The machine must remain running; completion can span several days.
To stop, press Ctrl+C. Rerun the **same generate command with the same directory**
to resume. Completed calls are replayed from the journal. An interrupted call with
unknown outcome is retained as a failure and is not automatically billed again.

Without `--wait`, the command exits with code **75** when it needs to pause and
prints the retry delay. Run it again later. A known HTTP 429 rejection gets at most
one retry with a new reservation. Truncated output, wrong answers, timeouts and
ambiguous failures are never silently regenerated. A fresh 401/403 stops further
calls; after correcting access, resume the directory, retaining that failed case.

The quota ledger is local to this run. Other jobs sharing the same organization
also consume Groq limits. No account upgrade or paid fallback is performed.

Check progress from another terminal:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.development.run status \
  --run-dir artifacts/development/recovery-live001
```

Generation writes `generation.json` only after all 93 cases have terminal outcomes,
including failures. It executes no predicted SQL and loads no gold answer values.

## 4. Score locally after generation finishes

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.development.run score \
  --run-dir artifacts/development/recovery-live001
```

This step makes **zero API calls**. It refuses incomplete generation, verifies the
journal/prediction identities, then executes SQL in the existing bounded read-only
sandbox. It uses the exact checksum-pinned upstream `compare_pandas_table` function
and pandas-loaded official gold CSVs. Only that comparison function is loaded, not
upstream cloud SDKs. Every failure stays in the denominator of 31.

Scoring limits are fixed for all three arms: 60 seconds/query, 256 MiB/database,
1 GiB worker memory, 100,000 result rows, 16 MiB/result and 1 MiB/value. Rejected or
truncated results count as failures. These limits differ from historical report
execution, so use the matched C0/C1/C2 comparison for promotion.

The results are in:

- **`report.md`** — short results table and selected arm.
- **`scores.json`** — detailed outcomes, paired gains/losses, per-database counts,
  nonempty correctness and selection rationale.
- **`score-checkpoint.json`** — resumable local scoring checkpoint.

If scoring is interrupted, rerun the same score command. A completed report is
replayed without re-executing SQL. Selection follows the predeclared net-gain/loss
and family rules; this is development evidence, not a new final test score.

## Local rehearsal and validation

For a completely provider-free rehearsal, prepare a **separate** directory with
`prepare --mock`, then run `generate` for that directory without API flags or a key.
Mock scheduling uses a virtual clock. Mock runs cannot produce benchmark scores
and cannot be converted into live runs by adding a flag.

Validation on 3 October 2026: 281 tests pass, including interrupted-call recovery,
429 retry, quota pause/resume, prediction tampering, fixture SQL scoring, and cached
scoring. The full 93-request real-input mock rehearsal passed with networking
blocked and exactly 93 reservations; a second generate invocation added none.
No Groq request or new model accuracy measurement was made during implementation.
