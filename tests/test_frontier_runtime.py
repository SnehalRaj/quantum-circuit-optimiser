"""Exercise the campaign adapter with local fake instruments."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm_circuit_optimiser.common import ROOT, sha256
from llm_circuit_optimiser.frontier.campaign import Campaign, InterruptedStage
from llm_circuit_optimiser.frontier.registry import RegistryTampered
from llm_circuit_optimiser.frontier.runtime import RuntimeAdapter, parse_emission, resolve_dependencies


def executable(path, body):
    path.write_text('#!' + sys.executable + '\n' + body)
    path.chmod(0o755)
    return str(path)


def row(identifier, q, t):
    return {'id': identifier, 'solverUsername': 'someone', 'status': 'accepted',
            'officialScore': q*t, 'officialMetrics': {'qubits': q, 'toffoli': t}}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, {'LCO_EMISSION_LOCK': str(self.root/'emission.lock')})
        environment.start()
        self.addCleanup(environment.stop)
        self.base, self.benchmark, self.tools = [self.root/name for name in ('base', 'benchmark', 'tools')]
        for folder in [self.base, self.benchmark, self.tools]: folder.mkdir()
        (self.base/'src/point_add').mkdir(parents=True)
        (self.base/'src/point_add/mod.rs').write_text('pub const POOL_1: usize = 18;\n')
        (self.base/'NOTICE').write_text('Fixture parent notice.\n')
        pins = {str(p.relative_to(self.base)): sha256(p) for p in self.base.rglob('*') if p.is_file()}
        (self.benchmark/'src').mkdir()
        (self.benchmark/'Cargo.toml').write_text('[package]\nname="fixture"\nversion="0.1.0"\n')
        (self.benchmark/'Cargo.lock').write_text('# fixture lock\n')
        (self.benchmark/'src/lib.rs').write_text('// fixed checker fixture\n')
        benchmark_pins = {str(p.relative_to(self.benchmark)): sha256(p) for p in self.benchmark.rglob('*') if p.is_file()}
        (self.benchmark/'manifest.json').write_text(json.dumps({'files_sha256': benchmark_pins}))
        self.calls = self.root/'eval-calls.txt'
        emitter = "from pathlib import Path\nimport sys\nPath('ops.bin').write_bytes(b'fixed operations')\nprint('froghop-one peak qubits 818',file=sys.stderr)\nprint('POSTPASS final=(1000, 100, 100)',file=sys.stderr)\nprint('research_catalog rewrites=0 native_toffolis_saved=0',file=sys.stderr)\n"
        evaluator = "from pathlib import Path\nimport json\nSOURCE=__SOURCE__\nwith Path(" + repr(str(self.calls)) + ").open('a') as f:f.write('screen\\n')\nPath(SOURCE,'score.json').write_text(json.dumps({'score':81800,'metrics':{'qubits':818,'toffoli':100}}))\nprint('tested shots: 9024\\nclassical mismatches: 0\\nphase-garbage batches: 0\\nancilla-garbage batches: 0')\n"
        cargo_body = "import os,sys\nfrom pathlib import Path\np=Path(os.environ['CARGO_TARGET_DIR'])/'release';p.mkdir(parents=True,exist_ok=True)\n"
        cargo_body += "emitter=" + repr(emitter) + "\nevaluator=" + repr(evaluator) + "\n"
        cargo_body += "for name,body in [('build_circuit',emitter),('eval_circuit',evaluator.replace('__SOURCE__',repr(str(Path.cwd()))))]:\n f=p/name;f.write_text('#!'+sys.executable+'\\n'+body);f.chmod(0o755)\n"
        cargo = executable(self.tools/'cargo', cargo_body)
        executable(self.tools/'rustc', "print('rustc 1.93.0 (fixture)')\n")
        checker_body = '''import argparse,json,hashlib
from pathlib import Path
p=argparse.ArgumentParser()
for flag in ['width','ops','pairs','label','out','streams','alpha','threads']:p.add_argument('--'+flag)
a=p.parse_args(); n=int(a.pairs); out=Path(a.out); lines=out.with_suffix('.jsonl')
with lines.open('w') as f:
 for i in range(n):
  for stream in range(2):
   r={'idx':i,'stream':stream,'input':{'idx':i,'target':[1,2],'addend':[3,4],'expected':[5,6]},'observed_target':[5,6],'observed_addend':[3,4],'dirty_qubits':[],'failures':{'coordinate':False,'phase':False,'workspace':False,'addend':False},'passed':True}
   f.write(json.dumps(r)+'\\n')
r={'pairs':n,'streams':2,'field_width':256,'label':a.label,'alpha':float(a.alpha),'failed_inputs':0,'failed_shots':0,'packet_sha256':hashlib.sha256(a.label.encode()).hexdigest(),'per_input':str(lines)}
out.write_text(json.dumps(r))
'''
        checker = executable(self.tools/'checker', checker_body)
        self.config = {'base_dir': str(self.base), 'benchmark_dir': str(self.benchmark), 'cargo': cargo,
                       'checker': {'path': checker, 'sha256': sha256(checker), 'kind': 'native'},
                       'username': 'owner', 'model': 'Model-free', 'harness': 'fixture',
                       'credits': ['The fixture parent author wrote the arithmetic.'],
                       'inherited_contract': 'Fixture contract\n' + '\n'.join('Recorded fixture condition '+str(i) for i in range(80))}
        self.job = {'id': 'q818', 'base_commit': 'a'*40, 'Q': 818, 'owner': 'cluster', 'region': 'fixture',
                    'base_files_sha256': pins, 'edits': [{'path': 'src/point_add/mod.rs', 'old': '18;', 'new': '19;', 'count': 1}],
                    'file_copies': [], 'dependencies': [{'role': 'parent', 'submission_id': 'parent',
                    'rationale': 'Fixture constructor', 'evidence': 'Fixture commit'}], 'must_beat': 101}
        self.runtime = RuntimeAdapter(self.config)
        self.campaign = Campaign(self.root/'campaign', self.runtime, owner='cluster')

    def _receipts(self):
        from llm_circuit_optimiser.frontier.registry import digest
        return self.campaign._read_chain(self.root/'campaign/jobs/q818', digest(self.job))[0]

    def test_configured_cargo_and_rustc_keep_their_proxy_names(self):
        cargo = self.tools/'cargo'
        cargo_body = cargo.read_text().split('\n', 1)[1]
        proxy = executable(self.tools/'rustup',
            "import sys\nfrom pathlib import Path\n"
            "name=Path(sys.argv[0]).name\n"
            "if name=='rustc': print('rustc 1.93.0 (fixture)')\n"
            "elif name=='cargo': exec(" + repr(cargo_body) + ")\n"
            "else: raise SystemExit('rustup was invoked instead of its cargo proxy')\n")
        for name in ('cargo', 'rustc'):
            path = self.tools/name
            path.unlink()
            path.symlink_to(proxy)
        try:
            result = self.campaign.run_job(self.job, through='build')
        except RuntimeError as error:
            stderr = '\n'.join(path.read_text() for path in
                (self.root/'campaign/jobs/q818/runtime/build').glob('*/compile/stderr.txt'))
            self.fail(f'{error}\n{stderr}')
        self.assertEqual(result['state'], 'ready')
        built = self._receipts()['build']
        self.assertTrue(Path(built['build_binary']).is_file())
        self.assertTrue(Path(built['eval_binary']).is_file())

    def test_full_chain_through_export_uses_actual_commands(self):
        result = self.campaign.run_job(self.job)
        self.assertEqual(result['state'], 'ready')
        receipts = self._receipts()
        self.assertEqual(receipts['develop']['per_input_rows_compared'], 16384)
        self.assertEqual(receipts['audit']['pairs'], 20480)
        self.assertEqual(receipts['screen']['T'], 100)
        self.assertEqual(receipts['emit']['catalog_rewrites'], 0)
        self.assertEqual(self.calls.read_text().splitlines(), ['screen'])
        self.campaign.run_job(self.job)
        self.assertEqual(self.calls.read_text().splitlines(), ['screen'])
        self.assertIn('## Measured results', Path(receipts['export']['note_path']).read_text())
        self.assertGreater(Path(receipts['export']['note_path']).stat().st_size, 5120)

    def test_typed_catalog_actions_reach_materialization(self):
        from llm_circuit_optimiser.grammar import compose
        grammar_file = ROOT/'grammars/frontier/catalog.json'
        grammar = compose([grammar_file])
        (self.base/'src/point_add/mod.rs').write_text('mod cond_pass;\nfn build() {\n    ops.extend(tail);\n}\n')
        self.job['base_files_sha256'] = {str(p.relative_to(self.base)): sha256(p)
                                       for p in self.base.rglob('*') if p.is_file()}
        self.job['edits'] = []
        self.job['metadata'] = {'family': 'froghop', 'grammar_refs': grammar.documents,
                                'actions': [{'rule': 'base.local_catalog', 'parameters': {}}]}
        files = self.root/'catalog-files/src/point_add/frontier_rewrite'
        files.mkdir(parents=True)
        for name in ('mod.rs', 'catalog.rs'):
            shutil.copy2(ROOT/'vendor/frontier_rewrite'/name, files/name)
        self.config.update(grammar_files=[str(grammar_file)], files_root=str(self.root/'catalog-files'))
        self.runtime = RuntimeAdapter(self.config)
        self.campaign = Campaign(self.root/'campaign', self.runtime, owner='cluster')
        self.campaign.run_job(self.job, through='materialize')
        result = self._receipts()['materialize']
        self.assertTrue(result['grammar_binding']['typed'])
        self.assertEqual(len(result['file_copies']), 2)
        self.assertIn('frontier_rewrite::apply', (Path(result['source_dir'])/'src/point_add/mod.rs').read_text())

    def test_declared_rule_without_grammar_cannot_materialize(self):
        self.job['metadata'] = {'actions': [{'rule': 'unknown.rule', 'parameters': {}}], 'family': 'froghop'}
        with self.assertRaises(ValueError):
            self.campaign.run_job(self.job, through='materialize')
        self.assertFalse((self.root/'campaign/jobs/q818/candidate/source').exists())

    def test_static_gate_prevents_all_evaluations(self):
        self.job['must_beat'] = 99
        result = self.campaign.run_job(self.job)
        self.assertEqual(result['stopped_at'], 'static_gate')
        self.assertFalse(self.calls.exists())

    def test_runtime_config_changes_cannot_resume(self):
        self.campaign.run_job(self.job, through='materialize')
        other = RuntimeAdapter({**self.config, 'model': 'different'})
        with self.assertRaises(ValueError):
            other.run_stage('build', self.job, self.root/'campaign/jobs/q818', self._receipts(), {})

    def test_runtime_config_change_blocks_fully_cached_chain(self):
        self.campaign.run_job(self.job, through='materialize')
        other = Campaign(self.root/'campaign', RuntimeAdapter({**self.config, 'model': 'different'}), owner='cluster')
        with self.assertRaisesRegex(ValueError, 'configuration changed'):
            other.run_job(self.job, through='materialize')

    def test_admission_code_change_blocks_fully_cached_chain(self):
        self.campaign.run_job(self.job, through='materialize')
        changed = self.runtime._implementation_hashes()
        changed[next(iter(changed))] = 'f'*64
        with patch.object(self.runtime, '_implementation_hashes', return_value=changed):
            with self.assertRaisesRegex(ValueError, 'configuration changed'):
                self.campaign.run_job(self.job, through='materialize')

    def test_checker_hash_guard(self):
        path = Path(self.config['checker']['path']);path.write_text(path.read_text()+'\n# changed\n')
        with self.assertRaises(ValueError):
            self.runtime._checker(self.root/'check')

    def test_expected_operation_hash_from_job_metadata_is_checked(self):
        self.job['metadata'] = {'expected_ops_sha256': 'f'*64}
        with self.assertRaisesRegex(ValueError, 'frozen replay hash'):
            self.campaign.run_job(self.job, through='emit')

    def test_audit_recounts_saved_rows_before_qualification(self):
        ops = self.root/'test-ops.bin'; ops.write_bytes(b'fixture')
        result = self.runtime._check(ops, self.root/'check', pairs=2, label='audit-fixture', alpha=.05)
        rows = Path(result['per_input'])
        values = [json.loads(line) for line in rows.read_text().splitlines()]
        values[0]['passed'] = False; values[0]['failures']['phase'] = True
        rows.write_text(''.join(json.dumps(value)+'\n' for value in values))
        result['per_input_sha256'] = sha256(rows)
        with patch.object(self.runtime, '_bound'), patch.object(self.runtime, '_check', return_value=result):
            with self.assertRaisesRegex(ValueError, 'count differs'):
                self.runtime._audit(self.job, self.root, {'emit': {'ops_path': str(ops)}},
                                    {'audit_slot': {'pairs': 2, 'streams': 2, 'label': 'audit-fixture', 'alpha': .05}})

    def test_missing_emission_count_cannot_pass_static_gate(self):
        self.campaign.run_job(self.job, through='emit')
        receipts = self._receipts();receipts['emit']['static_expected_T'] = None
        with self.assertRaises(ValueError):
            self.runtime._static_gate(self.job, self.root, receipts, {})

    def test_emission_parser_retains_inference_scope(self):
        value = parse_emission('froghop-double peak qubits = 803\nPOSTPASS final=(123, 300, 280.5)\nresearch_catalog rewrites=5')
        self.assertEqual((value['Q'], value['static_expected_T'], value['catalog_rewrites']), (803, 280.5, 5))
        self.assertIsNone(parse_emission('No counts')['static_expected_T'])

    def test_catalog_candidate_crossing_threshold_reaches_measurement(self):
        self.campaign.run_job(self.job, through='emit')
        receipts = self._receipts()
        receipts['emit'].update(parse_emission('POSTPASS final=(1000, 110, 110)\nresearch_catalog rewrites=5 native_toffolis_saved=20'))
        result = self.runtime._static_gate(self.job, self.root, receipts, {})
        self.assertTrue(result['passed'])
        self.assertTrue(result['deferred_to_measurement'])
        self.assertEqual(result['pre_catalog_expected_T'], 110)
        self.assertEqual(result['catalog_native_toffolis_saved'], 20)
        self.assertEqual(result['catalog_unit_saving_heuristic_T'], 90)

    def test_live_curve_refusal_does_not_run_submit(self):
        self.campaign.run_job(self.job)
        receipts = self._receipts()
        fake_snapshot = self.root/'snapshot';fake_snapshot.mkdir();(fake_snapshot/'stdout.txt').write_text('[]')
        with patch.object(self.runtime, '_public_rows', return_value=([row('leader', 817, 99)], fake_snapshot)):
            result = self.runtime._send(self.job, self.root/'campaign/jobs/q818', receipts, {})
        self.assertFalse(result['submitted']);self.assertFalse(result['refusal']['retryable'])
        self.assertEqual(self.calls.read_text().splitlines(), ['screen'])

    def _submission_config(self, mode='success'):
        repository = self.root/'challenge'
        shutil.copytree(self.benchmark, repository, ignore=shutil.ignore_patterns('manifest.json'))
        shutil.copytree(self.base/'src/point_add', repository/'src/point_add')
        shutil.copy2(self.base/'NOTICE', repository/'NOTICE')
        for args in [('init', '-q'), ('config', 'user.email', 'fixture@example.invalid'), ('config', 'user.name', 'Fixture'),
                     ('add', '.'), ('commit', '-qm', 'Fixture source')]:
            subprocess.run(['git', '-C', str(repository), *args], check=True, capture_output=True)
        self.job['base_commit'] = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
        self.job['dependencies'][0]['submission_id'] = 'aaaaaaaa'
        parent_row = row('aaaaaaaa-0000-0000-0000-000000000001', 1000, 1000)
        listing = self.root/'service.json';listing.write_text(json.dumps({'submissions': [parent_row]}))
        calls = self.root/'cli-calls.jsonl'
        body = "import sys,json\nfrom pathlib import Path\n"
        body += "listing=Path("+repr(str(listing))+");calls=Path("+repr(str(calls))+")\n"
        body += "with calls.open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        body += "if sys.argv[1]=='submissions':print(listing.read_text())\n"
        if mode == 'conflict':
            body += "elif sys.argv[1]=='submit':print(json.dumps({'error':{'code':'conflict','message':'another submission in flight'}}),file=sys.stderr);sys.exit(1)\n"
        elif mode == 'ambiguous-conflict':
            body += "elif sys.argv[1]=='submit':print(json.dumps({'id':'possible-submission'}));print(json.dumps({'error':{'code':'conflict','message':'another submission in flight'}}),file=sys.stderr);sys.exit(1)\n"
        elif mode == 'unknown':
            body += "elif sys.argv[1]=='submit':print('connection failed',file=sys.stderr);sys.exit(1)\n"
        else:
            body += "elif sys.argv[1]=='submit':\n print(json.dumps({'id':'new-public-id'}))\n listing.write_text(json.dumps({'submissions':[{'id':'new-public-id','solverUsername':'owner','status':'accepted','officialScore':81800,'officialMetrics':{'qubits':818,'toffoli':100}}]}))\n"
        cli = executable(self.tools/'ecdsafail', body)
        self.config.update(benchmark_repo=str(repository), ecdsafail=cli, poll_seconds=.001, poll_timeout=.01)
        self.runtime = RuntimeAdapter(self.config)
        self.campaign = Campaign(self.root/'campaign', self.runtime, owner='cluster')
        return calls

    def test_sealed_submission_consumes_screen_without_second_evaluation(self):
        calls = self._submission_config()
        result = self.campaign.run_job(self.job, through='record', allow_send=True)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.calls.read_text().splitlines(), ['screen'])
        commands = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(sum(command[0] == 'submit' for command in commands), 1)
        self.assertTrue(all(command[0] in ('submissions', 'submit') for command in commands))
        receipts = self._receipts()
        self.assertTrue(receipts['record']['on_frontier'])
        submit = next(command for command in commands if command[0] == 'submit')
        dependencies = json.loads(Path(submit[submit.index('--dependencies-file')+1]).read_text())
        self.assertEqual(dependencies[0]['submissionId'], 'aaaaaaaa-0000-0000-0000-000000000001')

    def test_dependency_prefix_requires_one_public_uuid(self):
        dependencies = [{'submissionId': 'aaaaaaaa', 'rationale': 'parent: arithmetic', 'evidence': 'recorded source'}]
        with self.assertRaisesRegex(ValueError, 'uniquely resolve'):
            resolve_dependencies(dependencies, [])
        with self.assertRaisesRegex(ValueError, 'uniquely resolve'):
            resolve_dependencies(dependencies, [{'id': 'aaaaaaaa-0000-0000-0000-000000000001'},
                                               {'id': 'aaaaaaaa-0000-0000-0000-000000000002'}])

    def test_specific_refusal_is_retryable(self):
        self._submission_config('conflict')
        result = self.campaign.run_job(self.job, through='send', allow_send=True)
        self.assertEqual(result['state'], 'waiting')
        self.assertTrue(result['refusal']['retryable'])
        self.assertEqual(self.calls.read_text().splitlines(), ['screen'])

    def test_unknown_submit_result_cannot_retry(self):
        calls = self._submission_config('unknown')
        with self.assertRaisesRegex(RuntimeError, 'recognized refusal'):
            self.campaign.run_job(self.job, through='send', allow_send=True)
        with self.assertRaises(InterruptedStage):
            self.campaign.run_job(self.job, through='send', allow_send=True)
        commands = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(sum(command[0] == 'submit' for command in commands), 1)
        self.assertEqual(self.calls.read_text().splitlines(), ['screen'])

    def test_response_with_conflict_text_and_possible_submission_cannot_retry(self):
        calls = self._submission_config('ambiguous-conflict')
        with self.assertRaisesRegex(RuntimeError, 'recognized refusal'):
            self.campaign.run_job(self.job, through='send', allow_send=True)
        with self.assertRaises(InterruptedStage):
            self.campaign.run_job(self.job, through='send', allow_send=True)
        commands = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(sum(command[0] == 'submit' for command in commands), 1)

    def test_changed_note_cannot_be_submitted(self):
        calls = self._submission_config()
        self.campaign.run_job(self.job)
        receipts = self._receipts()
        Path(receipts['export']['note_path']).write_text('changed note')
        with self.assertRaises(RegistryTampered):
            self.runtime._send(self.job, self.root/'campaign/jobs/q818', receipts, {})
        self.assertFalse(calls.exists())

    def test_record_reports_actual_curve_membership(self):
        fake_snapshot = self.root/'snapshot';fake_snapshot.mkdir();(fake_snapshot/'stdout.txt').write_text('[]')
        rows = [row('mine', 818, 100), row('better', 817, 99)]
        with patch.object(self.runtime, '_public_rows', return_value=(rows, fake_snapshot)):
            result = self.runtime._record(self.job, self.root, {'send': {'submission_id': 'mine'}}, {})
        self.assertTrue(result['terminal']);self.assertFalse(result['on_frontier'])


if __name__ == '__main__': unittest.main()
