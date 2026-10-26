"""Protect fixed audit inputs and finalists across controller restarts."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm_circuit_optimiser import runner, large
from llm_circuit_optimiser.cli import parser
from llm_circuit_optimiser.common import load, save, sha256
from llm_circuit_optimiser.families import load_family
from llm_circuit_optimiser.grammar import compose


class AuditResumeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.checker = self.root / 'checker'
        # Replace expensive circuit simulation with explicit outcomes. Keep the
        # real process, receipt validation, selection and resume paths.
        self.checker.write_text(f'#!{sys.executable}\n' + r'''
import hashlib, json, math, sys
from pathlib import Path
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
out = Path(args['--out'])
if out.exists() or out.with_suffix('.jsonl').exists():
    sys.exit(2)
calls = Path(__file__).with_name('calls.jsonl')
with calls.open('a') as f:
    f.write(json.dumps(args) + '\n')
ops = Path(args['--ops'])
spec = json.loads(ops.read_text())
n = int(args['--pairs']); alpha = float(args['--alpha'])
failed = 32 if spec.get('fail_final') and '/fresh-final/' in args['--label'] else 0
rows = []
for i in range(n):
    for stream in range(2):
        bad = i < failed and stream == 0
        rows.append(json.dumps({'idx': i, 'stream': stream,
            'input': {'idx': i, 'target': [1, 2], 'addend': [3, 4], 'expected': [5, 6]},
            'observed_target': [5, 6], 'observed_addend': [3, 4], 'dirty_qubits': [],
            'failures': {'coordinate': False, 'phase': bad, 'workspace': False, 'addend': False},
            'passed': not bad}) + '\n')
raw = ''.join(rows).encode(); per = out.with_suffix('.jsonl'); per.write_bytes(raw)
# Use the production-independent SciPy-free zero-failure expression. The
# failed fixture's bound comes from a fixed value checked in its test.
upper = ({.05: .005243362831700157, .025: .005510023540520732}[alpha]
         if failed else -math.expm1(math.log(alpha) / n))
result = {'pairs': n, 'streams': 2, 'field_width': int(args['--width']),
    'label': args['--label'], 'alpha': alpha, 'qubits': spec.get('Q', 10),
    'mean_toffoli': spec.get('T', 20), 'failed_inputs': failed, 'failed_shots': failed,
    'ops_sha256': hashlib.sha256(ops.read_bytes()).hexdigest(),
    'checker_binary_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'packet_sha256': hashlib.sha256(args['--label'].encode()).hexdigest(),
    'per_input': str(per), 'per_input_sha256': hashlib.sha256(raw).hexdigest(),
    'upper_bound': upper, 'binomial_upper': upper}
out.write_text(json.dumps(result))
sys.exit(1 if failed else 0)
''')
        self.checker.chmod(0o755)
        self.tools = {'small_emit': str(self.checker), 'circuit_check': str(self.checker),
                      'binary_sha256': {'circuit_check': sha256(self.checker)}}
        self.ops = self.root / 'ops.json'
        save(self.ops, {'Q': 10, 'T': 20})

    def calls(self):
        path = self.root / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_completed_check_reuses_verified_outputs(self):
        out = self.root / 'audit.json'
        first = runner.check(self.tools, self.ops, 16, 4, 'fixed-packet', out)
        resumed = runner.check(self.tools, self.ops, 16, 4, 'fixed-packet', out)
        self.assertEqual(first, resumed)
        self.assertEqual(len(self.calls()), 1)

    def test_cached_check_rejects_a_different_packet_without_launch(self):
        out = self.root / 'audit.json'
        runner.check(self.tools, self.ops, 16, 4, 'fixed-packet', out)
        before = out.read_bytes()
        with self.assertRaises(ValueError):
            runner.check(self.tools, self.ops, 16, 4, 'different-packet', out)
        self.assertEqual(out.read_bytes(), before)
        self.assertEqual(len(self.calls()), 1)

    def test_changed_verdict_rows_block_cache_reuse(self):
        out = self.root / 'audit.json'
        runner.check(self.tools, self.ops, 16, 4, 'fixed-packet', out)
        out.with_suffix('.jsonl').write_text('changed\n')
        with self.assertRaises(ValueError):
            runner.check(self.tools, self.ops, 16, 4, 'fixed-packet', out)
        self.assertEqual(len(self.calls()), 1)

    def test_partial_check_never_repeats_a_consumed_packet(self):
        out = self.root / 'audit.json'
        out.with_suffix('.jsonl').write_text('partial\n')
        with self.assertRaisesRegex(ValueError, 'incomplete|interrupted'):
            runner.check(self.tools, self.ops, 16, 4, 'fixed-packet', out)
        self.assertEqual(self.calls(), [])

    def campaign(self, *, failed=False, dominated=False, audit_pairs=8192):
        family = load_family('froghop')
        grammar = compose(runner.default_paths(family))
        def emit(target, directory, **kwargs):
            directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
            ops = directory / 'ops.json'
            save(ops, {'Q': 10 if target == 'q799' else 20,
                       'T': 10 if dominated or target == 'q839' else 20,
                       'fail_final': failed and target == 'q799'})
            return {'ops_path': str(ops), 'historical_hash_matches': True}
        small = {'emission': {'guards_active': True, 'activation_counts': dict.fromkeys('mfsur', 1)},
                 'qualified': True}
        with patch.object(runner, 'native_tools', return_value=self.tools), \
                patch.object(runner, 'small_stage', return_value=small), \
                patch.object(large, 'emit', side_effect=emit):
            return runner.run_campaign(grammar, family, {'provider': 'offline'}, self.root / 'run',
                target_ids=['q799', 'q839'], mode='reproduce', large_pairs=8192, audit_pairs=audit_pairs)

    def test_completed_campaign_resumes_without_repeating_audits(self):
        first = self.campaign()
        calls = self.calls()
        resumed = self.campaign()
        self.assertEqual(resumed, first)
        self.assertEqual(self.calls(), calls)

    def test_failed_finalist_does_not_promote_a_backup_on_resume(self):
        first = self.campaign(failed=True, dominated=True)
        selected = (self.root / 'run/selected.json').read_bytes()
        self.assertEqual(first['front'], [])
        resumed = self.campaign(failed=True, dominated=True)
        self.assertEqual((self.root / 'run/selected.json').read_bytes(), selected)
        self.assertEqual(resumed['front'], [])
        final_calls = [c for c in self.calls() if '/fresh-final/' in c['--label']]
        self.assertEqual(len(final_calls), 1)

    def test_partial_final_resume_keeps_the_original_confidence_allowance(self):
        original = runner.check
        def interrupt_second(*args, **kwargs):
            if Path(args[5]).as_posix().endswith('/final/001.json'):
                raise KeyboardInterrupt('Controller stopped between audits')
            return original(*args, **kwargs)
        with patch.object(runner, 'check', side_effect=interrupt_second):
            with self.assertRaises(KeyboardInterrupt):
                self.campaign(failed=True)
        frozen = (self.root / 'run/selected.json').read_bytes()
        resumed = self.campaign(failed=True)
        self.assertEqual((self.root / 'run/selected.json').read_bytes(), frozen)
        self.assertEqual([r['candidate']['target'] for r in resumed['front']], ['q839'])
        final_calls = [c for c in self.calls() if '/fresh-final/' in c['--label']]
        self.assertEqual([float(c['--alpha']) for c in final_calls], [.025, .025])
        self.assertEqual([c['--label'].rsplit('/', 1)[-1] for c in final_calls], ['0', '1'])

    def test_changed_finalist_list_blocks_resume(self):
        self.campaign()
        path = self.root / 'run/selected.json'
        save(path, load(path)[1:])
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'selection|finalist'):
            self.campaign()
        self.assertEqual(path.read_bytes(), before)

    def test_default_search_requests_a_fresh_audit(self):
        args = parser().parse_args(['search', '--out', str(self.root / 'run')])
        self.assertGreater(args.audit_pairs, 0)

    def test_screening_only_results_name_their_scope(self):
        result = self.campaign(audit_pairs=0)
        self.assertEqual(result['qualification_scope'], 'screening-only')
        self.assertFalse(any('/fresh-final/' in c['--label'] for c in self.calls()))


if __name__ == '__main__':
    unittest.main()
