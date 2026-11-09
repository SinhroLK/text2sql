"""Validate a hand-authored plan/contract offline. Exit 0 valid, 1 invalid, 2 input error."""
import argparse
import json
import sqlite3
from pathlib import Path

from text2sql.schema import inspect_sqlite_schema
from .models import SemanticPlan, TaskContract, loads
from .validation import validate


def read(path, model):
    with path.open('rb') as stream:
        data = stream.read(262145)
    if len(data)>262144:
        raise ValueError('Input exceeds 256 KiB')
    return loads(data.decode('utf-8'),model)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--contract',type=Path,required=True)
    parser.add_argument('--emit-sql',action='store_true',help='Lower the supported subset and run SAFE-001; never execute it')
    args=parser.parse_args(argv)
    try:
        plan=read(args.plan,SemanticPlan)
        contract=read(args.contract,TaskContract)
        schema=inspect_sqlite_schema(args.database)
        result=validate(plan,schema,contract)
        payload=result.to_dict()
        if args.emit_sql and result.valid:
            from .compile import compile_plan
            payload['sql']=compile_plan(plan,schema,contract)
    except (ValueError,OSError,UnicodeError,sqlite3.Error):
        # Avoid echoing arbitrary file contents, literals or database diagnostics.
        print(json.dumps({'valid':False,'error':'invalid_input','sql_authorized':False}))
        return 2
    print(json.dumps(payload,sort_keys=True))
    return 0 if result.valid else 1


if __name__=='__main__':
    raise SystemExit(main())
