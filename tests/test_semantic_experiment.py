from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from text2sql.semantic.models import SemanticPlan,TaskContract,loads,dumps,checksum
from text2sql.semantic.prompt import plan_prompt,response_schema
from text2sql.semantic.experiment import (text_hash,verify_contract,process_response,prepare_synthetic,
    generate_synthetic,score_synthetic,load_synthetic,verify_generation)
from text2sql.schema import inspect_sqlite_schema,canonical_schema_sha256
from text2sql.development.controls import digest
from text2sql.development.run import sha
from text2sql.evaluation.gold_results import OfficialGoldResult,GoldResultVariant
from text2sql.prompting.grounded import INSTRUCTIONS

ROOT=Path(__file__).resolve().parents[1]


class SemanticExperimentTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.directory=self.root/'run'
        self.db=self.root/'fixture.sqlite'
        with sqlite3.connect(self.db) as connection:
            connection.executescript((ROOT/'data/fixtures/semantic-plan-v1/schema.sql').read_text())
        self.schema=inspect_sqlite_schema(self.db)
        self.plan=loads((ROOT/'data/fixtures/semantic-plan-v1/plan.json').read_text(),SemanticPlan)
        self.contract=loads((ROOT/'data/fixtures/semantic-plan-v1/contract.json').read_text(),TaskContract)
        self.provenance={'contract_json':dumps(self.contract),'contract_sha256':checksum(self.contract),
            'question_sha256':text_hash('Number distinct days'),'schema_sha256':canonical_schema_sha256(self.schema),
            'origin':'synthetic_fixture','author':'fixture-author','reviewer':'fixture-test','reviewed':True,'evidence_scope':'synthetic'}
        self.response={'candidates':[dumps(self.plan)],'metadata':{'completion_complete':True,'finish_reason':'stop'}}
        self.case={'id':'example','question':'Number distinct days','prompt':'Synthetic request',
                   'database':str(self.db),'provenance':self.provenance,'response':self.response,'input_tokens':50,'output_tokens':500}
        csv=self.root/'expected.csv';csv.write_text('day,sequence\n2025-02-01,1\n2025-02-02,2\n')
        self.gold={'example':OfficialGoldResult('example',(GoldResultVariant(csv,sha(csv),None,None),),False)}
        comparator=self.root/'comparator.py'
        comparator.write_text('def compare_pandas_table(pred, gold, condition_cols, ignore_order):\n    return pred.equals(gold)\n')
        self.score_kwargs={'comparator_path':comparator,'expected_comparator_sha256':sha(comparator),
                           'gold_resources':{str(csv):sha(csv)}}

    def test_single_complete_plan_is_compiled_without_replacing_raw_response(self):
        result=process_response({'status':'complete',**self.response},schema=self.schema,contract=self.contract)
        self.assertEqual(result['status'],'compiled')
        self.assertIn('SELECT',result['generated_sql'])
        self.assertEqual(self.response['candidates'],[dumps(self.plan)])

    def test_truncation_empty_multiple_and_invalid_json_are_separate(self):
        for response in ({'candidates':[dumps(self.plan)],'metadata':{'completion_complete':False,'finish_reason':'length'}},
                         {'candidates':[],'metadata':self.response['metadata']},
                         {'candidates':['a','b'],'metadata':self.response['metadata']}):
            self.assertEqual(process_response({'status':'complete',**response},schema=self.schema,contract=self.contract)['status'],'generation_failure')
        bad={**self.response,'candidates':['```json\n{}\n```']}
        self.assertEqual(process_response({'status':'complete',**bad},schema=self.schema,contract=self.contract)['status'],'plan_parse_rejected')
        self.assertEqual(process_response({'status':'failed','error_code':'retry_limit'},schema=self.schema,contract=self.contract)['error_code'],'retry_limit')

    def test_wrong_grain_and_contract_override_rejected(self):
        wrong=replace(self.plan,steps=(replace(self.plan.steps[0],operation='project'),self.plan.steps[1]))
        response={'status':'complete',**self.response,'candidates':[dumps(wrong)]}
        self.assertEqual(process_response(response,schema=self.schema,contract=self.contract)['status'],'plan_validation_rejected')
        response['candidates']=[json.dumps({'plan':json.loads(dumps(self.plan)),'contract':{}})]
        self.assertEqual(process_response(response,schema=self.schema,contract=self.contract)['status'],'plan_parse_rejected')

    def test_synthetic_contract_cannot_be_used_as_reviewed_benchmark_contract(self):
        with self.assertRaises(ValueError):verify_contract(self.provenance,question=self.case['question'],schema=self.schema)
        for update in ({'reviewed':False},{'schema_sha256':'bad'},{'question_sha256':'bad'},
                       {'contract_sha256':'bad'},{'evidence_scope':'gold'},{'reviewer':''}):
            with self.subTest(update=update),self.assertRaises(ValueError):
                verify_contract({**self.provenance,**update},question=self.case['question'],schema=self.schema,synthetic=True)
        human={**self.provenance,'origin':'human_assisted_reference_free','evidence_scope':'question_schema_documents_values_only'}
        self.assertEqual(verify_contract(human,question=self.case['question'],schema=self.schema),self.contract)
        with self.assertRaises(ValueError):verify_contract({**human,'reviewer':human['author']},question=self.case['question'],schema=self.schema)

    def test_generation_interruption_resume_and_idempotence(self):
        second=deepcopy(self.case);second['id']='other'
        prepare_synthetic(self.directory,[self.case,second])
        self.assertFalse(generate_synthetic(self.directory,stop_after=1))
        self.assertFalse((self.directory/'generation.json').exists())
        self.assertTrue(generate_synthetic(self.directory))
        before=(self.directory/'requests.jsonl').read_bytes()
        generate_synthetic(self.directory)
        self.assertEqual((self.directory/'requests.jsonl').read_bytes(),before)
        self.assertEqual(sum(json.loads(l)['data']['event']=='reserve' for l in before.splitlines()),2)

    def test_frozen_database_and_contract_drift_rejected(self):
        prepare_synthetic(self.directory,[self.case])
        with sqlite3.connect(self.db) as c:c.execute("INSERT INTO events VALUES(4,'2025-03-01',1)")
        with self.assertRaisesRegex(ValueError,'resource changed'):generate_synthetic(self.directory)

    def test_edited_raw_response_rejected_even_with_recomputed_file_digest(self):
        prepare_synthetic(self.directory,[self.case]);generate_synthetic(self.directory)
        path=self.directory/'generation.json';payload=json.loads(path.read_text())
        payload['records'][0]['outcome']['candidates']=['{}'];payload['records_sha256']=digest(payload['records'])
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError,'durable outcome'):
            verify_generation(self.directory,load_synthetic(self.directory))

    def test_scoring_checkpoint_resume_uses_existing_sandbox_and_preserves_failures(self):
        second=deepcopy(self.case);second['id']='bad';second['response']['candidates']=['{}']
        self.gold['bad']=replace(self.gold['example'],example_id='bad')
        prepare_synthetic(self.directory,[self.case,second]);generate_synthetic(self.directory)
        before=(self.directory/'requests.jsonl').read_bytes()
        self.assertIsNone(score_synthetic(self.directory,self.gold,stop_after=1,**self.score_kwargs))
        report=score_synthetic(self.directory,self.gold,**self.score_kwargs)
        self.assertEqual(report['summary']['total'],2)
        self.assertEqual(report['summary']['correct'],1)
        self.assertEqual(report['records'][1]['status'],'plan_parse_rejected')
        self.assertFalse(report['summary']['accuracy_claim_allowed'])
        scores=(self.directory/'scores.json').read_bytes()
        score_synthetic(self.directory,self.gold,**self.score_kwargs)
        self.assertEqual((self.directory/'scores.json').read_bytes(),scores)
        self.assertEqual((self.directory/'requests.jsonl').read_bytes(),before)

    def test_scoring_requires_complete_generation_and_pinned_gold(self):
        prepare_synthetic(self.directory,[self.case]);generate_synthetic(self.directory,stop_after=1)
        with self.assertRaises(FileNotFoundError):score_synthetic(self.directory,self.gold,**self.score_kwargs)
        generate_synthetic(self.directory)
        with self.assertRaisesRegex(ValueError,'Unpinned'):
            score_synthetic(self.directory,self.gold,**{**self.score_kwargs,'gold_resources':{}})
        self.gold['example'].variants[0].path.write_text('day,sequence\nwrong,5\n')
        with self.assertRaisesRegex(ValueError,'resource changed'):score_synthetic(self.directory,self.gold,**self.score_kwargs)

    def test_prompt_has_entire_schema_and_context_without_sql_only_conflict(self):
        suffix='\n\nReturn one read-only SQLite query, SQL only, without comments.\nSQL:'
        context='\nFull schema: full inventory\nDocuments: unchanged\nQuestion: unchanged'
        prompt=plan_prompt(INSTRUCTIONS+context+suffix,self.contract)
        self.assertIn(context,prompt)
        self.assertNotIn('SQL only',prompt)
        self.assertIn(dumps(self.contract),prompt)
        schema=response_schema()
        self.assertFalse(schema['$defs']['SemanticPlan']['additionalProperties'])
        self.assertEqual(set(schema['$defs']['Expression']['properties']),set(json.loads(dumps(self.plan))['steps'][0]['outputs'][0]))
        with self.assertRaises(ValueError):plan_prompt('different input',self.contract)

    def test_nonempty_condition_columns_and_typed_receipts_survive_resume(self):
        variant=replace(self.gold['example'].variants[0],condition_cols=(0,1))
        self.gold['example']=replace(self.gold['example'],variants=(variant,))
        prepare_synthetic(self.directory,[self.case]);generate_synthetic(self.directory)
        first=score_synthetic(self.directory,self.gold,**self.score_kwargs)
        self.assertEqual(score_synthetic(self.directory,self.gold,**self.score_kwargs),first)
        self.gold['example']=replace(self.gold['example'],ignore_order=True)
        with self.assertRaisesRegex(ValueError,'checkpoint'):
            score_synthetic(self.directory,self.gold,**self.score_kwargs)

    def test_lowering_and_security_rejections_never_supply_sql_fallback(self):
        from unittest.mock import patch
        from text2sql.security import SQLiteSQLValidator,SQLValidationError
        rejected=SQLiteSQLValidator().validate('DELETE FROM events',self.schema)
        for error,status in ((ValueError('unsupported'),'lowering_unsupported'),
                             (SQLValidationError(rejected),'sql_validation_rejected')):
            with patch('text2sql.semantic.experiment.compile_plan',side_effect=error):
                result=process_response({'status':'complete',**self.response},schema=self.schema,contract=self.contract)
                self.assertEqual(result['status'],status)
                self.assertIsNone(result['generated_sql'])
