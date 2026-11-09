"""Coverage/provenance and lower-bound quota audit; intentionally no live run."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
from unittest.mock import patch

from text2sql.development.run import load_run,sha,write_json
from text2sql.development.offline import budget_preflight
from text2sql.development.tokenization import load_local_counter
from text2sql.semantic.prompt import plan_prompt,response_schema
from text2sql.semantic.models import SemanticPlan,loads,dumps

ROOT=Path(__file__).resolve().parents[1]


def main(output):
    if output.exists(): raise ValueError('Use a fresh directory')
    parent=ROOT/'artifacts/development/recovery-grounded002'
    archive=ROOT/'artifacts/development/validator-scope-v2/frozen-root'
    manifest=load_run(archive,parent)  # Hash verification, never predictions/gold values.
    identity=manifest['identity']
    # Current data are used for future preparation, so verify them separately.
    for path,checksum in identity['resources'].items():
        if not path.startswith('src/') and sha(ROOT/path)!=checksum:
            raise ValueError('Current non-source input differs from frozen parent')
    coverage_path=ROOT/'configs/semantic/development-coverage-v1.json'
    coverage=json.loads(coverage_path.read_text())
    prompts=identity['prompts'];rows=coverage['records']
    if [r['example_id'] for r in rows]!=[p['example_id'] for p in prompts]: raise ValueError('Coverage inventory mismatch')
    for row,prompt in zip(rows,prompts,strict=True):
        if row['question_sha256']!=hashlib.sha256(prompt['question'].encode()).hexdigest() or row['db_id']!=prompt['db_id']:
            raise ValueError('Coverage annotation question/schema mismatch')
    config=deepcopy(identity['config'])
    config['arms']=[a for a in config['arms'] if a['arm']=='C2']
    counter=load_local_counter(ROOT/config['tokenizer']['path'])
    requests=[{**p,'prompt':plan_prompt(p['prompt'],None)} for p in prompts]
    budget=budget_preflight(requests,config,token_counter=counter)
    fixture=loads((ROOT/'data/fixtures/semantic-plan-v1/plan.json').read_text(),SemanticPlan)
    report={'provider_calls':0,'benchmark_generation':False,'live_run_ready':False,
        'scope':'31 reused development questions; none silently excluded',
        'parent_run_sha256':manifest['identity_sha256'],'coverage':dict(Counter(r['status'] for r in rows)),
        'demonstrated_supported':0,'reviewed_benchmark_contracts':0,
        'contract_provenance_decision':'No synthetic, draft or model-authored contract may be labelled independently reviewed. Human-assisted reference-free contracts require an independent reviewer and bind question/schema/contract hashes. Automatic contract generation is not implemented.',
        'budget_kind':'lower_bound_without_real_contracts_not_runnable',
        'lower_bound_budget':{k:v for k,v in budget.items() if k!='requests'},
        'largest_lower_bound_request':max(r['reserved_tokens'] for r in budget['requests']),
        'response_schema_tokens':counter(json.dumps(response_schema(),sort_keys=True,separators=(',',':'))),
        'synthetic_plan_output_tokens':counter(dumps(fixture)),
        'completion_limit':3072,'reasoning_effort':'medium',
        'blockers':['language coverage unproved/incomplete','no independently reviewed benchmark contracts',
                    'full request budget cannot be certified without actual contracts'],
        'sources':{str(p.relative_to(ROOT)):sha(p) for p in [coverage_path,Path(__file__).resolve(),
                    *sorted((ROOT/'src/text2sql/semantic').glob('*.py'))]}}
    if budget['requests_exceeding_recorded_quota_scenario']:
        report['blockers'].append('even contract-free request lower bound exceeds recorded quota for some questions')
    output.mkdir(parents=True)
    write_json(output/'preflight.json',report)
    write_json(output/'budget-lower-bound.json',budget)
    write_json(output/'requests-lower-bound.json',requests)
    (output/'example-request-lower-bound.txt').write_text(requests[0]['prompt']+'\n')
    # Deliberately no run.json and no API generation entry point.
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Networking forbidden')):
        main(args.output_dir)
