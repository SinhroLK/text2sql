"""GEN-001 response processing and frozen synthetic runner; no live provider path.

Raw responses stay in the existing hash-chained journal. Derived SQL never replaces
those responses. Benchmark readiness is a separate coverage/provenance/quota gate.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from text2sql.development.controls import Limits, RequestJournal, digest
from text2sql.development.run import environment, sha, write_json
from text2sql.development.scoring import score_record, official_comparator
from text2sql.execution import SandboxPolicy, SQLiteSandboxExecutor
from text2sql.providers.base import ProviderResponse
from text2sql.schema import canonical_schema_sha256, inspect_sqlite_schema
from text2sql.security import SQLValidationError
from .models import SemanticPlan, TaskContract, loads, checksum
from .validation import validate
from .compile import compile_plan

VERSION='semantic-offline-runner-v1'


def text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def verify_contract(record, *, question, schema, synthetic=False):
    """An attestation is provenance, not proof that the contract is correct.

    Never call a model-generated or assistant-drafted contract independently
    reviewed. Human-assisted experiments must be labelled as such.
    """
    required={'contract_json','contract_sha256','question_sha256','schema_sha256',
              'origin','author','reviewer','reviewed','evidence_scope'}
    if set(record)!=required:
        raise ValueError('Invalid contract provenance fields')
    contract=loads(record['contract_json'],TaskContract)
    if (record['contract_sha256']!=checksum(contract) or record['question_sha256']!=text_hash(question)
            or record['schema_sha256']!=canonical_schema_sha256(schema)):
        raise ValueError('Contract provenance binding mismatch')
    allowed_origin='synthetic_fixture' if synthetic else 'human_assisted_reference_free'
    if (record['origin']!=allowed_origin or record['reviewed'] is not True
            or not isinstance(record['reviewer'],str) or not record['reviewer'].strip()
            or not isinstance(record['author'],str) or not record['author'].strip()
            or record['evidence_scope']!=('synthetic' if synthetic else 'question_schema_documents_values_only')):
        raise ValueError('Missing independent contract review or prohibited evidence scope')
    if not synthetic and record['author']==record['reviewer']:
        raise ValueError('Real task contracts require a distinct reviewer')
    return contract


def process_response(outcome, *, schema, contract):
    result={'status':'generation_failure','generated_sql':None,'sql_sha256':None}
    if outcome.get('status')!='complete':
        result['error_code']=outcome.get('error_code') or outcome.get('metadata',{}).get('error_code','generation_incomplete')
        return result
    metadata=outcome.get('metadata',{})
    if (metadata.get('finish_reason')!='stop' or metadata.get('completion_complete') is not True
            or len(outcome.get('candidates',[]))!=1):
        result['error_code']='incomplete_response'
        return result
    raw=outcome['candidates'][0]
    result['response_sha256']=text_hash(raw)
    try:
        plan=loads(raw,SemanticPlan)
    except ValueError:
        result.update(status='plan_parse_rejected',error_code='invalid_plan_json')
        return result
    receipt=validate(plan,schema,contract)
    result['plan_validation']=receipt.to_dict()
    if not receipt.valid:
        result['status']='plan_validation_rejected'
        return result
    try:
        sql=compile_plan(plan,schema,contract)
    except SQLValidationError as error:
        result.update(status='sql_validation_rejected',issue_codes=[i.code for i in error.result.issues])
        return result
    except ValueError:
        result.update(status='lowering_unsupported',error_code='unsupported_lowering')
        return result
    result.update(status='compiled',generated_sql=sql,sql_sha256=text_hash(sql))
    return result


def runner_sources():
    root=Path(__file__).resolve().parents[3]
    # Bind all executable project source, including the sandbox subprocess and
    # the unmodified journal/scorer. Extra source files are bound in new runs.
    return {str(p.resolve()):sha(p) for p in sorted((root/'src/text2sql').rglob('*.py'))}


def prepare_synthetic(directory: Path, cases: list[dict], *, limits=None):
    """Freeze already-authored synthetic responses. This is not model evidence."""
    if directory.exists(): raise ValueError('Use a fresh directory')
    ids=[c['id'] for c in cases]
    if not ids or len(ids)!=len(set(ids)): raise ValueError('Expected unique case IDs')
    resources=runner_sources()
    for case in cases:
        if set(case)!={'id','question','prompt','database','provenance','response','input_tokens','output_tokens'}:
            raise ValueError('Invalid synthetic case fields')
        database=Path(case['database']).resolve()
        if str(database)!=case['database']: raise ValueError('Database path must be absolute')
        resources[str(database)]=sha(database)
        verify_contract(case['provenance'],question=case['question'],schema=inspect_sqlite_schema(database),synthetic=True)
    limits=limits or Limits(2*len(ids),1_000_000,7200,180000,2,'3','1','2')
    identity={'version':VERSION,'mock':True,'environment':environment(),'cases':cases,'expected_ids':ids,
              'resources':resources,'limits':asdict(limits),'completion_tokens':3072,
              'execution_limits':asdict(SandboxPolicy())}
    directory.mkdir(parents=True)
    write_json(directory/'run.json',{'identity':identity,'identity_sha256':digest(identity)})


def load_synthetic(directory):
    manifest=json.loads((directory/'run.json').read_text())
    identity=manifest['identity']
    if (manifest['identity_sha256']!=digest(identity) or identity['version']!=VERSION
            or identity['mock'] is not True or identity['environment']!=environment()):
        raise ValueError('Frozen synthetic run identity changed')
    for path,checksum in identity['resources'].items():
        if sha(Path(path))!=checksum: raise ValueError('Frozen synthetic resource changed')
    return manifest


def generate_synthetic(directory: Path, *, stop_after=None):
    manifest=load_synthetic(directory)
    identity=manifest['identity']
    virtual=[1_000_000.0]
    records=[]
    with RequestJournal(directory/'requests.jsonl',{'expected_ids':identity['expected_ids'],
         'run_sha256':manifest['identity_sha256']},Limits(**identity['limits']),clock=lambda:virtual[0]) as journal:
        for case in identity['cases']:
            # Advancing a local clock is exclusively a mock pacing mechanism.
            virtual[0]+=86401
            response=case['response']
            outcome=journal.call(case['id'],case['prompt'],input_tokens=case['input_tokens'],
                max_completion_tokens=identity['completion_tokens'],invoke=lambda:ProviderResponse(
                    tuple(response['candidates']),case['input_tokens'],case['output_tokens'],response['metadata']))
            records.append({'id':case['id'],'outcome':outcome})
            if stop_after is not None and len(records)>=stop_after: return False
        payload={'run_sha256':manifest['identity_sha256'],'journal_sha256':sha(directory/'requests.jsonl'),
                 'records':records,'records_sha256':digest(records),'mock':True}
        path=directory/'generation.json'
        if path.exists():
            if json.loads(path.read_text())!=payload: raise ValueError('Saved raw generation differs from journal')
        else: write_json(path,payload)
    return True


def verify_generation(directory, manifest):
    identity=manifest['identity']
    generation=json.loads((directory/'generation.json').read_text())
    if (generation['run_sha256']!=manifest['identity_sha256'] or generation['mock'] is not True
            or generation['journal_sha256']!=sha(directory/'requests.jsonl')
            or generation['records_sha256']!=digest(generation['records'])
            or [r['id'] for r in generation['records']]!=identity['expected_ids']):
        raise ValueError('Raw generation coverage/hash mismatch')
    with RequestJournal(directory/'requests.jsonl',{'expected_ids':identity['expected_ids'],
            'run_sha256':manifest['identity_sha256']},Limits(**identity['limits'])) as journal:
        for case,record in zip(identity['cases'],generation['records'],strict=True):
            attempts=[e for e in journal.events if e['event']=='reserve' and e['example_id']==case['id']]
            if not attempts or any(e['request_sha256']!=digest([case['prompt'],case['input_tokens'],identity['completion_tokens']]) for e in attempts):
                raise ValueError('Raw response has no matching frozen request')
            outcomes=[e['result'] for e in journal.events if e['event']=='outcome' and e['reservation']==attempts[-1]['number']]
            expected=outcomes[-1] if outcomes else {'status':'interrupted','error_code':'ambiguous_call_not_reissued'}
            if record['outcome']!=expected:
                raise ValueError('Raw response differs from durable outcome')
    return generation


def score_synthetic(directory: Path, gold_by_id, *, comparator_path: Path,
                    expected_comparator_sha256: str, gold_resources: dict[str,str], stop_after=None):
    """Reuse production sandbox/scoring with explicit synthetic references.

    Extract only the checksum-verified official comparator function. Reference
    variant contents and comparison metadata are checkpoint-bound.
    """
    manifest=load_synthetic(directory)
    identity=manifest['identity']
    generation=verify_generation(directory,manifest)
    if set(gold_by_id)!=set(identity['expected_ids']): raise ValueError('Reference coverage mismatch')
    for path,checksum in gold_resources.items():
        if sha(Path(path))!=checksum: raise ValueError('Synthetic gold/evaluator resource changed')
    comparator=official_comparator(comparator_path,expected_comparator_sha256)
    reference_inventory={}
    for identifier,gold in gold_by_id.items():
        if gold.example_id!=identifier: raise ValueError('Synthetic reference ID mismatch')
        variants=[]
        for variant in gold.variants:
            path=str(variant.path.resolve())
            if gold_resources.get(path)!=variant.sha256 or sha(variant.path)!=variant.sha256:
                raise ValueError('Unpinned synthetic reference variant')
            variants.append({'path':path,'sha256':variant.sha256,'condition_cols':list(variant.condition_cols) if variant.condition_cols is not None else None})
        reference_inventory[identifier]={'variants':variants,'ignore_order':gold.ignore_order}
    evaluation_identity={'run_sha256':manifest['identity_sha256'],'generation_sha256':sha(directory/'generation.json'),
                         'gold_resources':gold_resources,'comparator_sha256':expected_comparator_sha256,
                         'references':reference_inventory}
    checkpoint=directory/'score-checkpoint.json'
    saved=json.loads(checkpoint.read_text()) if checkpoint.exists() else {'identity':evaluation_identity,'records':[],'records_sha256':digest([])}
    if (saved['identity']!=evaluation_identity or saved['records_sha256']!=digest(saved['records'])
            or [r['id'] for r in saved['records']]!=identity['expected_ids'][:len(saved['records'])]):
        raise ValueError('Score checkpoint identity/coverage/hash mismatch')
    outcomes={r['id']:r['outcome'] for r in generation['records']}
    executor=SQLiteSandboxExecutor(SandboxPolicy(**identity['execution_limits']))
    for case in identity['cases'][len(saved['records']):]:
        database=Path(case['database'])
        schema=inspect_sqlite_schema(database)
        contract=verify_contract(case['provenance'],question=case['question'],schema=schema,synthetic=True)
        processed=process_response(outcomes[case['id']],schema=schema,contract=contract)
        row={'id':case['id'],**processed,'correct':False,'nonempty_correct':False}
        if processed['status']=='compiled':
            record={'key':'P1:'+case['id'],'arm':'P1','example_id':case['id'],'db_id':'synthetic',
                    'status':'complete','generated_sql':processed['generated_sql']}
            scored=score_record(record,database,gold_by_id[case['id']],executor,comparator)
            row.update(status=scored['status'],correct=scored['correct'],nonempty_correct=scored['nonempty_correct'],score=scored)
        # Normalize tuples in typed receipts before checkpoint comparisons.
        row=json.loads(json.dumps(row,allow_nan=False))
        saved['records'].append(row)
        saved['records_sha256']=digest(saved['records'])
        write_json(checkpoint,saved)
        if stop_after is not None and len(saved['records'])>=stop_after: return None
    summary={'mock':True,'accuracy_claim_allowed':False,'total':len(saved['records']),
             'correct':sum(r['correct'] for r in saved['records']),
             'statuses':{s:sum(r['status']==s for r in saved['records']) for s in sorted({r['status'] for r in saved['records']})}}
    result={'identity':evaluation_identity,'summary':summary,'records':saved['records'],'records_sha256':saved['records_sha256']}
    path=directory/'scores.json'
    if path.exists():
        if json.loads(path.read_text())!=result: raise ValueError('Saved scores differ from verified checkpoint')
    else: write_json(path,result)
    return result
