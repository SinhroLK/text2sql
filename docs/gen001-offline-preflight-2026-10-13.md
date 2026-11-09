# GEN-001 offline integration and feasibility

The offline response-processing path and its synthetic generation/resume/scoring
rehearsal are implemented. **The semantic-plan-v1 development experiment is not
ready for model calls.** Coverage, contract provenance, and per-request quota
gates fail. Grounded-sqlite-v2 C2 remains the measured baseline (13/31); no new
model accuracy has been measured.

## Implemented integration

New isolated modules leave completed recovery runners and SEM-002 v1 files intact:

- `semantic/prompt.py`: complete JSON response schema derived from the dataclasses,
  typed-plan instructions, separate contract, and the exact grounded-v2 data block.
  Both old SQL-only instruction boundaries are replaced; schema, sampled values,
  documents and question are retained. No SQL examples or answer hints are added.
- `semantic/experiment.py`: independently bound contract provenance, response
  completion checking, strict JSON parsing, plan validation and deterministic
  lowering. Results distinguish generation failure, plan parse rejection, plan
  validation rejection, unsupported lowering, SQL security rejection, execution
  outcome and correctness. There is no model-SQL fallback or contract override.
- A frozen synthetic runner reuses the existing durable `RequestJournal`, SAFE-002
  executor and production `score_record`. The rehearsal uses the pinned official
  comparator against independently authored synthetic CSV results. All cases count.
  Its transport returns scripted responses only; no live provider path was enabled.

Raw JSON responses remain immutable journal outcomes. Compiled SQL is a separate
derived artifact, never a replacement response. Verification reconstructs raw
records from the journal, rejects edited responses even if their file digest was
recomputed, and requires exact complete coverage before scoring. Checkpoints bind
source/environment/database/contract/response identities, comparator hash,
reference contents and comparison metadata. Scoring resume cannot generate model
responses. Repeating a completed rehearsal leaves journals and scores unchanged.

## Coverage gate: failed

All 31 previously reviewed development questions have hash-bound annotations in
`configs/semantic/development-coverage-v1.json`. These are analysis data, not
per-question generation rules, routing, or answer logic.

| Finding | Questions |
|---|---:|
| Identified missing operations | 29 |
| Interpretation unresolved | 1 |
| Potentially representable, not demonstrated | 1 |
| Demonstrated full-task coverage | 0 |

Recurring gaps include date/gap arithmetic, calendar expansion and interval
joins, window SUM/centered AVG, conditional buckets, descending/top-k selection,
concatenation, recursion, FIFO overlap, weighted regression and pairwise events.
The annotations identify missing operations in straightforward faithful
constructions; they are not mathematical proofs that no equivalent rewrite exists.

local274 is unresolved: the question could require recomputing FIFO allocation
or reporting already stored picks; do not choose the convenient interpretation
without evidence. local354 may fit grouped MIN/MAX and staged equijoins, but has
no independently reviewed contract or full compiled witness yet. Neither is
counted as supported or silently removed from a future denominator.

The audit uses question text and the v1 operator inventory. It does not inspect
saved predictions or reference answer contents. This is not blinded analysis:
the development set was reviewed in earlier work. No contracts were derived from
reference answers, and no benchmark SQL was authored for this audit.

The earlier SEM-002 completion refers to a bounded software contract/checker,
not full benchmark coverage. This audit demonstrates why that distinction matters.

## Contract provenance gate: failed

There are **zero independently reviewed benchmark contracts**. Synthetic contracts
are accepted only by synthetic mode; they cannot pass as benchmark contracts.
The interface binds contract, question and canonical-schema hashes, declared
evidence scope, author, reviewer and review status. Model-authored, unreviewed,
self-reviewed or gold-derived declarations cannot pass the real-contract policy.
These are verifiable bindings and attestations, not proof that a reviewer reasoned
correctly or that declared provenance is truthful.

An agent-authored draft must remain a draft. This work does not falsely mark its
own contracts as independently reviewed and does not implement automatic
question-to-contract extraction.

