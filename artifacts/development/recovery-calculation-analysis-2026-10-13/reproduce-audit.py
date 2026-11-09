"""Verify and analyze saved experiment evidence; no generation or rescoring."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / 'scripts'))
from recovery_revision import ARCHIVE, check_evidence, verified
from text2sql.development.run import sha
from text2sql.development.controls import digest
from text2sql.prompting.calculation import calculation_prompt


def main(output):
    check_evidence()
    old_dir = ROOT / 'artifacts/development/recovery-grounded002'
    new_dir = ROOT / 'artifacts/development/recovery-calculation004'
    old_manifest, old_gen, old_scores = verified(ARCHIVE, old_dir)
    new_manifest, new_gen, new_scores = verified(ROOT, new_dir)
    checkpoint = json.loads((new_dir / 'score-checkpoint.json').read_text())
    assert checkpoint['records_sha256'] == digest(checkpoint['records'])
    assert checkpoint['records'] == new_scores['records']
    assert checkpoint['generation_sha256'] == sha(new_dir / 'generation.json')
    evaluation = ROOT / 'artifacts/development/validator-scope-v2/evaluation-v4'
    complete = json.loads((evaluation / 'complete.json').read_text())
    assert complete['policy_sha256'] == sha(evaluation / 'policy.json')
    assert complete['results']['recovery-grounded002'] == sha(evaluation / 'recovery-grounded002.json')
    policy = json.loads((evaluation / 'policy.json').read_text())
    for path, checksum in policy['source_hashes'].items():
        assert sha(ROOT / path) == checksum, path
    control = json.loads((evaluation / 'recovery-grounded002.json').read_text())
    assert control['policy_sha256'] == sha(evaluation / 'policy.json')
    assert control['generation_sha256'] == sha(old_dir / 'generation.json')
    assert control['historical_scores_sha256'] == sha(old_dir / 'scores.json')
    assert control['run_sha256'] == old_manifest['identity_sha256']
    assert old_scores['execution_limits'] == new_scores['execution_limits']
    assert old_scores['comparator_sha256'] == new_scores['comparator_sha256']
    old, new = old_manifest['identity'], new_manifest['identity']
    for field in ('model_id', 'temperature', 'seed', 'execution_limits'):
        assert old['config'][field] == new['config'][field]
    assert new['config']['arms'] == [a for a in old['config']['arms'] if a['arm']=='C2']
    assert old['gold_files'] == new['gold_files']
    assert old['target_families'] == new['target_families']
    for before, after in zip(old['prompts'], new['prompts'], strict=True):
        for field in ('example_id','db_id','database_path','question','documents'):
            assert before[field] == after[field]
        assert calculation_prompt(before['prompt']) == after['prompt']
    for path, checksum in old['resources'].items():
        if not path.startswith('src/'):
            assert sha(ROOT/path) == checksum, path
    baseline = {r['example_id']: r for r in control['records'] if r['arm']=='C2'}
    candidate = {r['example_id']: r for r in new_scores['records']}
    assert set(baseline) == set(candidate) and len(candidate)==31
    gains = [i for i in candidate if candidate[i]['correct'] and not baseline[i]['correct']]
    losses = [i for i in candidate if not candidate[i]['correct'] and baseline[i]['correct']]
    families = {db:f['family_id'] for f in new['target_families'] for db in f['databases']}
    family_net = defaultdict(int)
    for i in gains: family_net[families[candidate[i]['db_id']]] += 1
    for i in losses: family_net[families[candidate[i]['db_id']]] -= 1
    gate = {'net_at_least_three':len(gains)-len(losses)>=3, 'losses_at_most_two':len(losses)<=2,
            'at_least_two_improved_families':sum(v>0 for v in family_net.values())>=2}
    summaries = {}
    for name, rows, generation in [('grounded002',list(baseline.values()),[r for r in old_gen['records'] if r['arm']=='C2']),
                                    ('calculation004',list(candidate.values()),new_gen['records'])]:
        by_db = defaultdict(lambda:{'correct':0,'total':0})
        for row in rows:
            by_db[row['db_id']]['correct'] += int(row['correct'])
            by_db[row['db_id']]['total'] += 1
        summaries[name] = {'correct':sum(r['correct'] for r in rows), 'total':len(rows),
            'nonempty_correct':sum(r['nonempty_correct'] for r in rows),
            'statuses':dict(Counter(r['status'] for r in rows)), 'by_database':dict(by_db),
            'input_tokens':sum(r['input_tokens'] or 0 for r in generation),
            'output_tokens':sum(r['output_tokens'] or 0 for r in generation),
            'reasoning_tokens':sum((r['metadata'].get('completion_tokens_details') or {}).get('reasoning_tokens',0) or 0 for r in generation),
            'finish_reasons':dict(Counter(r['metadata'].get('finish_reason','missing') for r in generation)),
            'failures':[{'key':r['key'],'error_code':r['metadata'].get('error_code')} for r in generation if r['status']!='complete']}
    prompts = {p['example_id']:p for p in new['prompts']}
    predictions = {r['example_id']:r for r in new_gen['records']}
    rejected = []
    for i,row in candidate.items():
        if row['status'] != 'validation_rejected': continue
        sql = predictions[i]['generated_sql']
        item = {'example_id':i, 'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),
                'policy_issues':row['execution']['validation']['issue_codes']}
        with closing(sqlite3.connect((ROOT/prompts[i]['database_path']).resolve().as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('PRAGMA query_only=ON')
            c.set_progress_handler(lambda:1,1000000)
            try:
                c.execute('EXPLAIN '+sql).fetchone()
                item['sqlite_compile']='success'
            except sqlite3.Error as e:
                item.update(sqlite_compile='error',error=str(e))
        rejected.append(item)
    events = [json.loads(line)['data'] for line in (new_dir/'requests.jsonl').read_text().splitlines()]
    reservations = [e for e in events if e['event']=='reserve']
    outcomes = [e for e in events if e['event']=='outcome']
    output_data = {'provider_calls_by_audit':0,'scope':'Saved-score analysis; EXPLAIN only; no prediction modifications or rescoring',
        'summaries':summaries,'gains':gains,'losses':losses,'net_by_family':dict(family_net),
        'promotion_checks':gate,'promote':all(gate.values()),'retained_candidate':'grounded-sqlite-v2 C2',
        'compile_checks':rejected,'reserved_attempts':len(reservations),
        'noncomplete_journal_outcomes':[e for e in outcomes if e['result']['status']!='complete'],
        'per_example':[{'example_id':i,'db_id':candidate[i]['db_id'],'before':baseline[i]['status'],'after':candidate[i]['status']} for i in candidate],
        'hashes':{str(p.relative_to(ROOT)):sha(p) for p in [*(d/f for d in (old_dir,new_dir) for f in ('run.json','generation.json','scores.json','requests.jsonl')),
                  evaluation/'policy.json',evaluation/'complete.json',evaluation/'recovery-grounded002.json',Path(__file__).resolve()]}}
    with output.open('x') as f: json.dump(output_data,f,indent=2); f.write('\n')
    print(json.dumps({'summaries':summaries,'gains':gains,'losses':losses,'promotion_checks':gate,'compile_checks':rejected},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).with_name('audit.json'))
    main(parser.parse_args().output)
