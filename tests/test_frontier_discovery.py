"""Test source discovery with immutable Git objects and retained source bytes."""
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

from llm_circuit_optimiser.frontier.discovery import (
    NEW_GUARD, OLD_GUARD, analyze_sources, discover_trees, fit_cost, inspect_source,
    parse_reliability, propose_addendum_jobs, propose_setting_jobs, read_source,
)
from llm_circuit_optimiser.grammar import compose
from llm_circuit_optimiser.frontier.materialize import materialize
from llm_circuit_optimiser.frontier.reliability import rank_parent_reliability


FIXTURES = Path(__file__).parent/'fixtures/frontier/discovery'


def carry_records():
    rows = json.loads((FIXTURES/'carry-lane-rows.json').read_text())
    records, files_by_commit = [], {}
    for row in rows:
        commit = row['submissionCommitSha']
        manifest = json.loads((FIXTURES/commit[:8]/'manifest.json').read_text())
        files = {}
        for path, entry in manifest['files'].items():
            raw = (FIXTURES/entry['fixture']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise AssertionError('Retained source fixture changed')
            files[path] = raw
        files_by_commit[commit] = files
        source = inspect_source(files, commit)
        records.append({'submission_id': row['id'], 'commit': commit,
                        'Q': row['officialMetrics']['qubits'], 'T': row['officialMetrics']['toffoli'],
                        'user': row['solverUsername'], 'created': row['createdAt'], 'source': source})
    return records, files_by_commit


class DiscoverySourceTests(unittest.TestCase):
    def test_real_carry_pair_finds_dial_and_records_confounds(self):
        records, _ = carry_records()
        trees = analyze_sources(records)
        self.assertEqual(len(trees), 1)
        tree = trees[0]
        self.assertEqual([d['name'] for d in tree['dials']], ['GPOOL'])
        dial = tree['dials'][0]
        self.assertEqual(dial['observed_values'], [0, 10])
        self.assertEqual(dial['Q_fit']['intercept'], 818)
        self.assertEqual(dial['Q_fit']['slope'], 1)
        self.assertEqual(dial['Q_fit']['maximum_absolute_residual'], 0)
        self.assertTrue(any('Compiler policies' in r for r in dial['confounders']))
        self.assertTrue(any('S5 guard' in r for r in dial['confounders']))
        self.assertTrue(any('Nonce or tail' in r for r in dial['confounders']))
        nonce = next(c for c in tree['excluded_constants'] if c['name'] == 'NONCE')
        self.assertEqual(nonce['values'], [0, 1])

    def test_carry_jobs_preserve_nonce_and_policy_and_apply_guard(self):
        records, source_files = carry_records()
        trees = analyze_sources(records)
        jobs = propose_setting_jobs(trees, owner='test', limit=20)
        self.assertEqual(len(jobs), 10)
        job = next(j for j in jobs if j['Q'] == 823)
        self.assertIsNone(job['expected_T_inference'])
        self.assertTrue(job['metadata']['admission_required'])
        self.assertEqual(job['metadata']['setting'], {'name': 'GPOOL', 'value': 5})
        self.assertEqual(len(job['edits']), 2)
        self.assertEqual({d['role'] for d in job['dependencies']}, {'parent', 'setting'})
        self.assertEqual(job['edits'][1]['old'], OLD_GUARD)
        self.assertEqual(job['edits'][1]['new'], NEW_GUARD)
        files = copy.deepcopy(source_files[job['base_commit']])
        for edit in job['edits']:
            old, new = edit['old'].encode(), edit['new'].encode()
            self.assertEqual(files[edit['path']].count(old), 1)
            files[edit['path']] = files[edit['path']].replace(old, new)
        updated = inspect_source(files, job['base_commit'])
        base = trees[0]['base']['source']
        self.assertEqual([c for c in base['constants'] if c['excluded']][0]['value'], 0)
        self.assertEqual([c['value'] for c in updated['constants'] if c['excluded']],
                         [c['value'] for c in base['constants'] if c['excluded']])
        self.assertEqual(updated['compiler_policies'], base['compiler_policies'])
        self.assertEqual(next(c for c in updated['constants'] if c['name'] == 'GPOOL')['value'], 5)

    def test_nonliteral_change_splits_families(self):
        records, sources = carry_records()
        changed = copy.deepcopy(records[1])
        files = copy.deepcopy(sources[changed['commit']])
        files['src/point_add/mod.rs'] += b'\npub fn unrelated_algorithm() {}\n'
        changed['source'] = inspect_source(files, changed['commit'])
        trees = analyze_sources([records[0], changed])
        self.assertEqual(len(trees), 2)
        self.assertTrue(all(not tree['dials'] for tree in trees))

    def test_nonce_only_change_never_becomes_a_dial(self):
        path = 'src/point_add/mod.rs'
        files = {path: b'pub const NONCE: usize = 0;\npub const TAIL_SEED: u64 = 7;\n'}
        sources = [inspect_source(files, 'a'*40), inspect_source({path: files[path].replace(b'= 0;', b'= 1;')}, 'b'*40)]
        records = [{'source': source, 'commit': source['commit'], 'submission_id': str(i)*8,
                    'Q': 100, 'T': 1000, 'created': ''} for i, source in enumerate(sources, 1)]
        trees = analyze_sources(records)
        self.assertEqual(len(trees), 1)
        self.assertEqual(trees[0]['dials'], [])
        self.assertEqual(propose_setting_jobs(trees), [])

    def test_unknown_constant_requires_semantic_mapping(self):
        path = 'src/point_add/mod.rs'
        records = []
        for value in (5, 6):
            source = inspect_source({path: f'pub const ROUNDS: usize = {value};\n'.encode()}, str(value)*40)
            records.append({'source': source, 'commit': source['commit'], 'submission_id': str(value)*8,
                            'Q': 100+value, 'T': 1000-value, 'created': ''})
        trees = analyze_sources(records)
        self.assertEqual(trees[0]['dials'][0]['classification'], 'unmapped_integer')
        self.assertEqual(propose_setting_jobs(trees), [])

    def test_repeated_declarations_cannot_create_an_ambiguous_counted_edit(self):
        path = 'src/point_add/mod.rs'
        records = []
        for value in (1, 3):
            raw = (f'mod first {{\nconst POOL_1: usize = {value};\n}}\n'
                   'mod second {\nconst POOL_1: usize = 1;\n}\n').encode()
            source = inspect_source({path: raw}, str(value)*40)
            records.append({'source': source, 'commit': source['commit'], 'submission_id': str(value)*8,
                            'Q': 100+value, 'T': 1000-value, 'created': ''})
        trees = analyze_sources(records)
        self.assertEqual(len(trees[0]['dials']), 1)
        self.assertEqual(propose_setting_jobs(trees), [])

    def test_comments_and_strings_cannot_create_constants(self):
        raw = b'''/* outer
/* inner */
pub const COMMENT_FAKE: usize = 12;
*/
const TEXT: &str = r#"
pub const STRING_FAKE: usize = 13;
"#;
// pub const LINE_FAKE: usize = 14;
pub const POOL_1: usize = 0x10;
'''
        source = inspect_source({'src/point_add/mod.rs': raw}, 'a'*40)
        self.assertEqual([(r['name'], r['value']) for r in source['constants']], [('POOL_1', 16)])

    def test_cost_fit_preserves_residuals_and_requires_two_values(self):
        points = [{'value': 2, 'Q': 102, 'T': 900, 'commit': 'a'*40, 'submission_id': '1'*8}]
        self.assertEqual(fit_cost(points, 'T')['status'], 'insufficient_distinct_settings')
        duplicate = {**points[0], 'T': 899, 'commit': 'b'*40}
        self.assertEqual(fit_cost(points+[duplicate], 'T')['status'], 'insufficient_distinct_settings')
        rows = [{**points[0], 'value': v, 'T': t} for v, t in [(0, 10), (1, 8), (2, 7)]]
        result = fit_cost(rows, 'T')
        self.assertEqual(result['points'], 3)
        self.assertGreater(result['maximum_absolute_residual'], 0)
        self.assertEqual(len(result['residuals']), 3)

    def test_note_rate_is_labelled_as_an_unverified_poisson_model(self):
        results = parse_reliability('Measured lambda = 0.27 per 9,024 shots.', source='note-id')
        self.assertEqual(results[0]['lambda_range'], [.27, .27])
        self.assertEqual(results[0]['packet_shots'], 9024)
        self.assertIn('unverified', results[0]['scope'])
        self.assertIn('Poisson', results[0]['model'])
        self.assertEqual(results[0]['source'], 'note-id')
        unknown = parse_reliability('lambda = 4 per input.', source='note-id')
        self.assertIsNone(unknown[0]['packet_shots'])
        self.assertIsNone(unknown[0]['pass_chance_poisson_approximation'])
        ranged = parse_reliability('3.1-3.7 failing shots per 9,024', source='note-id')
        self.assertEqual(ranged[0]['lambda_range'], [3.1, 3.7])


class DiscoveryGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root/'source'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture author')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo/'src/point_add').mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.STDOUT).decode().strip()

    def commit(self, value, *, suffix=''):
        path = self.repo/'src/point_add/mod.rs'
        path.write_text(f'pub const POOL_1: usize = {value};\npub const NONCE: usize = 0;\n'+suffix)
        self.git('add', '.')
        self.git('commit', '-qm', f'Fixture setting {value}')
        return self.git('rev-parse', 'HEAD')

    def test_end_to_end_git_census_addendum_and_jobs(self):
        commits = [self.commit(value) for value in (1, 3)]
        rows = [{'id': f'{i:08x}', 'commit': commit, 'Q': 100+value, 'T': 1000-10*value,
                 'note': 'lambda = 0.2 per 9024 shots.'}
                for i, (commit, value) in enumerate(zip(commits, (1, 3)), 1)]
        result = discover_trees(rows, self.repo, self.root/'discovery')
        self.assertEqual(len(result['trees']), 1)
        self.assertEqual(len(result['jobs']), 2)
        job = next(j for j in result['jobs'] if j['Q'] == 102)
        self.assertEqual(job['expected_T_inference'], 980)
        self.assertEqual(job['base_commit'], commits[0])
        self.assertEqual(job['must_beat'], 990)
        self.assertEqual(job['metadata']['actions'][0]['parameters'], {'value': 2, 'guards': True})
        addendum = result['family_addenda'][0]
        rule = addendum['rules'][0]
        self.assertEqual(rule['kind'], 'setting')
        self.assertIsNone(rule['bindings']['fast'])
        self.assertEqual(rule['bindings']['fast_reason'], 'secp256k1 layout literal')
        self.assertEqual(rule['parameters']['guards']['enum'], [True])
        file = next((self.root/'discovery/families').glob('*.json'))
        grammar = compose([file])
        self.assertEqual(job['metadata']['grammar_refs'], grammar.documents)
        grammar.validate_frontier_actions(job['metadata']['actions'], 'froghop')
        self.assertIn(rule['id'], grammar.availability('froghop')['active'])
        curve = [{'Q': row['Q'], 'T': row['T']} for row in rows]
        self.assertEqual(propose_addendum_jobs([json.loads(file.read_text())], curve), result['jobs'])
        corrupted = copy.deepcopy(addendum)
        corrupted['rules'][0]['evidence'][0]['sha256'] = '0'*64
        with self.assertRaises(ValueError):
            propose_addendum_jobs([corrupted], curve)
        self.assertEqual(json.loads((self.root/'discovery/jobs.json').read_text())['jobs'], result['jobs'])
        self.assertEqual(discover_trees(list(reversed(rows)), self.repo), result)
        self.assertEqual(len(result['parent_reliability_ranking']), 2)
        self.assertEqual(job['metadata']['parent_reliability']['future_audit_feasibility'], 'unknown')

    def test_worktree_changes_do_not_change_committed_source(self):
        commit = self.commit(1)
        (self.repo/'src/point_add/mod.rs').write_text('uncommitted source')
        resolved, files = read_source(self.repo, commit[:10])
        self.assertEqual(resolved, commit)
        self.assertIn(b'POOL_1: usize = 1;', files['src/point_add/mod.rs'])
        with self.assertRaises(ValueError):
            read_source(self.repo, 'HEAD')

    def test_discovered_job_materializes_with_retained_notices(self):
        (self.repo/'NOTICE').write_text('Base author credit.\n')
        (self.repo/'LICENSE.md').write_text('Fixture license.\n')
        first = self.commit(1)
        (self.repo/'NOTICE').write_text('Later author credit.\n')
        second = self.commit(3)
        rows = [{'id': '11111111', 'commit': first, 'Q': 101, 'T': 990},
                {'id': '22222222', 'commit': second, 'Q': 103, 'T': 970}]
        result = discover_trees(rows, self.repo)
        self.assertEqual(len(result['trees']), 1)
        job = result['jobs'][0]
        self.assertIn('NOTICE', job['base_files_sha256'])
        self.assertIn('LICENSE.md', job['base_files_sha256'])
        receipt = materialize(job, self.root/'candidate', git_repo=self.repo)
        self.assertEqual(receipt['base']['files_sha256'], job['base_files_sha256'])
        self.assertEqual((Path(receipt['source_dir'])/'NOTICE').read_text(), 'Base author credit.\n')
        self.assertIn('POOL_1: usize = 2;', (Path(receipt['source_dir'])/'src/point_add/mod.rs').read_text())

    def test_links_fail_source_admission(self):
        self.commit(1)
        (self.repo/'src/point_add/linked.rs').symlink_to('../elsewhere')
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture symlink')
        with self.assertRaises(ValueError):
            read_source(self.repo, self.git('rev-parse', 'HEAD'))

    def test_single_source_does_not_invent_a_setting_range(self):
        commit = self.commit(1)
        result = discover_trees([{'id': '12345678', 'commit': commit, 'Q': 101, 'T': 990}], self.repo)
        self.assertEqual(result['trees'][0]['dials'], [])
        self.assertEqual(result['jobs'], [])


class ParentReliabilityTests(unittest.TestCase):
    def records(self):
        return [{'submission_id': 'parent-one', 'commit': 'a'*40, 'reliability':
                 parse_reliability('lambda = 0.2 per 9024 shots.', source='parent-one')},
                {'submission_id': 'parent-two', 'commit': 'b'*40, 'reliability':
                 parse_reliability('lambda = 2 per 9024 shots.', source='parent-two')},
                {'submission_id': 'unknown', 'commit': 'c'*40, 'reliability': []}]

    def evidence(self):
        return {'parent-two': {'source_commit': 'b'*40, 'result': {
            'pairs': 20480, 'streams': 2, 'failed_inputs': 0, 'operation_sha256': 'd'*64,
            'row_validation': {'schema': 'frontier-verdict-validation-v1', 'complete': True,
                               'pairs': 20480, 'streams': 2, 'rows': 40960, 'failed_inputs': 0,
                               'ops_sha256': 'd'*64}}}}

    def test_note_ranking_keeps_audit_feasibility_unknown(self):
        rows = rank_parent_reliability(self.records())
        self.assertEqual([r['submission_id'] for r in rows], ['parent-one', 'parent-two', 'unknown'])
        self.assertAlmostEqual(rows[0]['note_screen_pass_approximation']['range'][0], math.exp(-.2))
        self.assertTrue(all(r['future_audit_feasibility'] == 'unknown' for r in rows))
        self.assertTrue(all(r['measured_input_bound'] is None for r in rows))

    def test_measured_bound_uses_input_pairs_without_stream_independence(self):
        ranked = rank_parent_reliability(self.records(), audit_evidence=self.evidence())
        self.assertEqual(ranked[0]['submission_id'], 'parent-two')
        bound = ranked[0]['measured_input_bound']
        self.assertAlmostEqual(bound['upper_bound'], -math.expm1(math.log(.05/32)/20480), places=12)
        self.assertGreater(bound['upper_bound'], -math.expm1(math.log(.05/32)/40960))
        self.assertTrue(bound['meets_limit'])
        self.assertEqual(ranked[0]['future_audit_feasibility'], 'unknown')

    def test_shot_counts_and_unbound_receipts_do_not_admit_an_input_bound(self):
        for mutation in ('missing_joint_counts', 'different_commit', 'incomplete_rows', 'different_operations'):
            evidence = self.evidence()
            receipt = evidence['parent-two']['result']
            if mutation == 'missing_joint_counts':
                receipt['failed_shots'] = receipt.pop('failed_inputs')
            elif mutation == 'different_commit':
                evidence['parent-two']['source_commit'] = 'c'*40
            elif mutation == 'incomplete_rows':
                receipt['row_validation']['rows'] -= 1
            else:
                receipt['operation_sha256'] = 'e'*64
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                rank_parent_reliability(self.records(), audit_evidence=evidence)

    def test_note_contract_limits_do_not_become_rate_estimates(self):
        records = self.records()
        records[0]['reliability'] = parse_reliability('Keep below 18 failing shots per 9024.', source='limit')
        result = next(r for r in rank_parent_reliability(records) if r['submission_id'] == 'parent-one')
        self.assertIsNone(result['note_screen_pass_approximation'])


if __name__ == '__main__':
    unittest.main()
