from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from text2sql.schema.inspector import inspect_sqlite_connection
from text2sql.semantic.models import (Ref,Input,Predicate,Window,Expression,Join,Step,
    SemanticPlan,TaskContract,ColumnMeaning,PopulationRule,Order,VERSION,loads,dumps)
from text2sql.semantic import validate


def col(name,alias='e',source=None):
    return Expression(name,'column',(Ref(alias,source or name),))


def dates():
    dedup=Step('days','distinct',(Input('e','table','events'),),(col('day'),))
    win=Window((),(Ref('d','day'),),True,'rows')
    ordered=Step('ordered','window',(Input('d','step','days'),),
                 (col('day','d'),Expression('sequence','row_number',(),window=win)))
    plan=SemanticPlan(VERSION,(dedup,ordered),'ordered',('day','sequence'),('day',),
                      (Order('day','asc'),),'deterministic','rows')
    contract=TaskContract(VERSION,plan.output_columns,plan.output_grain,plan.order_by,
                          plan.ties,plan.expected_shape,(),(),
                          (ColumnMeaning(Ref('events','day'),'day','value'),))
    return plan,contract


class SemanticPlanTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE events(id INTEGER PRIMARY KEY NOT NULL,day TEXT,amount INT);
          INSERT INTO events VALUES(1,'2025-02-01',5),(2,'2025-02-01',8),(3,'2025-02-02',9);
          CREATE TABLE terms(person TEXT NOT NULL,region TEXT,start TEXT);
          INSERT INTO terms VALUES('p','north','2000-01-01'),('p','south','2010-01-01');
          CREATE TABLE calendar(month TEXT NOT NULL PRIMARY KEY);''')
        self.schema=inspect_sqlite_connection(self.db,'synthetic')

    def codes(self,plan,contract):
        return {i.code for i in validate(plan,self.schema,contract).issues if i.severity=='error'}

    def test_distinct_day_plan_proves_window_order_and_sql_counterexample(self):
        plan,contract=dates()
        result=validate(plan,self.schema,contract)
        self.assertTrue(result.valid,result.issues)
        self.assertFalse(result.sql_authorized)
        self.assertEqual(self.db.execute('SELECT day,ROW_NUMBER() OVER(ORDER BY day) FROM (SELECT DISTINCT day FROM events)').fetchall(),
                         [('2025-02-01',1),('2025-02-02',2)])
        self.assertEqual(len(self.db.execute('SELECT day,ROW_NUMBER() OVER(ORDER BY day) FROM events').fetchall()),3)
        bad=replace(plan,steps=(replace(plan.steps[0],operation='project'),plan.steps[1]))
        self.assertIn('window_order_not_unique',self.codes(bad,contract))

    def test_extra_projected_column_prevents_false_distinct_date_proof(self):
        plan,contract=dates()
        bad=replace(plan,steps=(replace(plan.steps[0],outputs=(col('day'),col('amount'))),plan.steps[1]))
        self.assertIn('window_order_not_unique',self.codes(bad,contract))

    def test_missing_projected_column_is_not_resolved_from_base_table(self):
        plan,contract=dates()
        bad=replace(plan,steps=(plan.steps[0],replace(plan.steps[1],outputs=(col('day','d'),col('amount','d')))))
        self.assertIn('column_out_of_scope',self.codes(bad,contract))

    def test_forward_reference_and_case_collisions(self):
        plan,contract=dates()
        self.assertIn('unknown_or_forward_source',self.codes(replace(plan,steps=tuple(reversed(plan.steps))),contract))
        self.assertIn('duplicate_name',self.codes(replace(plan,steps=(plan.steps[0],replace(plan.steps[1],name='DAYS'))),contract))

    def population_plan(self):
        active=Predicate('active_region',Ref('terms','region'),'eq','south')
        first=Step('first','aggregate',(Input('t','table','terms'),),
                   (col('person','t'),Expression('first_start','min',(Ref('t','start'),))),('person',))
        served=Step('served','distinct',(Input('t','table','terms'),),
                    (col('person','t'),),filters=(replace(active,column=Ref('t','region')),))
        join=Step('answer','join',(Input('f','step','first'),Input('s','step','served')),
                  (col('person','f'),col('first_start','f')),joins=(Join('inner',(Ref('f','person'),),
                  (Ref('s','person'),),'one_to_one','Both inputs have one row per person'),))
        plan=SemanticPlan(VERSION,(first,served,join),'answer',('person','first_start'),('person',),(), 'unspecified','rows')
        contract=TaskContract(VERSION,plan.output_columns,plan.output_grain,(),plan.ties,'rows',(active,),
            (PopulationRule(Ref('first','first_start'),(),('active_region',)),
             PopulationRule(Ref('answer','person'),('active_region',),())),())
        return plan,contract

    def test_first_term_population_separate_from_active_service_filter(self):
        plan,contract=self.population_plan()
        self.assertTrue(validate(plan,self.schema,contract).valid)
        premature=replace(plan.steps[0],filters=plan.steps[1].filters)
        bad=replace(plan,steps=(premature,*plan.steps[1:]))
        self.assertIn('premature_population_filter',self.codes(bad,contract))
        self.assertEqual(self.db.execute('SELECT MIN(start) FROM terms').fetchone()[0],'2000-01-01')
        self.assertEqual(self.db.execute("SELECT MIN(start) FROM terms WHERE region='south'").fetchone()[0],'2010-01-01')

    def test_active_filter_must_reach_final_relation(self):
        plan,contract=self.population_plan()
        bad=replace(plan,steps=(plan.steps[0],replace(plan.steps[1],filters=()),plan.steps[2]))
        self.assertIn('missing_population_filter',self.codes(bad,contract))
        self.assertIn('missing_final_filter',self.codes(bad,contract))

    def test_same_filter_name_cannot_hide_wrong_value_or_column(self):
        plan,contract=self.population_plan()
        for predicate in (replace(plan.steps[1].filters[0],value='north'),
                          replace(plan.steps[1].filters[0],column=Ref('t','person'))):
            bad=replace(plan,steps=(plan.steps[0],replace(plan.steps[1],filters=(predicate,)),plan.steps[2]))
            self.assertIn('filter_definition_mismatch',self.codes(bad,contract))

    def test_join_declared_cardinality_requires_key_evidence(self):
        plan,contract=self.population_plan()
        bad=replace(plan,steps=(plan.steps[0],replace(plan.steps[1],operation='project'),plan.steps[2]))
        self.assertIn('unproved_join_cardinality',self.codes(bad,contract))

    def test_composite_key_is_not_proven_by_one_component(self):
        plan,contract=self.population_plan()
        bad=replace(plan,steps=(plan.steps[0],replace(plan.steps[1],outputs=(col('person','t'),col('region','t'))),plan.steps[2]))
        self.assertIn('unproved_join_cardinality',self.codes(bad,contract))

    def test_date_join_requires_compatible_representations(self):
        left=Step('days','distinct',(Input('e','table','events'),),(col('day'),))
        join=Step('answer','join',(Input('d','step','days'),Input('c','table','calendar')),
                  (col('day','d'),),joins=(Join('inner',(Ref('d','day'),),(Ref('c','month'),),
                  'many_to_one','Calendar month is a non-null key'),))
        plan=SemanticPlan(VERSION,(left,join),'answer',('day',),('day',),(),'unspecified','rows')
        contract=TaskContract(VERSION,('day',),('day',),(),'unspecified','rows',(),(),
                  (ColumnMeaning(Ref('events','day'),'day','value'),ColumnMeaning(Ref('calendar','month'),'month','value')))
        self.assertIn('incompatible_join_representation',self.codes(plan,contract))
        normalized=replace(left,outputs=(Expression('day','date_key',(Ref('e','day'),),date_format='month'),))
        result=validate(replace(plan,steps=(normalized,join)),self.schema,contract)
        self.assertTrue(result.valid,result.issues)
        self.assertIn('date_conversion_requires_sql_check',{i.code for i in result.issues})

    def test_cumulative_values_cannot_be_summed_as_transactions(self):
        expr=Expression('total','sum',(Ref('e','amount'),))
        stage=Step('answer','aggregate',(Input('e','table','events'),),(expr,))
        plan=SemanticPlan(VERSION,(stage,),'answer',('total',),(),(),'unspecified','scalar')
        contract=TaskContract(VERSION,('total',),(),(),'unspecified','scalar',(),(),
                              (ColumnMeaning(Ref('events','amount'),'number','cumulative'),))
        self.assertIn('cumulative_measure_aggregation',self.codes(plan,contract))
        incremental=replace(contract,column_meanings=(replace(contract.column_meanings[0],measure='incremental'),))
        self.assertTrue(validate(plan,self.schema,incremental).valid)

    def test_ratio_requires_scoped_denominator_unit_and_zero_policy(self):
        totals=Step('totals','aggregate',(Input('e','table','events'),),
                    (Expression('amount','sum',(Ref('e','amount'),)),Expression('n','count',())))
        ratio=Expression('mean','ratio',(Ref('t','amount'),Ref('t','n')),zero_denominator='null',unit='fraction')
        final=Step('answer','project',(Input('t','step','totals'),),(ratio,))
        plan=SemanticPlan(VERSION,(totals,final),'answer',('mean',),(),(),'unspecified','scalar')
        contract=TaskContract(VERSION,('mean',),(),(),'unspecified','scalar',(),(),())
        self.assertTrue(validate(plan,self.schema,contract).valid)
        bad=replace(final,outputs=(replace(ratio,zero_denominator='',unit=''),))
        self.assertIn('missing_zero_denominator_policy',self.codes(replace(plan,steps=(totals,bad)),contract))
        missing=replace(final,outputs=(replace(ratio,args=(Ref('t','amount'),Ref('t','absent'))),))
        self.assertIn('column_out_of_scope',self.codes(replace(plan,steps=(totals,missing)),contract))

    def test_grouping_and_scalar_shape_checked(self):
        plan,contract=dates()
        self.assertIn('scalar_shape_not_proven',self.codes(replace(plan,expected_shape='scalar'),replace(contract,expected_shape='scalar')))
        bad=replace(plan.steps[0],operation='aggregate',group_by=())
        self.assertIn('ungrouped_projection',self.codes(replace(plan,steps=(bad,plan.steps[1])),contract))

    def test_output_contract_order_and_ties_cannot_silently_change(self):
        plan,contract=dates()
        self.assertIn('output_contract_mismatch',self.codes(replace(plan,order_by=()),contract))
        self.assertIn('output_order_not_unique',self.codes(replace(plan,order_by=()),contract))

    def test_strict_json_boundary_and_direct_construction(self):
        plan,contract=dates()
        self.assertEqual(loads(dumps(plan),SemanticPlan),plan)
        for text in ('{"version":"a","version":"b"}',dumps(plan).replace('"project"','"project"')[:-1],
                     dumps(plan)[:-1]+',"sql":"DELETE FROM events"}',dumps(plan).replace('"require_unique_order":true','"require_unique_order":1')):
            with self.subTest(text=text[:80]),self.assertRaises(ValueError): loads(text,SemanticPlan)
        with self.assertRaises(ValueError): loads(' '*262145,SemanticPlan)
        with self.assertRaises(ValueError): validate(replace(plan,ties='invalid'),self.schema,contract)
        self.assertIn('unsupported_version',self.codes(replace(plan,version='future'),contract))

    def test_forged_identifiers_and_literals_never_execute_sql(self):
        plan,contract=self.population_plan()
        evil=replace(contract.filters[0],value="south'; DROP TABLE terms; --")
        contract=replace(contract,filters=(evil,))
        active=replace(plan.steps[1],filters=(replace(evil,column=Ref('t','region')),))
        before=self.db.iterdump(); snapshot=list(before)
        with patch('sqlite3.connect',side_effect=AssertionError('validator must not connect')):
            result=validate(replace(plan,steps=(plan.steps[0],active,plan.steps[2])),self.schema,contract)
        self.assertFalse(result.sql_authorized)
        self.assertEqual(list(self.db.iterdump()),snapshot)
        self.assertTrue(result.valid)  # The value is data, not executable syntax.
        broken=replace(active,inputs=(Input('t','table','sqlite_master'),))
        self.assertIn('unknown_or_forward_source',self.codes(replace(plan,steps=(plan.steps[0],broken,plan.steps[2])),contract))

    def test_receipt_binds_plan_contract_schema_and_preserves_unverified_alignment(self):
        plan,contract=dates()
        a=validate(plan,self.schema,contract)
        b=validate(plan,self.schema,replace(contract,ties='all'))
        self.assertEqual(a.plan_sha256,b.plan_sha256)
        self.assertNotEqual(a.contract_sha256,b.contract_sha256)
        self.assertIn('natural_language_alignment_unverified',{i.code for i in a.issues})

    def test_left_join_right_filter_does_not_restrict_preserved_population(self):
        plan,contract=self.population_plan()
        final=replace(plan.steps[2],joins=(replace(plan.steps[2].joins[0],kind='left'),))
        bad=replace(plan,steps=(*plan.steps[:2],final))
        self.assertIn('missing_population_filter',self.codes(bad,contract))
        self.assertIn('missing_final_filter',self.codes(bad,contract))

    def test_unused_step_cannot_satisfy_population_obligations(self):
        plan,contract=dates()
        unused=replace(plan.steps[0],name='disconnected')
        self.assertIn('unused_step',self.codes(replace(plan,steps=(*plan.steps,unused)),contract))

    def test_bad_date_literal_rejected_without_database_access(self):
        plan,contract=dates()
        predicate=Predicate('since',Ref('events','day'),'ge','2025-02-30')
        contract=replace(contract,filters=(predicate,))
        filtered=replace(plan.steps[0],filters=(replace(predicate,column=Ref('e','day')),))
        self.assertIn('incompatible_date_literal',self.codes(replace(plan,steps=(filtered,plan.steps[1])),contract))
        good=replace(predicate,value='2025-02-01')
        contract=replace(contract,filters=(good,))
        filtered=replace(filtered,filters=(replace(good,column=Ref('e','day')),))
        self.assertTrue(validate(replace(plan,steps=(filtered,plan.steps[1])),self.schema,contract).valid)

    def test_contradictory_population_rule_and_unknown_schema_metadata(self):
        plan,contract=self.population_plan()
        rule=PopulationRule(Ref('first','first_start'),('active_region',),('active_region',))
        self.assertIn('contradictory_population_rule',self.codes(plan,replace(contract,populations=(rule,))))
        meaning=ColumnMeaning(Ref('terms','missing'),'day','value')
        self.assertIn('unknown_canonical_column',self.codes(plan,replace(contract,column_meanings=(meaning,))))

    def test_cli_receipt_and_invalid_input_do_not_modify_database(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from text2sql.semantic.__main__ import main
        plan,contract=dates()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); database=root/'fixture.sqlite'
            target=sqlite3.connect(database)
            self.db.backup(target); target.close()
            before=database.read_bytes()
            (root/'plan.json').write_text(dumps(plan))
            (root/'contract.json').write_text(dumps(contract))
            args=['--database',str(database),'--plan',str(root/'plan.json'),'--contract',str(root/'contract.json')]
            out=StringIO()
            with redirect_stdout(out): self.assertEqual(main(args),0)
            self.assertTrue(json.loads(out.getvalue())['valid'])
            self.assertFalse(json.loads(out.getvalue())['sql_authorized'])
            (root/'plan.json').write_text('{"unexpected":"secret-like-value"}')
            out=StringIO()
            with redirect_stdout(out): self.assertEqual(main(args),2)
            self.assertNotIn('secret-like-value',out.getvalue())
            self.assertEqual(database.read_bytes(),before)

    def test_lowering_distinct_days_is_consistent_and_requires_security_validation(self):
        from text2sql.semantic.compile import compile_plan
        plan,contract=dates()
        sql=compile_plan(plan,self.schema,contract)
        self.assertEqual(self.db.execute(sql).fetchall(),[('2025-02-01',1),('2025-02-02',2)])
        bad=replace(plan,steps=(replace(plan.steps[0],operation='project'),plan.steps[1]))
        with self.assertRaises(ValueError): compile_plan(bad,self.schema,contract)
        with patch('text2sql.security.SQLiteSQLValidator.validate',side_effect=RuntimeError('security invoked')):
            with self.assertRaisesRegex(RuntimeError,'security invoked'): compile_plan(plan,self.schema,contract)

    def test_lowering_first_start_preserves_history_before_region_filter(self):
        from text2sql.semantic.compile import compile_plan
        plan,contract=self.population_plan()
        self.assertEqual(self.db.execute(compile_plan(plan,self.schema,contract)).fetchall(),[('p','2000-01-01')])

    def test_lowering_literals_cannot_add_statements(self):
        from text2sql.semantic.compile import compile_plan
        plan,contract=self.population_plan()
        evil=replace(contract.filters[0],value="south'; DELETE FROM terms; --")
        contract=replace(contract,filters=(evil,))
        filtered=replace(plan.steps[1],filters=(replace(evil,column=Ref('t','region')),))
        sql=compile_plan(replace(plan,steps=(plan.steps[0],filtered,plan.steps[2])),self.schema,contract)
        self.assertEqual(self.db.execute(sql).fetchall(),[])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM terms').fetchone(),(2,))

    def test_lowering_schema_qualified_base_cannot_be_shadowed_by_cte(self):
        from text2sql.semantic.compile import compile_plan
        plan,contract=dates()
        first=replace(plan.steps[0],name='events')
        second=replace(plan.steps[1],inputs=(Input('d','step','events'),))
        self.assertEqual(len(self.db.execute(compile_plan(replace(plan,steps=(first,second)),self.schema,contract)).fetchall()),2)
