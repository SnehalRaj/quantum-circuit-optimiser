from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from llm_circuit_optimiser.frontier.materialize import materialize, normalize_recipe


def sha(data):
    return hashlib.sha256(data).hexdigest()


def put(root, path, data):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def pins(root):
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()}


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base, self.fixed, self.copies = [self.root / name for name in ('base', 'fixed', 'copies')]
        self.module = (b'pub const GPOOL: usize = 0;\r\n'
                       b'const NONCE: usize = 7;\r\n'
                       b'pub fn build() {\r\n'
                       b'    let mut tail = Vec::new();\r\n'
                       b'    for i in 0..NONCE { tail.push(i); }\r\n'
                       b'    ops.extend(tail);\r\n}\r\n')
        put(self.base, 'src/point_add/mod.rs', self.module)
        put(self.base, 'src/point_add/froghop_one.rs', b'pub const POOL_1: usize = 18;\r\nassert!(pool > 0);\r\n')
        put(self.base, 'NOTICE', b'Inherited parent authors\n')
        put(self.fixed, 'src/bin/eval_circuit.rs', b'fixed evaluator\n')
        put(self.fixed, 'src/point_add/mod.rs', b'fixed placeholder\n')
        put(self.fixed, 'Cargo.toml', b'fixed build\n')
        put(self.fixed, 'NOTICE', b'Fixed checker authors\n')
        put(self.fixed, 'manifest.json', json.dumps({'files_sha256': pins(self.fixed)}).encode())
        put(self.copies, 'src/point_add/catalog.rs', b'pub fn apply() {}\n')
        self.job = {'id': 'q819-p19-cat', 'base_commit': 'a' * 40, 'base_files_sha256': pins(self.base),
                    'Q': 819, 'expected_T_inference': 9718945,
                    'edits': [{'path': 'src/point_add/froghop_one.rs',
                               'old': 'pub const POOL_1: usize = 18;',
                               'new': 'pub const POOL_1: usize = 19;', 'count': 1}],
                    'file_copies': []}

    def run_job(self, job=None, name='work', **kwargs):
        return materialize(self.job if job is None else job, self.root / name,
                           benchmark_dir=self.fixed, base_dir=self.base, **kwargs)

    def test_ordered_edits_preserve_bytes_and_the_fixed_checker(self):
        self.job['edits'] += [
            {'path': 'src/point_add/froghop_one.rs', 'old': 'usize = 19;', 'new': 'usize = 20;', 'count': 1},
            {'path': 'src/point_add/mod.rs', 'old': 'GPOOL: usize = 0;', 'new': 'GPOOL: usize = 10;', 'count': 1},
            {'path': 'src/point_add/froghop_one.rs', 'old': 'assert!(pool > 0);',
             'new': 'assert!(pool + GPOOL > 0);', 'count': 1}]
        result = self.run_job()
        source = Path(result['source_dir'])
        self.assertEqual((source / 'src/point_add/froghop_one.rs').read_bytes(),
                         b'pub const POOL_1: usize = 20;\r\nassert!(pool + GPOOL > 0);\r\n')
        self.assertEqual((source / 'src/bin/eval_circuit.rs').read_bytes(), b'fixed evaluator\n')
        self.assertEqual((source / 'NOTICE').read_bytes(), b'Inherited parent authors\n')
        self.assertEqual(result['edits'][0]['after_sha256'], result['edits'][1]['before_sha256'])
        receipt = (self.root / 'work/materialization.json').read_bytes()
        self.assertEqual(self.run_job(), result)
        self.assertEqual((self.root / 'work/materialization.json').read_bytes(), receipt)

    def test_copy_hash_and_catalog_call_keep_the_inherited_tail(self):
        self.job['file_copies'] = [{'path': 'src/point_add/catalog.rs', 'source_path': 'src/point_add/catalog.rs',
                                    'source_commit': 'b' * 40, 'sha256': sha(b'pub fn apply() {}\n')}]
        self.job['edits'].append({'path': 'src/point_add/mod.rs', 'old': '    ops.extend(tail);',
                                 'new': '    ops = catalog::apply(ops);\r\n    ops.extend(tail);', 'count': 1})
        result = self.run_job(files_root=self.copies)
        self.assertEqual(result['file_copies'][0]['sha256'], sha(b'pub fn apply() {}\n'))
        self.assertIn(b'ops = catalog::apply(ops);\r\n', (Path(result['source_dir']) / 'src/point_add/mod.rs').read_bytes())
        bad = deepcopy(self.job)
        bad['file_copies'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'differs from its hash'):
            self.run_job(bad, 'bad-copy', files_root=self.copies)

    def test_missing_and_duplicate_match_fail_and_preserve_attempt(self):
        for old in ('absent source', 'usize'):
            bad = deepcopy(self.job)
            bad['edits'][0].update(path='src/point_add/mod.rs', old=old)
            work = 'bad-' + old.replace(' ', '-')
            with self.assertRaisesRegex(ValueError, 'matches'):
                self.run_job(bad, work)
            self.assertFalse((self.root / work / 'materialization.json').exists())
            self.assertEqual(len(list((self.root / work / 'attempts').glob('*/failure.json'))), 1)

    def test_changes_to_nonce_tail_body_and_tail_call_are_rejected(self):
        for old, new in [('NONCE: usize = 7', 'NONCE: usize = 8'),
                         ('tail.push(i)', 'tail.push(i + 1)'),
                         ('ops.extend(tail)', 'ops.extend(Vec::new())')]:
            job = deepcopy(self.job)
            job['edits'] = [{'path': 'src/point_add/mod.rs', 'old': old, 'new': new, 'count': 1}]
            with self.assertRaisesRegex(ValueError, 'nonce or tail'):
                self.run_job(job, 'nonce-' + str(len(old)))

    def test_base_and_benchmark_changes_are_rejected(self):
        put(self.base, 'extra.rs', b'unpinned\n')
        with self.assertRaisesRegex(ValueError, 'base source closure'):
            self.run_job()
        (self.base / 'extra.rs').unlink()
        put(self.fixed, 'Cargo.toml', b'altered build\n')
        with self.assertRaisesRegex(ValueError, 'fixed benchmark'):
            self.run_job()

    def test_resume_cannot_accept_tampered_source_and_receipt_hashes(self):
        receipt = self.run_job()
        source = Path(receipt['source_dir'])
        put(source, 'src/point_add/froghop_one.rs', b'altered source\n')
        receipt['source_hashes']['src/point_add/froghop_one.rs'] = sha(b'altered source\n')
        (self.root / 'work/materialization.json').write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'changed after'):
            self.run_job()

    def test_rejects_unpinned_directory_and_unfinished_output(self):
        job = deepcopy(self.job)
        job.pop('base_files_sha256')
        with self.assertRaisesRegex(ValueError, 'complete base_files'):
            self.run_job(job)
        (self.root / 'work/source').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'no frozen'):
            self.run_job()

    def test_rejects_symlinks_in_base_copy_and_output(self):
        (self.base / 'link').symlink_to(self.base / 'NOTICE')
        with self.assertRaisesRegex(ValueError, 'symbolic links'):
            self.run_job()
        (self.base / 'link').unlink()
        result = self.run_job()
        source = Path(result['source_dir'])
        (source / 'src/point_add/mod.rs').unlink()
        (source / 'src/point_add/mod.rs').symlink_to(self.base / 'src/point_add/mod.rs')
        with self.assertRaisesRegex(ValueError, 'symbolic links'):
            self.run_job()

    def test_rejects_evaluator_build_and_traversal_edits(self):
        for name in ('src/bin/eval_circuit.rs', 'Cargo.toml', '../x', '/tmp/x',
                     'src/point_add/../../eval.rs', 'src//point_add/mod.rs', 'src\\point_add\\mod.rs'):
            job = deepcopy(self.job)
            job['edits'][0]['path'] = name
            with self.assertRaises(ValueError):
                self.run_job(job)
        for count in (True, 0, -1, 1.0):
            job = deepcopy(self.job)
            job['edits'][0]['count'] = count
            with self.assertRaisesRegex(ValueError, 'positive integer'):
                self.run_job(job)

    def test_blocked_settings_and_legacy_normalization(self):
        job = deepcopy(self.job)
        job['blocked_reasons'] = ['No counted PEAK0 edit yet']
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.run_job(job)
        job = {'base_commit': 'a' * 40, 'edits': [['src/point_add/mod.rs', 'old', 'new']],
               'files_from_commit_4073f09c': {'src/point_add/catalog.rs': '0' * 64}}
        result = normalize_recipe(job)
        self.assertEqual(result['edits'][0]['count'], 1)
        self.assertEqual(result['file_copies'][0]['source_commit'], '4073f09c')

    def test_copied_files_cannot_replace_source_or_change_the_checker(self):
        for destination in ('src/point_add/mod.rs', 'src/bin/eval_circuit.rs'):
            job = deepcopy(self.job)
            job['file_copies'] = [{'path': destination, 'source_path': 'src/point_add/catalog.rs',
                                   'source_commit': 'b' * 40, 'sha256': sha(b'pub fn apply() {}\n')}]
            with self.assertRaises(ValueError):
                self.run_job(job, 'overwrite-' + Path(destination).stem, files_root=self.copies)

    def test_git_copy_uses_pinned_object_bytes(self):
        repo = self.root / 'git-copy'
        repo.mkdir()
        put(repo, 'src/point_add/mod.rs', self.module)
        put(repo, 'src/point_add/catalog.rs', b'pub fn apply() {}\n')
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
        git('init', '-q')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Copy source')
        commit = git('rev-parse', 'HEAD')
        job = {'id': 'git-copy', 'base_commit': commit, 'edits': [],
               'file_copies': [{'path': 'src/point_add/extra.rs', 'source_path': 'src/point_add/catalog.rs',
                                'source_commit': commit, 'sha256': sha(b'pub fn apply() {}\n')}]}
        put(repo, 'src/point_add/catalog.rs', b'worktree replacement\n')
        result = materialize(job, self.root / 'git-copy-work', git_repo=repo, benchmark_dir=self.fixed)
        self.assertEqual((Path(result['source_dir']) / 'src/point_add/extra.rs').read_bytes(), b'pub fn apply() {}\n')
        self.assertEqual(result['file_copies'][0]['resolved_source_commit'], commit)

    def test_git_source_resolves_a_commit_and_ignores_worktree_edits(self):
        repo = self.root / 'git'
        repo.mkdir()
        for name, content in [('src/point_add/mod.rs', self.module), ('NOTICE', b'Git parent\n'),
                              ('src/bin/eval_circuit.rs', b'candidate checker must be ignored\n')]:
            put(repo, name, content)
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
        git('init', '-q')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Pinned source')
        commit = git('rev-parse', 'HEAD')
        job = {'id': 'git-case', 'base_commit': commit[:10], 'base_files_sha256': {}, 'edits': [], 'file_copies': []}
        put(repo, 'src/point_add/mod.rs', b'uncaptured worktree edit\n')
        result = materialize(job, self.root / 'git-work', git_repo=repo, benchmark_dir=self.fixed)
        source = Path(result['source_dir'])
        self.assertEqual(result['parent_commit'], commit)
        self.assertEqual((source / 'src/point_add/mod.rs').read_bytes(), self.module)
        self.assertEqual((source / 'src/bin/eval_circuit.rs').read_bytes(), b'fixed evaluator\n')
        self.assertEqual((source / 'NOTICE').read_bytes(), b'Git parent\n')


if __name__ == '__main__':
    unittest.main()
