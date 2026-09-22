#!/usr/bin/env bash
# Frozen quota-aware RUN-002 v2 entry point. Run from any directory.
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$PROJECT_ROOT"

if [[ ! -x .venv/bin/python ]]; then
    echo "Missing .venv/bin/python. Install requirements.lock first." >&2
    exit 2
fi

if [[ -f .env ]]; then
    set -a
    # Source only your own trusted .env file.
    source .env
    set +a
fi

mode="${1:-run}"
if [[ "$mode" == "run" || "$mode" == "preflight" ]]; then
    shift || true
else
    echo "Usage: scripts/run_final_evaluation_v2.sh [preflight|run] [options]" >&2
    exit 2
fi

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m pip check
.venv/bin/python -m text2sql.finalization.freeze_v2_cli verify

common_args=(
    --output-root "${FINAL_OUTPUT_ROOT:-artifacts/final/run002-v2}"
    --request-delay-seconds "${FINAL_REQUEST_DELAY_SECONDS:-10}"
    --transient-retry-seconds "${FINAL_TRANSIENT_RETRY_SECONDS:-60}"
)

if [[ "$mode" == "preflight" ]]; then
    exec .venv/bin/python -u -m text2sql.finalization.run_v2_cli \
        --preflight-only "${common_args[@]}" "$@"
fi

: "${GROQ_API_KEY:?Set GROQ_API_KEY in .env or the environment first}"
exec .venv/bin/python -u -m text2sql.finalization.run_v2_cli \
    "${common_args[@]}" "$@"
