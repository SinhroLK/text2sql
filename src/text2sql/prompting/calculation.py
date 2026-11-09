"""A single instruction-block ablation; context is copied unchanged from v2."""
from text2sql.prompting.grounded import INSTRUCTIONS

PROMPT_VERSION = 'grounded-calculation-v3'
START = 'Before writing SQL, check these requirements internally:'
END = 'SQLite capabilities below describe this runtime.'
PLAN = '''Before writing SQL, form a concise internal calculation plan:
- State the requested output grain and the population being measured. Map each requested measure to its definition, inputs, units, denominator, and time/order keys.
- Arrange the necessary calculations in dependency order. Track the row grain and exposed columns at each step, and how joins or filters change that population.
- Translate that plan into SQLite CTEs. Use the supplied stored representations and runtime capabilities; preserve the required ordering and interval boundaries.
Before returning SQL, trace each requested measure through those CTEs. Check that the SQL implements the plan, join keys have compatible representations, columns remain in scope, and functions and aggregate/window combinations are supported. Check one small boundary case mentally. Keep planning brief to leave room for complete SQL; output only the SQL.

'''


def calculation_prompt(original: str) -> str:
    if not original.startswith(INSTRUCTIONS):
        raise ValueError('Expected exact grounded v2 instruction prefix')
    start, end = original.index(START), original.index(END)
    return original[:start] + PLAN + original[end:]
