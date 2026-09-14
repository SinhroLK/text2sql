# Automatic B7 run

Put `GROQ_API_KEY=...` in your own trusted `.env`, then from the project root:

```bash
bash scripts/run_b7_auto.sh
```

The script exports `.env` without printing it, performs preflight, resumes saved
progress, runs all 31 development examples, and automatically scores EVAL-003
into `artifacts/experiments/exp007-b7/report.json`.

Keep the machine awake and process running; use a terminal multiplexer for
long runs. It pauses rather than exits before the next full 7,024-token request
would exceed 90% of the configured rolling minute/day budget. Defaults are
8,000 TPM and 200,000 TPD, matching the published model free tier; actual account
limits may differ. See [Groq rate limits](https://console.groq.com/docs/rate-limits).
Set `GROQ_TPM` and `GROQ_TPD` in `.env` to your account limits. Conservative
accounting can stretch the run across several days.

`quota.jsonl` persists full token reservations, including rejected retries.
Its scope is this run directory, not other programs or prior account usage.
Do not remove it to evade pauses. Server 429 responses also pause using
`retry-after` (one hour if unavailable), then retry the same candidate, up to
100 rejected retries per candidate. SDK retries remain disabled. The original
124-call cap counts logical candidate calls, not extra rejected HTTP attempts;
successful response metadata records `rate_limit_retries`.

`B7_MAX_COST_USD` defaults to `1.00`. Spending caps, credential/service failures,
and checkpoint mismatches stop for inspection instead of looping indefinitely.
The spending estimate is local, not a provider billing guarantee.

Re-run the same command to resume. Do not modify source or configuration during
a run. Interrupting local quota waits is safe because they precede call
reservations. Interrupting an in-flight request or server-429 retry wait leaves
an ambiguous reservation: it is not reissued on restart and counts as a failed
candidate, preserving the existing protection against duplicate billing.
Without `--auto-quota`, the original runner behavior remains unchanged.

This script produces the machine-readable EVAL-003 report; it does not claim
that B7 improved accuracy or edit project completion statuses automatically.
