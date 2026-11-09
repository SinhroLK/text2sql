"""Offline recovery revision: archive, validate/rescore, and prepare a focused run.

Never invokes a model provider. Historical verification uses the archived root,
not amended manifests or disabled verification. Run from the repository root.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil

from text2sql.development.controls import Limits, RequestJournal, digest
from text2sql.development.run import load_run, sha, validate_generation, write_json

ROOT = Path.cwd()
BASE = ROOT / 'artifacts/development/validator-scope-v2'
ARCHIVE = BASE / 'frozen-root'
NAMES = ('recovery-live001', 'recovery-grounded002')


def verified(root, source):
    manifest = load_run(root, source)
    identity = manifest['identity']
    with RequestJournal(source / 'requests.jsonl', {'expected_ids': identity['expected_ids'],
                        'run_sha256': manifest['identity_sha256']}, Limits(**identity['limits'])) as journal:
        generation = validate_generation(source, manifest, journal)
    scores = json.loads((source / 'scores.json').read_text())
    if (scores['run_sha256'] != manifest['identity_sha256']
            or scores['generation_sha256'] != sha(source / 'generation.json')
            or [r['key'] for r in scores['records']] != identity['expected_ids']):
        raise ValueError('Historical scores do not match generation/run')
    return manifest, generation, scores


def archive():
    if ARCHIVE.exists():
        raise ValueError('Archive exists; never overwrite historical evidence')
    inputs = {n: verified(ROOT, ROOT / 'artifacts/development' / n) for n in NAMES}
    resources = set().union(*(set(v[0]['identity']['resources']) for v in inputs.values()))
    resources.update(p.relative_to(ROOT).as_posix() for p in (ROOT / 'src').rglob('*.py'))
    for relative in sorted(resources):
        destination = ARCHIVE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Large immutable data are hardlinked; every read rechecks the frozen hash.
        if relative.startswith('data/'):
            os.link(ROOT / relative, destination)
        else:
            shutil.copy2(ROOT / relative, destination)
    evidence = {}
    for name in NAMES:
        source = ROOT / 'artifacts/development' / name
        verified(ARCHIVE, source)
        evidence[name] = {p.name: sha(p) for p in source.iterdir() if p.is_file()}
    write_json(BASE / 'historical-evidence.json', evidence)


def check_evidence():
    evidence = json.loads((BASE / 'historical-evidence.json').read_text())
    for name, files in evidence.items():
        for file, checksum in files.items():
            if sha(ROOT / 'artifacts/development' / name / file) != checksum:
                raise ValueError('Historical artifact changed: ' + name + '/' + file)


def evaluate(output):
    from text2sql.development.scoring import score_record, official_comparator
    from text2sql.evaluation import OfficialGoldResultStore
    from text2sql.execution import SQLiteSandboxExecutor, SandboxPolicy
    from text2sql.security import SQL_VALIDATOR_VERSION, SQLiteSQLValidator
    from text2sql.schema import inspect_sqlite_schema
    check_evidence()
    output.mkdir(exist_ok=False)
    sources = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'src').rglob('*.py')}
    write_json(output / 'policy.json', {'validator_version': SQL_VALIDATOR_VERSION,
               'source_hashes': sources, 'historical_evidence_sha256': sha(BASE / 'historical-evidence.json'),
               'scope': 'All historical rejections revalidated; newly accepted predictions scored unchanged. Other outcomes retained, not reexecuted.'})
    for name in NAMES:
        source = ROOT / 'artifacts/development' / name
        manifest, generation, old = verified(ARCHIVE, source)
        identity = manifest['identity']
        upstream = ROOT / 'data/raw/spider2/spider2-lite/evaluation_suite'
        metadata = upstream / 'gold/spider2lite_eval.jsonl'
        gold = OfficialGoldResultStore.from_official_directory(upstream / 'gold/exec_result', metadata,
            expected_metadata_sha256=identity['resources'][metadata.relative_to(ROOT).as_posix()],
            allowed_example_ids=[p['example_id'] for p in identity['prompts']])
        for prompt in identity['prompts']:
            if [v.path.relative_to(ROOT).as_posix() for v in gold.get(prompt['example_id']).variants] != identity['gold_files'][prompt['example_id']]:
                raise ValueError('Gold inventory changed')
        utils = upstream / 'evaluate_utils.py'
        compare = official_comparator(utils, identity['resources'][utils.relative_to(ROOT).as_posix()])
        executor = SQLiteSandboxExecutor(SandboxPolicy(**identity['config']['execution_limits']))
        prompts = {p['example_id']: p for p in identity['prompts']}
        predictions = {r['key']: r for r in generation['records']}
        records, changes = [], []
        for previous in old['records']:
            result = deepcopy(previous)
            if previous['status'] == 'validation_rejected':
                record = predictions[previous['key']]
                database = ROOT / prompts[record['example_id']]['database_path']
                validation = SQLiteSQLValidator().validate(record['generated_sql'], inspect_sqlite_schema(database))
                change = {'key': record['key'], 'sql_sha256': validation.sql_sha256,
                          'old_issues': previous['execution']['validation']['issue_codes'],
                          'validation': validation.to_dict()}
                if validation.valid:
                    result = score_record(record, database, gold.get(record['example_id']), executor, compare)
                change['new_status'] = result['status']
                changes.append(change)
            records.append(result)
        summary = {a: {'correct': sum(r['correct'] for r in records if r['arm']==a),
                       'total': sum(r['arm']==a for r in records)} for a in ('C0','C1','C2')}
        write_json(output / (name + '.json'), {'run_sha256': manifest['identity_sha256'],
            'generation_sha256': sha(source/'generation.json'), 'historical_scores_sha256': sha(source/'scores.json'),
            'policy_sha256': sha(output/'policy.json'), 'summary': summary, 'changes': changes, 'records': records})
        print(name, summary, [(c['key'],c['new_status']) for c in changes if c['validation']['valid']], flush=True)
    check_evidence()
    if sources != {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'src').rglob('*.py')}:
        raise ValueError('Source changed during evaluation')
    write_json(output / 'complete.json', {'provider_calls': 0, 'policy_sha256': sha(output/'policy.json'),
               'results': {n: sha(output/(n+'.json')) for n in NAMES}})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['archive', 'evaluate', 'verify'])
    parser.add_argument('--output-dir', type=Path, default=BASE/'evaluation-v3')
    args = parser.parse_args()
    if args.action == 'evaluate':
        evaluate(args.output_dir.resolve())
    else:
        {'archive': archive, 'verify': check_evidence}[args.action]()
