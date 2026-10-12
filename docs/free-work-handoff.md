# Offline recovery handoff — 3 October 2026

The local preparation components for the next baseline experiment are implemented and tested.
**Update:** batch CLI integration and end-to-end tests are now complete; use the
[runnable experiment guide](run-recovery-experiment.md). The remaining text records
the earlier component handoff. The experiment itself can use Groq's free tier with pacing and resume, so a
paid upgrade is not required. API usage and spending are separate decisions. See
[roadmap 4.4](project-plan-roadmap.md) for current next steps.
No model request was sent and no new accuracy score was produced. All **266 offline
tests pass**. The final preparation also succeeded with socket connections blocked.
The public tokenizer vocabulary was downloaded separately at no model/API cost.

## Completed work

- **DATA-005:** conservatively exclude six overlapping training databases
  (`baseball_1`, `chinook_1`, `formula_1`, `sakila_1`, `soccer_1`, `store_1`).
  The derived retrieval index retains 6,546 of 7,000 examples; 454 are excluded.
  Both random and similarity selection operate on the filtered index. The original
  index is unchanged. Policy checks reject changed audit evidence or source entries.
  The policy also defines 28 conservative reporting families across 30 target databases.
- **DATA-004:** version the 31-example development scope and explicitly label a
  later evaluation on the reviewed 104 examples as a **reused-set evaluation**.
  It cannot support an untouched-test generalization claim. Selection uses development
  outcomes only; original results and failed attempts remain retained.
- **SEM-001:** build 155 paired saved development records across B1/B2/B4/B5/B6R,
  with separate diagnostics for the 104 reviewed B6R test records. Twelve development
  cases have manual SQL-hash-bound annotations. Other labels distinguish observed
  execution errors, suspected truncation, and unclassified semantic mismatches.
  These are initial diagnostics, not an exhaustive proof of each root cause.
- **OPS-001, offline portion:** durable reservations before requests; call/token/cost
  caps; rolling quota deferral; process locking; hash-chain verification; response
  replay; no reissue after ambiguous failure/interruption; at most one retry after
  an explicit HTTP 429 rejection, with a new reservation. A provider adapter connects
  these controls to the pipeline. Mock integration tests verify generation and resume.
- **EXP-002 preparation:** prepare all 31 full-context prompts and three fixed
  comparison configurations. Retrieval stays disabled to isolate the baseline changes.
  Installed package versions, runtime details, resource/source hashes, and artifact
  checksums are recorded. This environment snapshot is not a new portable dependency lock.

Saved development scores remain B1 5/31, B2 2/31, B4 5/31, B5 4/31, and B6R 6/31.
They are historical outcomes, not measurements of the new code.

## Proposed model experiment — disabled

| Arm | Completion limit | Reasoning effort | Comparison |
|---|---:|---|---|
| C0 | 1,024 | low | Corrected compact-schema baseline with required documents |
| C1 | 3,072 | low | Change completion limit only |
| C2 | 3,072 | medium | Change reasoning effort only, relative to C1 |

Model, temperature, seed, prompt, input scope, and document availability are fixed.
The initial 4,096-token proposal exceeded the recorded quota scenario on two prompts;
3,072 fits while preserving all schema and document context. This decision was made
before new outcomes existed.

The draft limits are **93 initial requests**, at most **186 attempts**, **1.5 million
reserved tokens**, and a **$3 total cap**. This cap is a proposal, not authorization.
Retry policy applies only to known HTTP 429 rejections, never to a wrong answer,
incomplete completion, timeout, or ambiguous server failure.

The local token screen uses the checksum-pinned o200k ordinary-text vocabulary,
plus 256 tokens for request overhead and a 15% margin. OpenAI's public
[gpt-oss tokenizer implementation](https://github.com/openai/gpt-oss/blob/main/gpt_oss/tokenizer.py)
and [tiktoken encoding definitions](https://github.com/openai/tiktoken/blob/main/tiktoken_ext/openai_public.py)
document the encoding. The provider's exact chat template is still unverified.

Under the recorded safe quota scenario (7,200 tokens/minute and 180,000/day), no
planned request is oversized. The full two-attempt envelope reserves 865,466 tokens
and $1.309882 **at the artificial planning ceilings of $1/$2 per million input/output
tokens**. These are not provider prices or a billing quote. The envelope exceeds one
day's recorded quota, so scheduling can extend across days; limits are local to this
run and do not account for other clients sharing the account.

Selection is predeclared: official development accuracy first; promotion requires
at least three net gains, at most two lost examples, and gains across at least two
database families. Ties favor lower cost/budget/effort. No outcome-selected replacement
runs. This is an engineering selection rule on an already used development set,
not a claim of statistical significance.

## Reproduce and inspect

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.development.offline \
  --output-dir artifacts/development/recovery003-offline
```

Use a new directory. Prerequisites are the existing pinned datasets/artifacts and
the public vocabulary at `data/raw/tokenizers/o200k_base.tiktoken`; its download URL
and SHA-256 are in [the ablation plan](../configs/development/baseline-ablation-v1.json).
Missing or changed vocabulary fails locally; this command never downloads it.

The completed [recovery002 directory](../artifacts/development/recovery002-offline/preflight.json)
contains prompts, the filtered-index manifest, budget estimates, separate development
and reviewed-test analysis JSONL, an installed-package snapshot, and artifact hashes.
Analysis records never enter generation prompts. They do not load new gold resources
or re-execute saved prediction SQL. Generated artifacts are gitignored.

## Boundary before live API work

The next measurement requires live model requests, which can run on the Free plan.
Groq lists 30 requests/minute, 1,000 requests/day, 8,000 tokens/minute and 200,000
tokens/day for this model; actual organization limits must be checked.
[Official limits](https://console.groq.com/docs/rate-limits).
**Requests have not started.** Both
the protocol and ablation plan keep `paid_runs_enabled: false`; the offline command
has no live mode. The provider adapter rejects non-mock providers without explicit
authorization and confirmation of account limits/pricing, and rejects hidden retries.

First finish and test the runnable batch CLI, including checkpoint/resume and
separate scoring, and provide user-facing commands. Before a live run: verify the
Free account limits, confirm the
provider request overhead/reservation policy, and freeze a compatible live-run
identity and output directory with the authorized cap. The journal/control API is
tested; it is not evidence of account-wide quota enforcement or a completed live
batch. The earlier wording conflated live requests with paid work. Default to the
Free plan, pausing for quota instead of upgrading. The $3 draft is not a required
payment. Notify the user and obtain separate authorization if actual spending or
a paid upgrade is proposed. Existing enable flags are unchanged by this clarification.

After measuring C0/C1/C2, use the errors to guide the semantic planner, value and
join improvements, and bounded repair. Their implementation can be local, but their
benefit requires separately budgeted model experiments. Those later phases have
not been marked complete merely because the preparation passes.
