"""Check replay comparisons with explicit synthetic receipt chains."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from llm_circuit_optimiser.frontier.campaign import STAGES, envelope
from llm_circuit_optimiser.frontier.registry import binomial_upper, digest
from llm_circuit_optimiser.frontier.replay import compare_campaign, load_expected, note_sections


FIXTURES = Path(__file__).resolve().parents[1]/'evidence/frontier/replay'


def seal_fixture(fixture):
    fixture = copy.deepcopy(fixture)
    fixture.pop('fixture_sha256', None)
    return {**fixture, 'fixture_sha256': digest(fixture)}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fixture = load_expected(FIXTURES)[0]

    def tearDown(self):
        self.tmp.cleanup()

    def synthetic_replay(self, *, missing_section=False, wrong_role=False, wrong_counts=False, stop=None):
        """Build test evidence only; these bytes are never called historical results."""
        fixture = copy.deepcopy(self.fixture)
        fixture['scope'] = 'Synthetic comparator test.'
        operation_bytes = b'Synthetic operation stream for a comparator test.\n'
        operation = hashlib.sha256(operation_bytes).hexdigest()
        fixture['expected']['operation_sha256'] = operation
        source = b'Synthetic catalog source.\n'
        path = 'src/point_add/frontier_rewrite/catalog.rs'
        fixture['source_files_sha256'] = {path: hashlib.sha256(source).hexdigest()}
        fixture = seal_fixture(fixture)
        directory = self.root/'jobs'/fixture['candidate']
        directory.mkdir(parents=True)
        dependencies = copy.deepcopy(fixture['expected']['required_dependencies'])
        if wrong_role:
            dependencies[0]['role'] = 'source'
        job = {'id': fixture['candidate'], 'base_commit': fixture['base_commit'], 'dependencies': dependencies}
        job_hash = digest(job)
        (directory/'job.json').write_text(json.dumps(envelope({'job': job, 'job_sha256': job_hash})))
        (directory/'ops.bin').write_bytes(operation_bytes)
        source_path = directory/'source'/path
        source_path.parent.mkdir(parents=True)
        source_path.write_bytes(source)
        sections = fixture['expected']['required_note_sections']
        note = '\n\n'.join('## '+section+'\nTest text.' for section in (sections[1:] if missing_section else sections))
        (directory/'note.md').write_text(note)
        public_dependencies = [{'submissionId': item['submission_id'], 'rationale': item['role']+': test source',
                                'evidence': 'Synthetic evidence.'} for item in dependencies]
        (directory/'dependencies.json').write_text(json.dumps(public_dependencies))
        def artifact(name):
            return {'path': name, 'sha256': hashlib.sha256((directory/name).read_bytes()).hexdigest()}
        screen = {**copy.deepcopy(fixture['expected']['screen']), 'operation_sha256': operation,
                  'evaluator_sha256': 'a'*64, 'artifacts': []}
        if wrong_counts:
            screen['counts']['classical_mismatches'] = 1
            screen['passed'] = False
        slot = {'pairs': 20480, 'streams': 2, 'label': 'synthetic-replay-audit', 'alpha': .05/32}
        results = {'materialize': {'artifacts': []},
                   'build': {'evaluator_sha256': 'a'*64, 'artifacts': []},
                   'emit': {'ops_path': 'ops.bin', 'operation_sha256': operation, 'artifacts': [artifact('ops.bin')]},
                   'static_gate': {'passed': True, 'artifacts': []},
                   'develop': {'passed': True, 'artifacts': []}, 'screen': screen,
                   'audit': {**slot, 'operation_sha256': operation, 'failed_inputs': 0,
                             'upper_bound': binomial_upper(0, slot['pairs'], slot['alpha']),
                             'qualified': True, 'artifacts': []},
                   'export': {'operation_sha256': operation, 'note_path': 'note.md', 'dependencies_path': 'dependencies.json',
                              'source_dir': 'source', 'source_hashes': fixture['source_files_sha256'],
                              'artifacts': [artifact('note.md'), artifact('dependencies.json'), artifact('source/'+path)]}}
        previous = job_hash
        for index, stage in enumerate(STAGES[:STAGES.index('export')+1]):
            receipt = envelope({'stage': stage, 'job_sha256': job_hash, 'previous_sha256': previous,
                                'context': {'audit_slot': slot} if stage == 'audit' else {}, 'result': results[stage]})
            (directory/f'{index:02d}-{stage}.json').write_text(json.dumps(receipt))
            previous = receipt['receipt_sha256']
            if stage == stop:
                break
        return fixture, directory

    def test_retained_fixture_counts_and_roles(self):
        fixtures = load_expected(FIXTURES)
        self.assertEqual([f['expected']['screen']['Q'] for f in fixtures], [818, 819])
        self.assertEqual([f['expected']['screen']['T'] for f in fixtures], [9728780, 9718964])
        self.assertEqual({d['role'] for d in fixtures[1]['expected']['required_dependencies']},
                         {'parent', 'lever', 'catalog', 'setting'})
        self.assertTrue(all(len(f['source_records']) == 6 for f in fixtures))
        self.assertNotIn('/Users/', json.dumps(fixtures))
        self.assertNotIn('/private/' + 'tmp/', json.dumps(fixtures))
        summary = json.loads((FIXTURES/'source-check.json').read_text())
        self.assertEqual(summary['replay_status'], 'incomplete')
        self.assertTrue(all(r['archived_operation_bytes']['matches_expected'] for r in summary['records']))

    def test_missing_campaign_is_incomplete(self):
        result = compare_campaign(self.root, FIXTURES)
        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(all(row['missing'] for row in result['results']))

    def test_matching_synthetic_chain_and_artifacts(self):
        fixture, _ = self.synthetic_replay()
        result = compare_campaign(self.root, [fixture])
        self.assertEqual(result['status'], 'matched', result)
        self.assertTrue(all(row['matched'] for row in result['results'][0]['checks']))

    def test_incomplete_chain_does_not_claim_a_match(self):
        fixture, _ = self.synthetic_replay(stop='emit')
        result = compare_campaign(self.root, [fixture])
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('05-screen.json', result['results'][0]['missing'])

    def test_changed_operation_bytes_fail(self):
        fixture, directory = self.synthetic_replay()
        (directory/'ops.bin').write_bytes(b'Different bytes.')
        self.assertEqual(compare_campaign(self.root, [fixture])['status'], 'mismatch')

    def test_missing_operation_bytes_are_incomplete(self):
        fixture, directory = self.synthetic_replay()
        (directory/'ops.bin').unlink()
        self.assertEqual(compare_campaign(self.root, [fixture])['status'], 'incomplete')

    def test_receipt_hash_changes_fail(self):
        fixture, directory = self.synthetic_replay()
        file = directory/'05-screen.json'
        receipt = json.loads(file.read_text())
        receipt['result']['T'] -= 1
        file.write_text(json.dumps(receipt))
        self.assertEqual(compare_campaign(self.root, [fixture])['status'], 'mismatch')

    def test_wrong_screen_counts_fail_even_with_valid_receipt_hashes(self):
        fixture, _ = self.synthetic_replay(wrong_counts=True)
        self.assertEqual(compare_campaign(self.root, [fixture])['status'], 'mismatch')

    def test_missing_note_section_fails(self):
        fixture, _ = self.synthetic_replay(missing_section=True)
        result = compare_campaign(self.root, [fixture])
        self.assertEqual(result['status'], 'mismatch')
        self.assertTrue(any(not item['matched'] and item['check'].startswith('note section:')
                            for item in result['results'][0]['checks']))

    def test_wrong_dependency_role_fails(self):
        fixture, _ = self.synthetic_replay(wrong_role=True)
        result = compare_campaign(self.root, [fixture])
        self.assertEqual(result['status'], 'mismatch')
        self.assertTrue(any(not item['matched'] and item['check'].startswith('typed dependency:')
                            for item in result['results'][0]['checks']))

    def test_fake_headings_in_code_do_not_count(self):
        text = '## Present\n\n```text\n## Hidden\n```\n\n~~~\n## Also hidden\n~~~\n## Last\n'
        self.assertEqual(note_sections(text), ['Present', 'Last'])

    def test_invalid_fixture_and_job_mapping_fail(self):
        changed = copy.deepcopy(self.fixture)
        changed['expected']['screen']['T'] -= 1
        with self.assertRaises(ValueError):
            compare_campaign(self.root, [changed])
        with self.assertRaises(ValueError):
            compare_campaign(self.root, [self.fixture], job_ids={self.fixture['candidate']: '../escape'})
        with self.assertRaises(ValueError):
            compare_campaign(self.root, [self.fixture], job_ids={'unknown': 'job'})


if __name__ == '__main__':
    unittest.main()
