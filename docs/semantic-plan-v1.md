# SEM-002: typed semantic plans, offline v1

**Follow-up:** [GEN-001 offline integration and feasibility](gen001-offline-preflight-2026-10-13.md) is complete as an audit: synthetic runner works, but benchmark coverage, contract provenance and request-size gates fail. No live run is ready.

Implemented on 2026-10-13 as a separate `text2sql.semantic` package. Grounded v2
C2 remains the measured baseline. No model/provider call, reference-answer access,
benchmark-specific branch, prompt rewrite, or historical rescore is part of this
implementation. The earlier calculation004 internal-planning prompt is a separate,
completed negative experiment.

## Interfaces and trust boundary

`SemanticPlan` is a typed sequence of named relational stages with explicit
inputs, projected expressions, grouping, filters, joins, window keys, output grain,
ordering/ties and expected shape. `TaskContract` is supplied separately: it fixes
output requirements, canonical filter definitions, population/filter placement
rules and declared column representations/measure meanings. Both carry
`semantic-plan-v1` and have deterministic JSON/checksums.

The JSON loader rejects duplicate/unknown keys, wrong types (including booleans
masquerading as integers), unknown enum values and oversized inputs. It limits
input to 256 KiB, arrays to 256 entries and strings to 4096 characters. Direct
Python dataclass construction is revalidated at the public validator boundary.
Model output cannot replace the independently supplied contract through this API.

**The task contract must be reviewed independently of the submitted plan.** If a
model generates both incorrectly, structural validation cannot recover the intended
question meaning. Existing v1 contracts are hand-authored synthetic fixtures, not
31 benchmark contracts. No claim is made that question-to-contract extraction has
been solved. Every validation receipt includes
`natural_language_alignment_unverified`, even for a structurally valid plan.

## What is checked

- Table/column references against the canonical schema, and step inputs against
  earlier steps only. Unprojected base columns cannot leak through a CTE boundary.
  Duplicate aliases and output names, forward/cyclic references and disconnected
  stages are rejected.
- Output columns, entity grain, ordering, ties and scalar shape against the
  separate contract. Grain evidence comes from non-null schema primary keys,
  DISTINCT projections or aggregate grouping, not a model's asserted uniqueness.
- Distinct-day window calculations: partition/order keys must cover a proven
  unique key when `require_unique_order` is requested. DISTINCT(day,amount) does
  not prove one row per day. If uniqueness is not requested, window ties remain
  an explicit warning rather than being assumed safe.
- Filter IDs must match their canonical column lineage, operator and value.
  Population rules require or forbid those filters upstream of a named measure.
  This detects a region filter applied before a first-term aggregate when the
  contract requires full history. A filtered right side of LEFT JOIN does not
  count as filtering the preserved left population.
- Join references and compatible declared representations; claimed many-to-one
  or one-to-one cardinality needs key evidence. Relationship meaning and potential
  multiplication remain review warnings. Names alone do not establish valid joins.
- Day/month/year representations and date literal validity, cumulative measures
  incorrectly summed/averaged as transactions, aggregate stage/group consistency,
  scoped ratio operands, explicit fraction/percent units and zero-denominator policy.

A successful check establishes consistency within these represented constraints,
not that every measure/population/algorithm is correct. In particular, column
meaning and date-format annotations are assertions supplied by the contract, not
inferred database facts. Equal representations do not prove equal units or a valid
business relationship. A scoped denominator is not proof that it is the intended
denominator. The validator cannot infer the meaning of "first", "final root",
"FIFO", or "overtake" from an output label. Such task semantics still need explicit
contracts and coverage before any model experiment.

## Deterministic SQL lowering

`compile_plan(plan, schema, contract)` accepts only valid plans, constructs one
read-only SQLite WITH/SELECT statement, and runs the existing SAFE-001 validator.
It never executes the SQL. Physical tables are qualified with `main` so a CTE name
cannot shadow them. Identifier/literal quoting prevents strings from becoming SQL
syntax. Compilation has no generic SQL-expression escape hatch.

