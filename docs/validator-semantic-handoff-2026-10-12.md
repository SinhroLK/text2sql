# Validator and calculation experiment handoff

Work started from clean master `a37ae0897d5ab86bff9d2e33200a99456ebdb1b4`.
No model API calls, billing changes, commits or pushes were made. All original
run files were inventoried by SHA-256 before changes. Both completed experiments
were present locally, including manifests, prompts, journals, predictions and
scores; no required evidence was missing. These ignored artifacts are local-only
unless separately backed up.

## Validator revision

`safe001-sqlite-v2` fixes two independently reproduced problems:

- SQLite reports some `COUNT(*)` reads over CTEs/derived relations with an empty
  column and no database. The previous authorizer treated those as missing base
  tables. The new branch admits only that callback shape; physical reads still
  require canonical tables. SQLite resolves CTE scope, including forward/nested
  references and shadowing. No global CTE-name whitelist is introduced.
- `sqlparse` can classify valid WITH queries as UNKNOWN when CTE names are keyword
  tokens (including `full` and `start`). A top-level statement fallback handles
  those declarations, followed by the same SQLite compilation and authorizer.
  SELECT is still required; a SELECT inside a CTE cannot authorize outer DML.

One statement, comment prohibition, bound-parameter rejection, forbidden
functions/keywords, canonical physical identifiers, snapshot revalidation,
query_only, authorizer, time and resource limits remain enforced. Compilation
alone never authorizes execution. Deliberate comment restrictions are unchanged.

New tests exercise derived COUNT reads, MATERIALIZED/recursive/forward/nested
CTEs, keyword and quoted names, provenance, out-of-scope/missing columns, hidden
writes, system tables, functions, parameters, comments and unsupported DISTINCT
windows. A sandbox regression verifies the same fix at worker execution time.

## Preserving and verifying evidence

`scripts/recovery_revision.py archive` was run before editing source. It first
verified the original manifests and hash-chained journals, reconstructed saved
predictions from durable outcomes, checked score/run links, and copied source and
small resources to `artifacts/development/validator-scope-v2/frozen-root`.
Large data files are hardlinks and are hash-verified on every use; they must not
be edited through either path. This is a local reproducibility archive, not a
backup independent of the data files. `historical-evidence.json` records hashes
of every original run file. Neither historical manifests nor verifier rules were
amended to accept source drift. Loading the old runs against current source is
expected to fail; use the verified archived root for historical analysis.

Authoritative reevaluation is `validator-scope-v2/evaluation-v4`, with a completion
marker, source hashes, policy version, original run/prediction/score hashes, and
per-rejection SQL hashes. It revalidates every historical rejection and executes
only newly accepted predictions through SAFE-002 with the original 60-second
limits and pinned official comparator/gold inventory. Unaffected historical
outcomes are carried forward explicitly; this is not a full rerun of all queries.
The preliminary `evaluation-v2` lacks a completion marker: its source-drift check
failed during implementation. Preserve it as an incomplete diagnostic, never
use it for claims. The completed evaluation-v3 is retained as an intermediate policy audit. Final review then added a parenthesized-SELECT regression/fix; evaluation-v4 uses that final source throughout. Prepared calculation003 and mock003/004 are also preserved but superseded; do not generate from them. The ready run is calculation004 and its final offline rehearsal is mock005.

Reproduce into another fresh directory (offline):

```bash
PYTHONPATH=src .venv/bin/python scripts/recovery_revision.py verify
PYTHONPATH=src .venv/bin/python scripts/recovery_revision.py evaluate \
  --output-dir artifacts/development/validator-scope-v2/CHOOSE-NEW-EVALUATION
```

## Semantic findings and uncertainty

Seven independent in-memory SQLite tests in `tests/test_semantic_patterns.py`
provide counterexamples, not repaired benchmark predictions:

| Pattern | Counterexample and implication |
|---|---|
| Cumulative measures | Snapshots 10,17,15 sum to 42; the final total is 15. MAX is also wrong when corrections decrease a snapshot. |
| Date joins | January sales of 3 and 7 join to zero when day strings are compared with a month key; normalizing both to month yields 10. Bare YYYY-MM and invalid modifiers return NULL. |
| Hierarchies | A chain with quantities 2 then 3 contains 6 terminal units at its final root; the intermediate parent is not another final root. Isolated roots need an explicit population decision. |
| FIFO | Interleaved A4,B2,A4 demand against A6,B2 stock allocates 4,2,2 by line. Grouping A demand first loses which line is short. Mean percentages and a ratio of totals also differ. |
| Pairwise events | One driver moving third to first crosses two drivers in one lap; counting improved laps gives one. Filtering before LAG loses the preceding row. Crossings alone do not adjudicate pit/retirement causes. |
| Population | Two jobs paying 100 and 300 average 200 as jobs, but 166.67 when the first job has two matching skill rows. The question must resolve which population is intended. |
| Scope/runtime | A missing projected column and COUNT(DISTINCT ...) OVER fail in SQLite; a distinct relation followed by a supported count is a viable general pattern. |