If human-assisted contracts are used later, label that assistance explicitly.
To isolate the planner/compiler contribution, the comparison should provide the
same reviewed contract information to its SQL baseline. Comparing assisted plans
directly to historical model-only 13/31 would measure a bundle of added human
information and architecture, not a pure planning effect. An automatic contract
stage would instead need its own frozen method, error accounting and call budget.
No reviewer approval is being requested now because the other feasibility gates
already fail.

## Quota gate: failed even before actual contracts

The exact proposed request contains the complete response schema and frozen
v2 context. For this feasibility audit only, the contract is `{}`; the artifacts
are explicitly named **lower-bound**, not runnable requests.

- Same C2 setting: 3072 completion tokens, medium reasoning.
- Response-schema description: **1060 input tokens**.
- Largest request reservation: **8035**, versus the recorded safe **7200 TPM**.
- **11/31** requests exceed that per-request allowance without real contracts.
- All 31 remain in the audit; nothing was pruned or silently shortened.
- The simple two-stage synthetic plan serializes to **260 output tokens**. This
  is not a bound for real tasks; planning output and reasoning share the cap.

Counting uses the pinned local tokenizer, 256-token chat allowance and 15% margin.
Actual contract content and provider/account overhead still need verification.
The total accounting cap passes, which does not solve oversized individual
requests. Waiting for another quota window cannot solve that size problem.
No bigger allowance, paid fallback, upgrade or API diagnostic is proposed.

## Validation evidence

**353/353 offline tests pass**, including 12 new GEN-001 tests. New tests cover
completion/JSON boundaries, immutable raw outcomes, contract override/provenance,
resource drift, partial-generation resume, scoring resume, reference metadata drift,
separate lowering/security errors and request-context preservation.

`semantic-gen001-rehearsal002` completes seven scripted synthetic cases through
all applicable layers: correct, result mismatch, generation failure, invalid JSON,
invalid plan, unsupported lowering and execution error. It interrupts/resumes both
generation and scoring and checks repeat-run journal/score identity. Networking
is blocked. The deliberate 1/7 synthetic outcome is **not model accuracy**.

The first rehearsal found tuple/list normalization differences in saved receipts;
it is preserved as `semantic-gen001-rehearsal001` without a successful rehearsal
receipt. The final runner normalizes checkpoint records, and a regression covers
nonempty comparator condition-column tuples. Preflight001 is also preserved as an
intermediate source snapshot; **preflight002** is the final feasibility artifact.

Both old completed experiments and calculation004 still verify with their original
source/resource rules and journal outcomes. No predictions, references, historical
scores or frozen experimental sources were changed by this work. No model calls,
commits or pushes were made.

Reproduce offline into fresh directories:

```bash
PYTHONPATH=src .venv/bin/python scripts/preflight_semantic_experiment.py \
  --output-dir artifacts/development/semantic-gen001-preflight003

PYTHONPATH=src .venv/bin/python scripts/rehearse_semantic_experiment.py \
  --output-dir artifacts/development/semantic-gen001-rehearsal003

LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONPATH=src .venv/bin/python \
  -m unittest discover -s tests
```

## Decision and next work

GEN-001 remains **IN PROGRESS, live readiness failed**. The synthetic integration
work is complete; a runnable 31-question model experiment is intentionally absent.
Preflight directories contain no `run.json` and cannot be used with the recovery
`generate` command. All finished experiments remain closed.

The next engineering decision is whether to expand the typed language enough to
justify its cost or retain it as a diagnostic prototype. Do not launch repeated
prompt experiments around the current unsupported language. A concrete next
milestone would add general arithmetic/conditional expressions and explicit
window aggregates/date operations, with independent synthetic proofs of their
semantics, then repeat coverage and compact-request quota checks. That expansion
must use a new language version and must not introduce an arbitrary-SQL escape
hatch to manufacture coverage. Contract provenance needs a viable plan before
claiming an automatic end-to-end system.

None of these checks establishes generalization. The existing development and
reviewed test sets remain used data; final evaluation still requires an untouched,
family-audited holdout and a frozen statistical protocol.
