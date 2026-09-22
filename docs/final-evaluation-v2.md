# Quota-aware final evaluation v2

## Purpose

RUN-002 v1 conflated Groq infrastructure failures with model predictions.
Repeated resumes retained 28 provider failures before the issue was diagnosed.
Those checkpoints remain unchanged under `artifacts/final/run002-v1/` and are
not inputs to v2.

RUN-002 v2 repeats all six frozen arms from empty checkpoints with the same
held-out split, prompt methods, model, temperature, seed, output limit,
retrieval index, evaluation resources, and statistical comparisons. Its only
methodological change is the predeclared provider-error policy below.

## Provider-error policy

A prediction is appended only after the provider returns a non-empty candidate.
The pending marker remains associated with the same example until that happens.

- HTTP 429, 408, 409, 425, 5xx, and connection failures are operational events.
  They are written to a separate sanitized operational journal, then the same
  example is retried without a prediction or score.
- The wait uses the greater of Groq's numeric `Retry-After` and parseable
  `x-ratelimit-reset-tokens` values. If neither is available, the frozen
  fallback is 60 seconds.
- Authentication, permission, invalid-request, and other non-retryable errors
  stop the command without writing a prediction. Fix the configuration and run
  the same command again.
- If the process is interrupted with a pending marker, the same example is
  retried on resume. The interruption is recorded in the operational journal
  and is not scored.
- Successful predictions remain append-only and are never reissued.
- Scoring starts only after all six arms have exactly 104 successful prediction
  records.

Operational journals contain sanitized status, category, wait, and reset
metadata. They never contain the API key or raw request/response payload.

## Identity

- Freeze: `run001-final-evaluation-v2`
- Release tag: `text2sql-final-v2`
- Run: `run002-final-evaluation-v2`
- Output: `artifacts/final/run002-v2/`
- Configs: `configs/final/final002-*-test.toml`
- Statistical plan: `configs/final/statistical-plan-v2.toml`

The v1 output directory must not be copied into or used to seed v2.

## Commands

Provider-free validation:

```bash
scripts/run_final_evaluation_v2.sh preflight
```

Start or resume the complete run:

```bash
scripts/run_final_evaluation_v2.sh run
```

The script loads a trusted project `.env`, runs all offline checks, verifies
the v2 release tag and freeze, and uses a 10-second delay between successful
requests. On a quota response it remains active, prints
`run002_v2_provider_retry_wait`, sleeps until the reset, and retries automatically.
Press `Ctrl+C` to stop. Running the same command later resumes from the last
successful checkpoint.

Optional fallback override for transient errors that provide no reset header:

```bash
export FINAL_TRANSIENT_RETRY_SECONDS=300
scripts/run_final_evaluation_v2.sh run
```

This does not override an explicit provider reset duration.
