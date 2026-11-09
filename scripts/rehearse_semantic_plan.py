"""Provider-free synthetic rehearsal. Creates only a fresh audit directory."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
from unittest.mock import patch

from text2sql.semantic import SemanticPlan, TaskContract, loads, validate
from text2sql.semantic.compile import compile_plan
from text2sql.schema import inspect_sqlite_schema
from text2sql.execution import SQLiteSandboxExecutor

ROOT=Path(__file__).resolve().parents[1]


def main(output):
    fixture=ROOT/'data/fixtures/semantic-plan-v1'
    plan=loads((fixture/'plan.json').read_text(),SemanticPlan)
    contract=loads((fixture/'contract.json').read_text(),TaskContract)
    if output.exists(): raise ValueError('Use a fresh output directory')
    with tempfile.TemporaryDirectory() as temp:
        database=Path(temp)/'synthetic.sqlite'
        with sqlite3.connect(database) as connection:
            connection.executescript((fixture/'schema.sql').read_text())
        connection.close()
        before=hashlib.sha256(database.read_bytes()).hexdigest()
        schema=inspect_sqlite_schema(database)
        receipt=validate(plan,schema,contract)
        assert receipt.valid
        sql=compile_plan(plan,schema,contract)
        execution=SQLiteSandboxExecutor().validate_and_execute(database,sql)
        assert execution.succeeded and execution.rows == (('2025-02-01',1),('2025-02-02',2))
        invalid=replace(plan,steps=(replace(plan.steps[0],operation='project'),plan.steps[1]))
        rejection=validate(invalid,schema,contract)
        assert not rejection.valid and 'window_order_not_unique' in {i.code for i in rejection.issues}
        assert hashlib.sha256(database.read_bytes()).hexdigest()==before
    sources=[*sorted((ROOT/'src/text2sql/semantic').glob('*.py')),Path(__file__).resolve(),
             ROOT/'tests/test_semantic_plan.py',*sorted(fixture.iterdir())]
    report={'provider_calls':0,'benchmark_examples':0,'valid_plan':receipt.to_dict(),
            'wrong_grain_plan':rejection.to_dict(),'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),
            'execution':execution.to_dict(),'database_unchanged':True,
            'sources':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    output.mkdir(parents=True)
    (output/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
    (output/'example.sql').write_text(sql+'\n')
    print(json.dumps({'provider_calls':0,'valid_plan':True,'wrong_grain_rejected':True,
                      'sandbox_rows':2,'output_dir':str(output)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')):
        main(args.output_dir)
