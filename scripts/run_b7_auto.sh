#!/usr/bin/env bash
# Run from any directory. Source only your own trusted .env file.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi
: "${GROQ_API_KEY:?Set GROQ_API_KEY in .env or your environment first}"
export PYTHONPATH="${PWD}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec .venv/bin/python -u -m text2sql.refinement.cli run \
    --auto-quota --max-cost-usd "${B7_MAX_COST_USD:-1.00}" \
    --tokens-per-minute "${GROQ_TPM:-8000}" \
    --tokens-per-day "${GROQ_TPD:-200000}" "$@"
