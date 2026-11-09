"""Offline single-factor prompt ablation from frozen grounded002 C2 inputs."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
from unittest.mock import patch

from recovery_revision import ROOT, ARCHIVE, verified, check_evidence
from text2sql.development.controls import digest
from text2sql.development.offline import budget_preflight
from text2sql.development.run import load_run, sha, write_json, generate_run
from text2sql.development.tokenization import load_local_counter, reserve_input_tokens
from text2sql.prompting.calculation import calculation_prompt, PROMPT_VERSION


def prepare(directory, mock=False):
    if directory.exists():
        raise ValueError('Use a fresh run directory')
    check_evidence()
    parent, _, _ = verified(ARCHIVE, ROOT / 'artifacts/development/recovery-grounded002')
    identity = deepcopy(parent['identity'])
    config = identity['config']
    config['arms'] = [a for a in config['arms'] if a['arm']=='C2']
    config.update(plan_id='calculation-plan-v3', prompt_version=PROMPT_VERSION,
        prompt_variant='grounded_calculation',
        prompt_change_scope='Replace only the internal checklist with a concise calculation-plan-and-trace instruction. Exact frozen context retained.',
        selection_protocol='docs/calculation-experiment-2026-10-12.md',
        execution_policy='Generate all 31 C2 records before scoring; compare to saved C2 under validator v2. No paid fallback.',
        completion_budget_decision='Retain 3072/medium; larger allowances are audited offline only.')
    counter = load_local_counter(ROOT / config['tokenizer']['path'])
    audit = []
    for prompt in identity['prompts']:
        before = prompt['prompt']
        after = calculation_prompt(before)
        prompt.update(prompt=after, prompt_version=PROMPT_VERSION,
                      prompt_sha256=hashlib.sha256(after.encode()).hexdigest(),
                      input_token_reservation=reserve_input_tokens(counter(after)))
        audit.append({'example_id': prompt['example_id'], 'old_tokens': counter(before),
                      'new_tokens': counter(after), 'context_unchanged': before[before.index('SQLite capabilities below'):]==after[after.index('SQLite capabilities below'):]})
    budget = budget_preflight(identity['prompts'], config, token_counter=counter)
    if not budget['fits_total_caps'] or budget['requests_exceeding_recorded_quota_scenario']:
        raise ValueError('Candidate exceeds recorded free-tier quota scenario')
    alternatives = {}
    for cap in (3072,4096):
        scenario = deepcopy(config)
        scenario['arms'][0]['max_completion_tokens'] = cap
        checked = budget_preflight(identity['prompts'], scenario, token_counter=counter)
        alternatives[str(cap)] = {k:v for k,v in checked.items() if k!='requests'}
        alternatives[str(cap)]['largest_request'] = max(r['reserved_tokens'] for r in checked['requests'])
    identity['expected_ids'] = ['C2:'+p['example_id'] for p in identity['prompts']]
    identity['mock'] = mock
    identity['prompt_parent_run_sha256'] = parent['identity_sha256']
    identity['resources'].update({p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT/'src/text2sql').rglob('*.py')})
    for name in ('scripts/prepare_calculation_experiment.py','scripts/recovery_revision.py','docs/calculation-experiment-2026-10-12.md'):
        identity['resources'][name] = sha(ROOT/name)
    # Check every unchanged frozen data/config resource against the live root.
    manifest = {'identity': identity, 'identity_sha256': digest(identity)}
    directory.mkdir(parents=True)
    write_json(directory/'run.json',manifest)
    load_run(ROOT,directory)
    write_json(directory/'budget.json',budget)
    write_json(directory/'prompt-audit.json',{'provider_calls':0,'records':audit,'larger_budget_scenarios':alternatives,
               'limitation':'Recorded local quota scenario only; no account/network diagnostic performed.'})
    (directory/'example-prompt.txt').write_text(identity['prompts'][0]['prompt']+'\n')
    print(json.dumps({'run_dir':str(directory),'requests':31,'provider_calls':0,'budget_scenarios':alternatives}))
    if mock:
        generate_run(ROOT,directory)
        checksum = sha(directory/'requests.jsonl')
        generate_run(ROOT,directory)
        assert sha(directory/'requests.jsonl') == checksum


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--mock',action='store_true')
    args=parser.parse_args()
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Networking forbidden in offline preparation')):
        prepare(args.run_dir.resolve(),args.mock)
