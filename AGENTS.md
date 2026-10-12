# Project collaboration preferences

- The user runs all model API requests themselves. Prepare the code and give the user the exact command when an API run is needed; do not initiate model API calls, including free-tier diagnostics, from the agent environment.
- Offline prompt preparation, local database inspection, local scoring, mock rehearsals, and automated tests may run autonomously.
- Notify the user before any proposed paid step. Do not enable billing, paid fallbacks, or account upgrades.
- Preserve completed experiments and their original prompts/results. Use a new version and run directory for changed prompts; never replace old results with edited predictions.
