from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from text2sql.development.controls import Limits, digest
from text2sql.development.prompt_run import prepare_grounded_run
from text2sql.development.run import environment, load_run, sha, write_json

ROOT = Path(__file__).resolve().parents[1]


class GroundedRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'baseline'
        self.source.mkdir()
        self.output = self.root / 'grounded'
        self.db = self.root / 'fixture.sqlite'
        with sqlite3.connect(self.db) as c:
            c.executescript("CREATE TABLE cities(name TEXT, capital INTEGER); INSERT INTO cities VALUES('Example',1);")
        docs = self.root / 'data/raw/spider2/spider2-lite/resource/documents'
        docs.mkdir(parents=True)
        (docs / 'definition.md').write_text('A capital is represented by 1.')
        manifest_path = self.root / 'configs/development/task-documents-v1.json'
        manifest_path.parent.mkdir(parents=True)
        write_json(manifest_path, {'policy_version': 'task-documents-v1', 'documents': {'definition.md': sha(docs/'definition.md')}})
        config = json.loads((ROOT / 'configs/development/free-tier-run-v1.json').read_text())
        self.identity = {'runner_version': 'recovery-free-run-v1', 'environment': environment(),
                         'config': config, 'mock': False, 'expected_ids': ['C0:x','C1:x','C2:x'],
                         'prompts': [{'example_id': 'x', 'db_id': 'fixture', 'question': 'Which cities are capitals?',
                                      'prompt': 'old prompt', 'prompt_sha256': 'old', 'database_path': 'fixture.sqlite',
                                      'documents': [{'name': 'definition.md'}]}],
                         'resources': {'fixture.sqlite': sha(self.db)},
                         'limits': asdict(Limits(186,1500000,7200,180000,2,'3','1','2')),
                         'gold_files': {'x': ['unused-gold.csv']}, 'target_families': []}
        self.save()

    def save(self):
        write_json(self.source/'run.json', {'identity': self.identity, 'identity_sha256': digest(self.identity)})

    def prepare(self, counter=lambda text: len(text)//4):
        with patch('text2sql.development.prompt_run.load_local_counter', return_value=counter):
            return prepare_grounded_run(self.root, self.source, self.output)

    def test_preparation_preserves_baseline_and_model_settings(self):
        original = (self.source/'run.json').read_bytes()
        result = self.prepare()
        self.assertEqual((self.source/'run.json').read_bytes(), original)
        new = result['identity']
        for field in ('model_id', 'arms', 'temperature', 'seed', 'execution_limits'):
            self.assertEqual(new['config'][field], self.identity['config'][field])
        self.assertEqual(new['expected_ids'], self.identity['expected_ids'])
        self.assertEqual(new['prompts'][0]['prompt_version'], 'grounded-sqlite-v2')
        self.assertIn('A capital is represented by 1.', new['prompts'][0]['prompt'])
        self.assertFalse((self.output/'requests.jsonl').exists())
        self.assertEqual(load_run(self.root,self.output), result)
        # Gold file does not exist; preparation cannot have read reference answers.
        self.assertFalse((self.root/'unused-gold.csv').exists())
        audit=json.loads((self.output/'prompt-audit.json').read_text())
        self.assertEqual(audit['provider_calls'],0)
        self.assertFalse(audit['reference_answers_used'])
        with self.assertRaisesRegex(ValueError,'exists'):
            self.prepare()

    def test_oversized_prompt_fails_without_creating_run(self):
        with self.assertRaisesRegex(ValueError,'exceed frozen budgets'):
            self.prepare(counter=lambda text: 100_000)
        self.assertFalse(self.output.exists())

    def test_resource_drift_rejected_before_sampling(self):
        self.db.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Frozen resource changed'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_nonbaseline_source_rejected(self):
        self.identity['config']['prompt_variant']='grounded_sqlite'
        self.save()
        with self.assertRaisesRegex(ValueError,'original simple-schema'):
            self.prepare()


if __name__ == '__main__':
    unittest.main()