Supported v1 operations: projection, DISTINCT, grouped/global aggregates, binary
inner/left equijoins, literal predicates, MIN/MAX/SUM/AVG/COUNT, ratios, normalized
date keys, ROW_NUMBER and LAG. Window input ordering is ascending; final ordering
can be ascending or descending. COUNT with no operand counts rows; an operand
counts non-null values. A ratio specifies `fraction` or `percent`, and zero returns
NULL or zero. The contract can express error-on-zero, but this lowering rejects it.

Not supported: recursive/hierarchy plans, arbitrary expressions/functions,
non-equality joins, UNION, HAVING, correlated subqueries, descending window keys,
regression forecasting, full FIFO or pairwise event algorithms. There is no silent
fallback to model SQL. Unsupported tasks must be explicitly accounted for in a
future experiment; they cannot be silently dropped from the denominator.

No semantic receipt authorizes database execution (`sql_authorized` is always
false). Use SAFE-002's `validate_and_execute` for the generated SQL. Its independent
validation, read-only snapshot, authorizer and resource limits remain mandatory.
No production pipeline or frozen recovery runner imports this package yet.

## Offline evidence and reproduction

**341/341 tests pass**, including **26 new SEM-002 tests**. Coverage includes
positive and adversarial cases, synthetic SQL counterexamples for duplicate days
and filter-before-aggregation, JSON boundary rejection, scope, join multiplicity,
composite keys, left joins, date joins/literals, cumulative measures, ratios,
output contracts, SQL quoting, physical-table shadowing and mandatory SAFE-001.
A valid receipt also retains the warning about unverified natural-language meaning.

The standalone network-blocked rehearsal uses only the three-row fixture in
`data/fixtures/semantic-plan-v1/`. It produces two ordered distinct days through
SAFE-002 and rejects the corresponding plan without deduplication. The source
database bytes are unchanged. `artifacts/development/semantic-plan-v1-offline001/`
contains receipts, source/fixture hashes and example SQL. These are synthetic
software checks, not a model accuracy experiment.

Run again into a fresh directory:

```bash
PYTHONPATH=src .venv/bin/python scripts/rehearse_semantic_plan.py \
  --output-dir artifacts/development/semantic-plan-v1-offline002

LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONPATH=src .venv/bin/python \
  -m unittest discover -s tests
```

For your own reviewed plan/contract and existing SQLite database:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.semantic \
  --database /path/to/database.sqlite \
  --plan /path/to/plan.json --contract /path/to/contract.json --emit-sql
```

The CLI inspects schema, validates, and optionally emits SQL; it does not execute
that SQL. Exit 0 means a valid supported plan, 1 means a validation rejection,
and 2 means an invalid input or unsupported lowering. Receipts contain plan,
contract and schema hashes. The default output excludes plan literals and SQL.

## Next controlled experiment: design, not authorization to call a model

SEM-002's offline contract/checker is complete for this explicitly bounded v1
language. GEN-001 integration is not complete and no new live run is prepared.
Before preparing it:

1. Audit representation coverage of the already reviewed development questions
   without gold-derived plan construction. Record unsupported algorithms instead
   of inventing unvalidated expression strings or dropping questions.
2. Define how independent task contracts are obtained and reviewed without
   reference answers. Fix the contract provenance and distinguish human assistance
   from model-only performance. Hand-authored contracts would be a different
   intervention from automatically generated contracts; do not conflate them.
3. Integrate one model response containing a typed plan with strict parsing,
   validation and deterministic lowering. Keep grounding/context, model and
   3072/medium settings fixed. Log plan rejection, unsupported lowering, SQL
   validation, execution and correctness separately, with all questions counted.
   No repair, retries beyond the journal policy, or per-question gold-based routing.
4. Count the complete new request/output schema locally and rehearse generation,
   checkpoint/resume and scoring offline before freezing a new run directory.
   Extra planning output consumes the same completion allowance; feasibility of
   grounded v2 does not establish feasibility of this structured response.
5. Preregister a paired comparison against grounded v2 C2 under the same security
   and execution policy, including net gains/losses by family and all overhead.
   The earlier default promotion threshold is net >=3, losses <=2 and improvements
   in >=2 families; freeze the actual protocol before any generation.

There is deliberately no new API command yet: the coverage, contract-provenance
and quota gates above have not been completed. The user still runs every model
request. No paid step is proposed. All existing development and reviewed-test
results remain used data; an independent family-audited untouched evaluation is
still required for a new final accuracy claim.