These support the visible defects in grounded C2 local269/272/279/309/356 and
scope/runtime errors local009/354. They do not prove that correcting one detail
would make any saved answer match every reference. local273 already implements
range overlap; the remaining ordering and output conventions are unresolved.
local336 already attempts pairwise events; full pit/retirement classification
needs further independent adjudication. local170 has both invalid aggregate use
and cohort-side counting. local335 selects a constructor aggregate rather than
the requested driver-season population. No predictions or gold files were edited.

Reference review remains separate from model error attribution:

- local168 accepts five salary results: 92689.2222, 96521.7109, 98586.9248,
  106900, and 84243.5998. The wording does not unambiguously settle weighting
  jobs versus job-skill matches. Do not pick one of these interpretations as truth
  solely because it helps a score.
- local277 accepts 277.3343, 266.2247, and 417.4506. The question names seasonality
  and weighted regression without fully defining weights/normalization. The new
  SQL attempts regression; the remaining method discrepancy needs adjudication.
- local273's only supplied CSV labels PRODUCT_NAME with 4280 and 6520 and gives
  zero percentages. That conflicts with a naive reading of human-readable names,
  but is not sufficient evidence to declare the reference wrong. Keep representation
  and allocation issues separate; do not rewrite the reference.

## Next controlled experiment

See [preregistered protocol](calculation-experiment-2026-10-12.md). The prepared
`recovery-calculation004` has 31 C2 requests and `grounded-calculation-v3` prompts.
It changes only the internal instruction block; all frozen context is preserved.
The maximum reserved request is **6871 tokens** under the recorded 7200 safe
allowance. A 4096 completion allowance would reach **7895**, with **9/31** requests
exceeding the local scenario, so it is not selected. These are local estimates,
not verified current account quotas. No paid step is proposed.

Run from the project root; only the user initiates generation:

```bash
set -a
source .env
set +a
PYTHONPATH=src .venv/bin/python -m text2sql.development.run generate \
  --run-dir artifacts/development/recovery-calculation004 \
  --allow-api --confirm-free-plan --wait
```

Resume with that exact command/directory. Keep frozen source unchanged while the
run is active. Do not reprepare the existing directory. After all 31 outcomes:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.development.run score \
  --run-dir artifacts/development/recovery-calculation004
```

The single-arm report's selected_arm only identifies the sole candidate; it does
not establish promotion. Apply the paired, cross-family gate in the preregistration
against grounded002 C2 under the new validator, preserving all failures.

For fresh offline preparation, use `scripts/prepare_calculation_experiment.py
--run-dir <new-directory>` with `PYTHONPATH=src .venv/bin/python`. `--mock` performs
all 31 requests with network connections blocked and checks journal idempotence.
The original grounded-v2 builder is retained unchanged.

The existing 31 development and 104 reviewed test questions are all used data.
No untouched holdout is currently reserved. The protocol requires new questions
on independently audited, nonoverlapping database families, separately adjudicated
references, and a frozen candidate/statistical plan before a one-time evaluation.
No final accuracy claim follows from this work.

## Validation results

The final evaluation-v4 completion marker and result/source hashes verify. All 103 current non-source resources also match the historical manifests (`current-data-verification.json`). All original run-file hashes remain unchanged.

All 315 offline tests pass. Compileall and `git diff --check` pass. The full
31-request `recovery-calculation-mock005` rehearsal blocks network connections,
completes every record and leaves the journal unchanged on repeat. The prepared
live run has no requests journal and has made zero calls. `offline-validation-final.json`
records test-source/log hashes and the final candidate/mock artifact hashes.

The revised-policy comparison retains historical scores and changes only newly
accepted saved predictions:

| Source run | C0 | C1 | C2 |
|---|---:|---:|---:|
| Original historical scores | 7/31 | 5/31 | 9/31 |
| Original predictions under validator v2 | 7/31 | 5/31 | 11/31 |
| Grounded002 historical scores | 6/31 | 8/31 | 13/31 |
| Grounded002 predictions under validator v2 | 6/31 | 8/31 | 13/31 |

Original C2 local072 and local270 now score correct; local169 and local336 execute
but mismatch. Original C1 local169 times out. Grounded C0/C1 local070 execute but
mismatch; grounded C0/C2 local169 reach the 60-second timeout. Thus the revised
C2 false rejection is fixed without a correctness gain or security/resource bypass.
The 12 existing executable wrong grounded C2 answers remain. Its other three
rejections are genuine SQL errors, and its two truncations remain failures.
All newly accepted queries retain their exact saved SQL; comment-policy cases
remain rejected.

Under the common new validator, old-versus-grounded C2 is 11 versus 13, not 9
versus 13: gains are local068/167/171 and the loss is local269. This reduces the
net prompt-associated gain from four to two on these reused questions. The
original published 9/31 and 13/31 remain valid historical-policy observations;
neither is overwritten. This is further evidence against attributing every
observed gain solely to semantic reasoning.
