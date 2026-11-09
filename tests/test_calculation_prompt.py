from copy import deepcopy
import unittest
from text2sql.prompting.calculation import calculation_prompt, PLAN
from text2sql.prompting.grounded import INSTRUCTIONS
from text2sql.development.scoring import summarize


class CalculationPromptTest(unittest.TestCase):
    def test_only_instruction_block_changes(self):
        context = '\nSchema: arbitrary\nQuestion: arbitrary\n'
        result = calculation_prompt(INSTRUCTIONS + context)
        self.assertTrue(result.endswith(context))
        self.assertIn(PLAN, result)
        self.assertTrue(result.startswith(INSTRUCTIONS.split('Before writing SQL')[0]))
        self.assertEqual(result[result.index('SQLite capabilities below'):],
                         (INSTRUCTIONS+context)[(INSTRUCTIONS+context).index('SQLite capabilities below'):])
        with self.assertRaises(ValueError):
            calculation_prompt('unknown version')

    def test_single_candidate_summary_counts_failures_without_arm_comparison(self):
        identity = {'config': {'arms': [{'arm':'C2','max_completion_tokens':3072,'reasoning_effort':'medium'}]},
                    'target_families':[{'family_id':'f','databases':['db']}]}
        rows=[{'arm':'C2','example_id':'a','db_id':'db','correct':True,'nonempty_correct':True,'status':'correct'},
              {'arm':'C2','example_id':'b','db_id':'db','correct':False,'nonempty_correct':False,'status':'generation_failure'}]
        result=summarize(rows,identity,[])
        self.assertEqual(result['arms']['C2']['execution_accuracy'],0.5)
        self.assertEqual(result['arms']['C2']['generation_failures'],1)
        self.assertEqual(result['paired_comparisons'],[])
