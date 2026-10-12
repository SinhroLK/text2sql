import hashlib,json,sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from text2sql.development.run import load_run,sha
from text2sql.development.controls import digest
root=Path.cwd();source=root/'artifacts/development/recovery-live001';out=root/'artifacts/development/recovery-error-analysis-2026-10-11'
m=load_run(root,source);i=m['identity'];g=json.loads((source/'generation.json').read_text());s=json.loads((source/'scores.json').read_text())
assert s['generation_sha256']==sha(source/'generation.json')
assert s['run_sha256']==m['identity_sha256']==g['run_sha256']
assert g['records_sha256']==digest(g['records'])
assert [r['key'] for r in s['records']]==i['expected_ids']
bykey={r['key']:r for r in g['records']};prompts={p['example_id']:p for p in i['prompts']}
rows=[]
for r in s['records']:
 sql=bykey[r['key']]['generated_sql']
 row={'key':r['key'],'db_id':r['db_id'],'status':r['status'],'correct':r['correct'],
      'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),
      'validation_issues':r.get('execution',{}).get('validation',{}).get('issue_codes',[])}
 if r['status']=='validation_rejected':
  db=root/prompts[r['example_id']]['database_path']
  with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as c:
   c.execute('PRAGMA query_only=ON');c.set_progress_handler(lambda:1,1000000)
   try:c.execute('EXPLAIN '+sql).fetchone();row['sqlite_compile']='success'
   except sqlite3.Error as error:row.update(sqlite_compile='error',sqlite_compile_error=str(error)[:500])
 rows.append(row)
summary={arm:{'statuses':dict(Counter(r['status'] for r in rows if r['key'].startswith(arm+':'))),
              'rejected_but_compiles':sum(r.get('sqlite_compile')=='success' for r in rows if r['key'].startswith(arm+':'))}
         for arm in ['C0','C1','C2']}
newpath=root/'artifacts/development/recovery-grounded002/requests.jsonl'
raw=newpath.read_bytes();lines=raw.splitlines(keepends=True)
if lines and not lines[-1].endswith(b'\n'):lines.pop()
prefix=b''.join(lines);events=[json.loads(l)['data'] for l in lines]
res={e['number']:e for e in events if e['event']=='reserve'}
new_outcomes=[]
for e in events:
 if e['event']!='outcome':continue
 r=e['result'];md=r.get('metadata',{})
 new_outcomes.append({'key':res[e['reservation']]['example_id'],'status':r['status'],
                      'finish_reason':md.get('finish_reason'),'error_code':md.get('error_code',r.get('error_code')),
                      'output_tokens':r.get('output_tokens'),
                      'reasoning_tokens':md.get('completion_tokens_details',{}).get('reasoning_tokens'),
                      'output_cap':md.get('max_tokens')})
result={'analysis_only':True,'provider_calls':0,'prediction_execution':'EXPLAIN only for rejected original queries; no predictions rescored',
        'source_hashes':{name:sha(source/name) for name in ['run.json','generation.json','scores.json','requests.jsonl']},
        'summary':summary,'records':rows,
        'new_run_snapshot':{'path':str(newpath.relative_to(root)), 'prefix_bytes':len(prefix),
                            'prefix_sha256':hashlib.sha256(prefix).hexdigest(),'outcomes':new_outcomes,
                            'limitation':'Partial generation snapshot, not a score; active run untouched.'}}
(out/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(summary));print('new snapshot',len(new_outcomes),dict(Counter(r['status'] for r in new_outcomes)))
