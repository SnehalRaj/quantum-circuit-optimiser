"""Check typed source edits against pinned grammar and base identities."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from llm_circuit_optimiser.common import digest
from llm_circuit_optimiser.frontier.bindings import bind_actions
from llm_circuit_optimiser.frontier.discovery import (
    OLD_GUARD, NEW_GUARD, _dial_rule, analyze_sources, discover_trees, inspect_source,
    propose_addendum_jobs, read_source)
from llm_circuit_optimiser.frontier.materialize import _apply_recipe, materialize, normalize_recipe
from llm_circuit_optimiser.grammar import compose

ROOT = Path(__file__).resolve().parents[1]
MOD = 'src/point_add/mod.rs'
BODY = 'src/point_add/froghop_one.rs'


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.base = {MOD: b'mod cond_pass;\nfn build() {\n    ops.extend(tail);\n}\n',
                     BODY: ('pub const GPOOL: usize = 0;\n' + OLD_GUARD + '\n').encode(),
                     'NOTICE': b'Parent credit\n'}
        other = {**self.base, BODY: ('pub const GPOOL: usize = 10;\n' + NEW_GUARD + '\n').encode()}
        records = [{'submission_id': str(i)*8, 'commit': c, 'Q': q, 'T': t, 'created': str(i),
                    'source': inspect_source(files, c)} for i, c, q, t, files in
                   [(1, 'a'*40, 818, 10000, self.base), (2, 'b'*40, 828, 9000, other)]]
        tree = analyze_sources(records)[0]
        self.document = {'schema_version': 1, 'id': 'binding-fixture', 'revision': 1,
                         'rules': [_dial_rule(tree, tree['dials'][0])]}
        self.rule = self.document['rules'][0]['id']
        self.job = propose_addendum_jobs([self.document], values={self.rule: [5]})[0]
        self.grammar = self.compile(self.document)

    def compile(self, document, *, catalog=False):
        path = self.directory/'setting.json'
        path.write_text(json.dumps(document))
        return compose([path] + ([ROOT/'grammars/frontier/catalog.json'] if catalog else []))

    def test_setting_binds_literal_and_required_guard_with_full_source_pins(self):
        files = dict(self.base)
        bound = bind_actions(self.job, self.grammar, base_files=files)
        self.assertEqual(bound['job']['edits'], self.job['edits'])
        self.assertEqual(bound['job']['base_files_sha256']['NOTICE'], hashlib.sha256(files['NOTICE']).hexdigest())
        receipt = bound['receipt']
        self.assertTrue(receipt['typed'])
        self.assertEqual(receipt['grammar_sha256'], self.grammar.sha256)
        self.assertEqual(receipt['actions'][0]['fast_reason'], 'secp256k1 layout literal')
        self.assertEqual(receipt['edits'][1]['new'], NEW_GUARD)
        self.assertEqual(bound, bind_actions(self.job, self.grammar, base_files=files))

    def test_discovery_binding_materialization_retains_notices(self):
        repo = self.directory/'source'
        repo.mkdir()
        def git(*args):
            return subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                                  text=True, check=True).stdout.strip()
        git('init', '-q')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        rows = []
        for index, value in enumerate([0, 10]):
            files = {**self.base, 'LICENSE.md': b'Fixture license\n',
                     BODY: (f'pub const GPOOL: usize = {value};\n' +
                            (OLD_GUARD if value == 0 else NEW_GUARD) + '\n').encode()}
            for name, raw in files.items():
                path = repo/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            git('add', '.')
            git('commit', '-qm', f'Fixture {value}')
            rows.append({'id': str(index + 1)*8, 'commit': git('rev-parse', 'HEAD'),
                         'Q': 818 + value, 'T': 10000 - 100*value})
        discovered = discover_trees(rows, repo, self.directory/'discovery')
        grammar = compose(sorted((self.directory/'discovery/families').glob('*.json')))
        job = discovered['jobs'][0]
        _, files = read_source(repo, job['base_commit'])
        bound = bind_actions(job, grammar, base_files=files)
        result = materialize(bound['job'], self.directory/'materialized', git_repo=repo)
        self.assertEqual(result['base']['files_sha256'], bound['job']['base_files_sha256'])
        source = Path(result['source_dir'])
        self.assertEqual((source/'NOTICE').read_bytes(), self.base['NOTICE'])
        self.assertEqual((source/'LICENSE.md').read_bytes(), b'Fixture license\n')
        self.assertIn('GPOOL: usize = 1;', (source/BODY).read_text())
        self.assertIn(NEW_GUARD, (source/BODY).read_text())

    def test_action_only_job_expands_but_altered_existing_edits_fail(self):
        job = copy.deepcopy(self.job)
        job['edits'] = []
        self.assertEqual(bind_actions(job, self.grammar, base_files=self.base)['job']['edits'], self.job['edits'])
        for change in ['omit_guard', 'literal', 'extra']:
            job = copy.deepcopy(self.job)
            if change == 'omit_guard': job['edits'].pop()
            elif change == 'literal': job['edits'][0]['new'] = 'pub const GPOOL: usize = 6;'
            else: job['edits'].append({'path': MOD, 'old': 'build', 'new': 'other', 'count': 1})
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'differ'):
                bind_actions(job, self.grammar, base_files=self.base)

    def test_changed_base_commit_source_and_grammar_reference_fail(self):
        for changed in ['commit', 'source', 'reference', 'missing_reference', 'guard_false', 'family']:
            job, files = copy.deepcopy(self.job), dict(self.base)
            if changed == 'commit': job['base_commit'] = 'c'*40
            elif changed == 'source': files[BODY] += b'// changed\n'
            elif changed == 'reference': job['metadata']['grammar_refs'][0]['sha256'] = 'f'*64
            elif changed == 'missing_reference': job['metadata'].pop('grammar_refs')
            elif changed == 'guard_false': job['metadata']['actions'][0]['parameters']['guards'] = False
            else: job['metadata']['family'] = 'skywalk-cof'
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                bind_actions(job, self.grammar, base_files=files)

    def test_recipe_cannot_invent_a_literal_or_coupled_expression(self):
        for changed in ['statement', 'coupled', 'condition']:
            document = copy.deepcopy(self.document)
            program = document['rules'][0]['evidence'][0]['setting_recipe']
            if changed == 'statement': program['base']['source']['constants'][0]['statement'] = 'execute();'
            elif changed == 'coupled': program['dial']['coupled_edits'][0]['new'] = 'execute();'
            else: program['dial']['coupled_edits'][0]['when'] = '__import__("os").system("false")'
            grammar = self.compile(document)
            job = copy.deepcopy(self.job)
            job['metadata']['grammar_refs'] = grammar.documents
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                bind_actions(job, grammar, base_files=self.base)

    def catalog_job(self, grammar):
        return {'id': 'catalog-fixture', 'base_commit': 'a'*40, 'edits': [], 'file_copies': [],
                'metadata': {'family': 'froghop', 'actions': [{'rule': 'base.local_catalog', 'parameters': {}}],
                             'grammar_refs': grammar.documents}}

    def test_catalog_binds_exact_sources_and_module_and_call(self):
        grammar = compose([ROOT/'grammars/frontier/catalog.json'])
        bound = bind_actions(self.catalog_job(grammar), grammar, base_files=self.base)
        self.assertEqual(len(bound['job']['edits']), 2)
        self.assertEqual(len(bound['job']['file_copies']), 2)
        self.assertIn('frontier_rewrite::apply(ops);\n    ops.extend(tail);', bound['job']['edits'][1]['new'])
        payload = [(item, (ROOT/'vendor/frontier_rewrite'/Path(item['path']).name).read_bytes(), item['source_commit'])
                   for item in bound['job']['file_copies']]
        changed, _, _ = _apply_recipe(self.base, normalize_recipe(bound['job']), payload)
        inherited = bind_actions(self.catalog_job(grammar), grammar, base_files=changed)
        self.assertTrue(inherited['receipt']['actions'][0]['inherited'])
        self.assertEqual(inherited['job']['edits'], [])

    def test_catalog_rejects_wrong_hash_placement_partial_source_and_duplicate_anchor(self):
        grammar = compose([ROOT/'grammars/frontier/catalog.json'])
        bound = bind_actions(self.catalog_job(grammar), grammar, base_files=self.base)['job']
        for changed in ['copy', 'placement', 'partial', 'anchor']:
            job, files = copy.deepcopy(bound), dict(self.base)
            if changed == 'copy': job['file_copies'][0]['sha256'] = 'f'*64
            elif changed == 'placement': job['edits'][1]['new'] = '    ops.extend(tail);\n    ops = frontier_rewrite::apply(ops);'
            elif changed == 'partial': files['src/point_add/frontier_rewrite/mod.rs'] = b'changed'
            else: files[MOD] += b'mod cond_pass;\n'
            job.pop('base_files_sha256')
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                bind_actions(job, grammar, base_files=files)

    def test_setting_and_catalog_preserve_action_order(self):
        grammar = self.compile(self.document, catalog=True)
        job = copy.deepcopy(self.job)
        job['edits'] = []
        job['metadata']['grammar_refs'] = grammar.documents
        job['metadata']['actions'].append({'rule': 'base.local_catalog', 'parameters': {}})
        result = bind_actions(job, grammar, base_files=self.base)
        self.assertEqual(len(result['job']['edits']), 4)
        self.assertEqual([e['path'] for e in result['job']['edits']], [BODY, BODY, MOD, MOD])

    def test_manual_jobs_keep_an_explicit_scope_and_still_check_counts(self):
        job = copy.deepcopy(self.job)
        job['metadata'] = {}
        result = bind_actions(job, base_files=self.base)
        self.assertFalse(result['receipt']['typed'])
        self.assertIn('Manual counted', result['receipt']['scope'])
        job['edits'][0]['count'] = 2
        with self.assertRaisesRegex(ValueError, 'matches'):
            bind_actions(job, base_files=self.base)


if __name__ == '__main__':
    unittest.main()
