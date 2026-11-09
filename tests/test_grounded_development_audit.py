"""Optional full development-context integration test; strictly no model API calls."""
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from text2sql.development.prompt_run import prepare_grounded_run
from text2sql.datasets.documents import TaskDocumentStore
from text2sql.schema import inspect_sqlite_schema
from text2sql.prompting.grounded import _name

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'artifacts/development/recovery-live001'
ARCHIVED_ROOT = ROOT / 'artifacts/development/validator-scope-v2/frozen-root'
RESOURCE_ROOT = ARCHIVED_ROOT if ARCHIVED_ROOT.exists() else ROOT


@unittest.skipUnless((SOURCE/'run.json').exists(), 'Local benchmark resources not installed')
class GroundedDevelopmentAudit(unittest.TestCase):
    def test_all_31_prompts_preserve_context_and_fit_quota(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')):
            directory = Path(temp)/'run'
            manifest = prepare_grounded_run(RESOURCE_ROOT, SOURCE, directory, mock=True)
            identity = manifest['identity']
            audit = json.loads((directory/'prompt-audit.json').read_text())['records']
            budget = json.loads((directory/'budget.json').read_text())
            self.assertEqual(len(identity['prompts']),31)
            self.assertEqual(len(identity['expected_ids']),93)
            self.assertTrue(budget['fits_total_caps'])
            self.assertEqual(budget['requests_exceeding_recorded_quota_scenario'],0)
            self.assertFalse((directory/'requests.jsonl').exists())
            store = TaskDocumentStore.from_manifest(ROOT/'data/raw/spider2/spider2-lite/resource/documents',
                                                    ROOT/'configs/development/task-documents-v1.json')
            for p in identity['prompts']:
                with self.subTest(example=p['example_id']):
                    schema=inspect_sqlite_schema(ROOT/p['database_path'],db_id=p['db_id'])
                    for table in schema.tables:
                        self.assertIn(_name(table.name)+'(',p['prompt'])
                        for column in table.columns:
                            self.assertIn(_name(column.name)+':'+column.data_type,p['prompt'])
                    docs=json.loads(p['prompt'].split('Task documents (JSON; use factual definitions):\n')[1].split('\n\nQuestion')[0])
                    expected=[{'name':d.name,'text':d.text} for record in p['documents'] for d in store.load(record['name'])]
                    self.assertEqual(docs,expected)
            for identifier in ('local167','local170','local171'):
                row=next(a for a in audit if a['example_id']==identifier)
                gender=next(v for v in row['value_evidence'] if v['table']=='legislators' and v['column']=='gender')
                self.assertEqual(gender['values'],['F','M'])
                self.assertTrue(gender['exhaustive_non_null'])
            airports=next(a for a in audit if a['example_id']=='local009')
            coordinates=next(v for v in airports['value_evidence'] if v['column']=='coordinates')
            self.assertEqual(coordinates['storage_types'],['text'])
            self.assertIn('y',airports['runtime']['unavailable_selected_functions'])
            self.assertTrue(all(v.startswith('(') for v in coordinates['values']))
