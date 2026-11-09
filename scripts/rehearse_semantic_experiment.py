"""Frozen GEN-001 synthetic generation/resume/scoring rehearsal; zero model calls."""
import argparse
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import socket
import sqlite3
from unittest.mock import patch

from text2sql.semantic.models import (SemanticPlan,TaskContract,loads,dumps,checksum,Step,Input,Expression,Ref,VERSION)
from text2sql.semantic.experiment import (text_hash,prepare_synthetic,generate_synthetic,score_synthetic)
from text2sql.semantic.prompt import plan_prompt
from text2sql.prompting.grounded import INSTRUCTIONS
from text2sql.schema import inspect_sqlite_schema,canonical_schema_sha256
from text2sql.development.run import sha,load_run,write_json
from text2sql.evaluation.gold_results import OfficialGoldResult,GoldResultVariant

ROOT=Path(__file__).resolve().parents[1]


def main(output):
    if output.exists():raise ValueError('Use a fresh directory')
    inputs=output/'inputs';inputs.mkdir(parents=True)
    database=inputs/'synthetic.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript((ROOT/'data/fixtures/semantic-plan-v1/schema.sql').read_text())
    connection.close()
    overflow=inputs/'overflow.sqlite'
    with sqlite3.connect(overflow) as connection:
        connection.executescript('CREATE TABLE events(amount INTEGER NOT NULL); INSERT INTO events VALUES(9223372036854775807),(9223372036854775807);')
    connection.close()
    fixture=ROOT/'data/fixtures/semantic-plan-v1'
    plan=loads((fixture/'plan.json').read_text(),SemanticPlan)
    contract=loads((fixture/'contract.json').read_text(),TaskContract)
    question='Number the distinct insertion days in ascending order.'
    context='\nFull schema: events(id INTEGER PRIMARY KEY NOT NULL,day TEXT,amount INTEGER)\nQuestion: '+json.dumps(question)
    grounded=INSTRUCTIONS+context+'\n\nReturn one read-only SQLite query, SQL only, without comments.\nSQL:'
    def make(identifier,plan,contract,db=database,complete=True,raw=None):
        schema=inspect_sqlite_schema(db)
        provenance={'contract_json':dumps(contract),'contract_sha256':checksum(contract),'question_sha256':text_hash(question),
            'schema_sha256':canonical_schema_sha256(schema),'origin':'synthetic_fixture','author':'synthetic-test-author',
            'reviewer':'synthetic-expected-behavior','reviewed':True,'evidence_scope':'synthetic'}
        return {'id':identifier,'question':question,'prompt':plan_prompt(grounded,contract),'database':str(db.resolve()),
                'provenance':provenance,'response':{'candidates':[raw if raw is not None else dumps(plan)],
                'metadata':{'finish_reason':'stop' if complete else 'length','completion_complete':complete}},
                'input_tokens':2000,'output_tokens':300 if complete else 3072}
    bad=replace(plan,steps=(replace(plan.steps[0],operation='project'),plan.steps[1]))
    cases=[make('valid',plan,contract),make('wrong_grain',bad,contract),make('invalid_json',plan,contract,raw='{}'),
           make('truncated',plan,contract,complete=False),make('result_mismatch',plan,contract)]
    totals=Step('totals','aggregate',(Input('e','table','events'),),
                (Expression('amount','sum',(Ref('e','amount'),)),Expression('n','count',())))
    ratio=Step('answer','project',(Input('t','step','totals'),),
               (Expression('value','ratio',(Ref('t','amount'),Ref('t','n')),zero_denominator='error',unit='fraction'),))
    scalar=SemanticPlan(VERSION,(totals,ratio),'answer',('value',),(),(),'unspecified','scalar')
    scalar_contract=TaskContract(VERSION,('value',),(),(),'unspecified','scalar',(),(),())
    cases.append(make('unsupported_lowering',scalar,scalar_contract))
    total=replace(totals,name='answer',outputs=(Expression('value','sum',(Ref('e','amount'),)),))
    cases.append(make('execution_error',replace(scalar,steps=(total,)),scalar_contract,overflow))
    # These requests are scripted transport tests, not human-reviewed task plans.
    # Full real-input token budgets are audited separately by the preflight script.
    directory=output/'run'
    prepare_synthetic(directory,cases)
    generate_synthetic(directory,stop_after=2)
    assert not (directory/'generation.json').exists()
    generate_synthetic(directory)
    journal_hash=sha(directory/'requests.jsonl')
    generate_synthetic(directory)
    assert sha(directory/'requests.jsonl')==journal_hash
    parent=load_run(ROOT/'artifacts/development/validator-scope-v2/frozen-root',ROOT/'artifacts/development/recovery-grounded002')
    comparator=ROOT/'data/raw/spider2/spider2-lite/evaluation_suite/evaluate_utils.py'
    comparator_hash=parent['identity']['resources'][str(comparator.relative_to(ROOT))]
    expected=inputs/'expected.csv';expected.write_text('day,sequence\n2025-02-01,1\n2025-02-02,2\n')
    wrong=inputs/'different.csv';wrong.write_text('day,sequence\n2099-01-01,1\n')
    gold={}
    for case in cases:
        path=wrong if case['id']=='result_mismatch' else expected
        gold[case['id']]=OfficialGoldResult(case['id'],(GoldResultVariant(path,sha(path),None,None),),False)
    options={'comparator_path':comparator,'expected_comparator_sha256':comparator_hash,
             'gold_resources':{str(p.resolve()):sha(p) for p in (expected,wrong)}}
    score_synthetic(directory,gold,stop_after=2,**options)
    result=score_synthetic(directory,gold,**options)
    score_hash=sha(directory/'scores.json')
    assert score_synthetic(directory,gold,**options)==result
    assert sha(directory/'scores.json')==score_hash and sha(directory/'requests.jsonl')==journal_hash
    assert result['summary']['correct']==1 and result['summary']['total']==7
    assert {r['status'] for r in result['records']}=={'correct','plan_validation_rejected','plan_parse_rejected',
            'generation_failure','result_mismatch','lowering_unsupported','execution_error'}
    write_json(output/'rehearsal.json',{'provider_calls':0,'synthetic_only':True,'generation_resume_verified':True,
        'scoring_resume_verified':True,'repeat_preserves_journal_and_scores':True,'summary':result['summary'],
        'journal_sha256':journal_hash,'scores_sha256':score_hash,'script_sha256':sha(Path(__file__).resolve())})
    print(json.dumps(result['summary'],sort_keys=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')):
        main(args.output_dir.resolve())
