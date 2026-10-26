"""Campaign claims and crashes must not repeat public evaluations or sends."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from llm_circuit_optimiser.frontier.campaign import Campaign, InterruptedStage, JobBusy
from llm_circuit_optimiser.frontier.registry import Registry, RegistryConflict, RegistryTampered, binomial_upper, digest
from llm_circuit_optimiser.frontier.notes import dependencies_for_submission, render_note, validate_note_size

OPS = hashlib.sha256(b'fixed circuit').hexdigest()
JOB_HASH = digest({'job': 'fixed'})


def claim_process(path):
    try:
        return Registry(path).claim_screen(OPS, job_id='child', region='same', job_sha256=JOB_HASH)['token']
    except RegistryConflict:
        return None


def job(identifier='q818'):
    return {'id': identifier, 'base_commit': 'a'*40, 'Q': 818, 'owner': 'cluster', 'region': 'froghop-818',
            'edits': [{'path': 'src/point_add/mod.rs', 'old': 'mfs', 'new': 'mfsur', 'count': 1}],
            'file_copies': [], 'dependencies': [
                {'role': 'parent', 'submission_id': 'parent-id', 'rationale': 'Inherited constructor', 'evidence': 'Pinned source commit'}],
            'expected_T_inference': 9728780, 'must_beat': 9754259}


class FakeAdapter:
    def __init__(self, fail=None, reject=None):
        self.calls = []
        self.fail, self.reject = fail, reject
        self.refusals = 0
        self.pending = 0
        self.evaluator_sha256 = 'e'*64

    def run_stage(self, stage, job, directory, receipts, context):
        self.calls.append(stage)
        if stage == self.fail:
            raise RuntimeError('interrupted instrument')
        result = {'artifacts': []}
        if stage == 'materialize':
            result.update(source_hashes={'src/point_add/mod.rs': 'b'*64},
                          edit_receipts=[{'path': 'src/point_add/mod.rs', 'old': 'mfs', 'new': 'mfsur',
                                          'before_sha256': 'c'*64, 'after_sha256': 'b'*64}])
        elif stage == 'build':
            result['evaluator_sha256'] = self.evaluator_sha256
        elif stage == 'emit':
            (directory/'ops.bin').write_bytes(b'fixed circuit')
            result.update(operation_sha256=OPS, artifacts=[{'path': 'ops.bin', 'sha256': OPS}])
        elif stage in ('static_gate', 'develop'):
            result['passed'] = stage != self.reject
        elif stage == 'screen':
            result.update(operation_sha256=OPS, shots=9024, Q=818, T=9728780, score=818*9728780,
                          evaluator_sha256=self.evaluator_sha256,
                          passed=stage != self.reject, counts={'classical_mismatches': int(stage == self.reject),
                          'phase_garbage_batches': 0, 'ancilla_garbage_batches': 0})
        elif stage == 'audit':
            slot = context['audit_slot']
            failures = 100 if stage == self.reject else 0
            upper = binomial_upper(failures, slot['pairs'], slot['alpha'])
            result.update(operation_sha256=OPS, pairs=slot['pairs'], streams=slot['streams'], alpha=slot['alpha'],
                          label=slot['label'], failed_inputs=failures, upper_bound=upper, qualified=upper <= .001)
        elif stage == 'send':
            if self.refusals:
                self.refusals -= 1
                result.update(submitted=False, refusal={'reason': 'in flight', 'retryable': True})
            else:
                result.update(submitted=True, submission_id='public-id')
        elif stage == 'record':
            result['terminal'] = not self.pending
            self.pending = max(0, self.pending-1)
        return result


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'registry.sqlite3'
        self.registry = Registry(self.path)

    def test_claim_is_atomic_across_processes(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(claim_process, [str(self.path)]*8))
        self.assertEqual(sum(value is not None for value in claims), 1)

    def test_completed_screen_reuses_receipt_but_incomplete_does_not(self):
        claim = self.registry.claim_screen(OPS, job_id='one', region='r', job_sha256=JOB_HASH)
        with self.assertRaises(RegistryConflict):
            self.registry.claim_screen(OPS, job_id='two', region='r', job_sha256=JOB_HASH)
        result = {'operation_sha256': OPS, 'passed': False}
        self.registry.complete_screen(claim['token'], result)
        cached = self.registry.claim_screen(OPS, job_id='two', region='r', job_sha256=JOB_HASH)
        self.assertTrue(cached['reused'])
        self.assertEqual(cached['result'], result)
        with self.assertRaises(RegistryConflict):
            self.registry.complete_screen(claim['token'], {**result, 'passed': True})

    def test_interruption_consumes_screen_and_audit_slot(self):
        screen = self.registry.claim_screen(OPS, job_id='one', region='r', job_sha256=JOB_HASH)
        self.registry.interrupt_screen(screen['token'], 'process died')
        with self.assertRaises(RegistryConflict):
            self.registry.claim_screen(OPS, job_id='one', region='r', job_sha256=JOB_HASH)
        slot = self.registry.reserve_audit(family='family', job_id='one', operation_sha256=OPS, job_sha256=JOB_HASH)
        self.registry.interrupt_audit(slot['token'], 'process died')
        with self.assertRaises(RegistryConflict):
            self.registry.reserve_audit(family='family', job_id='one', operation_sha256=OPS, job_sha256=JOB_HASH)
        other = self.registry.reserve_audit(family='family', job_id='two', operation_sha256=OPS, job_sha256=JOB_HASH)
        self.assertEqual(other['slot'], 1)
        self.assertNotEqual(slot['label'], other['label'])

    def test_family_slots_are_unique_and_finite(self):
        def reserve(i):
            return self.registry.reserve_audit(family='fixed', job_id=f'job-{i}', operation_sha256=OPS, job_sha256=JOB_HASH)
        with ThreadPoolExecutor(max_workers=8) as pool:
            slots = list(pool.map(reserve, range(32)))
        self.assertEqual({r['slot'] for r in slots}, set(range(32)))
        self.assertEqual(len({r['label'] for r in slots}), 32)
        self.assertTrue(all(r['alpha'] == .05/32 for r in slots))
        with self.assertRaises(RegistryConflict):
            reserve(32)

    def test_registry_digest_detects_changed_evidence(self):
        self.registry.claim_screen(OPS, job_id='one', region='r', job_sha256=JOB_HASH)
        with sqlite3.connect(self.path) as con:
            con.execute('UPDATE screens SET record=?', ('{}',))
        with self.assertRaises(RegistryTampered):
            self.registry.get_screen(OPS)

    def test_exact_bound_matches_known_case(self):
        self.assertAlmostEqual(binomial_upper(0, 20480, .05/32), .00031545161047326906, places=12)
        self.assertAlmostEqual(binomial_upper(3, 20480, .05/32), .0006098913058319523, places=10)
        for counts in [(True, 2), (-1, 2), (3, 2), (0, 0)]:
            with self.assertRaises(ValueError):
                binomial_upper(*counts, .05)


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adapter = FakeAdapter()
        self.campaign = Campaign(self.root, self.adapter, owner='cluster')

    def test_resume_uses_receipts_without_repeating_stages(self):
        first = self.campaign.run_job(job())
        self.assertEqual(first['state'], 'ready')
        calls = list(self.adapter.calls)
        again = self.campaign.run_job(job())
        self.assertEqual(first, again)
        self.assertEqual(calls, self.adapter.calls)
        self.campaign.run_job(job(), through='record', allow_send=True)
        self.assertEqual(self.adapter.calls.count('screen'), 1)
        self.assertEqual(self.adapter.calls.count('audit'), 1)
        self.assertEqual(self.adapter.calls.count('send'), 1)

    def test_cross_job_screen_uses_global_completed_evidence(self):
        self.campaign.run_job(job('first'))
        self.campaign.run_job(job('second'))
        self.assertEqual(self.adapter.calls.count('screen'), 1)
        self.assertEqual(self.adapter.calls.count('audit'), 2)

    def test_cross_job_cache_requires_the_same_fixed_evaluator(self):
        self.campaign.run_job(job(), through='screen')
        self.adapter.evaluator_sha256 = 'f'*64
        with self.assertRaisesRegex(ValueError, 'different evaluator'):
            self.campaign.run_job(job('second'), through='screen')
        self.assertEqual(self.adapter.calls.count('screen'), 1)

    def test_changed_contract_blocks_cached_chain(self):
        self.campaign.run_job(job(), through='screen')
        path = self.root/'contract.json'
        value = json.loads(path.read_text())
        value['epsilon'] = .1
        path.write_text(json.dumps(value))
        with self.assertRaises(RegistryTampered):
            self.campaign.run_job(job(), through='screen')

    def test_job_and_artifact_changes_stop_resume(self):
        self.campaign.run_job(job())
        changed = job(); changed['edits'][0]['new'] = 'mfsu'
        with self.assertRaises(RegistryConflict):
            self.campaign.run_job(changed)
        (self.root/'jobs/q818/ops.bin').write_bytes(b'changed circuit')
        with self.assertRaises(RegistryTampered):
            self.campaign.run_job(job())

    def test_receipt_tampering_stops_resume(self):
        self.campaign.run_job(job())
        path = self.root/'jobs/q818/05-screen.json'
        record = json.loads(path.read_text()); record['result']['Q'] = 819
        path.write_text(json.dumps(record))
        with self.assertRaises(RegistryTampered):
            self.campaign.run_job(job())

    def test_failed_screen_stops_audit_and_send(self):
        self.adapter.reject = 'screen'
        result = self.campaign.run_job(job(), through='record', allow_send=True)
        self.assertEqual(result['state'], 'rejected')
        self.assertNotIn('audit', self.adapter.calls)
        self.assertNotIn('send', self.adapter.calls)
        self.campaign.run_job(job(), through='record', allow_send=True)
        self.assertEqual(self.adapter.calls.count('screen'), 1)

    def test_failed_audit_stops_export_and_send(self):
        self.adapter.reject = 'audit'
        self.campaign.run_job(job(), through='record', allow_send=True)
        self.assertNotIn('export', self.adapter.calls)
        self.assertNotIn('send', self.adapter.calls)

    def test_unknown_screen_and_send_are_never_repeated(self):
        self.adapter.fail = 'screen'
        with self.assertRaises(RuntimeError):
            self.campaign.run_job(job())
        self.adapter.fail = None
        with self.assertRaises(InterruptedStage):
            self.campaign.run_job(job())
        self.assertEqual(self.adapter.calls.count('screen'), 1)
        other_root = self.root/'send-case'
        other_adapter = FakeAdapter(fail='send')
        other = Campaign(other_root, other_adapter, owner='cluster')
        with self.assertRaises(RuntimeError):
            other.run_job(job(), through='record', allow_send=True)
        other_adapter.fail = None
        with self.assertRaises(InterruptedStage):
            other.run_job(job(), through='record', allow_send=True)
        self.assertEqual(other_adapter.calls.count('send'), 1)

    def test_finished_send_recovers_without_resubmission(self):
        self.campaign.run_job(job(), through='send', allow_send=True)
        (self.root/'jobs/q818/08-send.json').unlink()
        self.campaign.run_job(job(), through='record', allow_send=True)
        self.assertEqual(self.adapter.calls.count('send'), 1)
        self.assertIn('record', self.adapter.calls)

    def test_completed_registry_recovers_missing_screen_receipts(self):
        self.campaign.run_job(job(), through='screen')
        (self.root/'jobs/q818/05-screen.json').unlink()
        (self.root/'jobs/q818/attempts/screen/0001.finished.json').unlink()
        self.campaign.run_job(job(), through='audit')
        self.assertEqual(self.adapter.calls.count('screen'), 1)

    def test_explicit_refusal_can_retry_but_record_poll_does_not_resend(self):
        self.adapter.refusals = 1
        self.adapter.pending = 1
        first = self.campaign.run_job(job(), through='record', allow_send=True)
        self.assertEqual(first['state'], 'waiting')
        second = self.campaign.run_job(job(), through='record', allow_send=True)
        self.assertEqual(second['state'], 'waiting')
        third = self.campaign.run_job(job(), through='record', allow_send=True)
        self.assertEqual(third['state'], 'complete')
        self.assertEqual(self.adapter.calls.count('send'), 2)
        self.assertEqual(self.adapter.calls.count('screen'), 1)

    def test_send_permission_blocked_jobs_and_owner_filter(self):
        with self.assertRaises(ValueError):
            self.campaign.run_job(job(), through='send')
        blocked = job(); blocked['setting_requests'] = [{'name': 'PEAK0', 'value': 1000}]
        self.assertEqual(self.campaign.run_job(blocked)['state'], 'blocked')
        self.assertEqual(self.adapter.calls, [])
        foreign = job('foreign'); foreign['owner'] = 'mac'
        self.assertEqual(self.campaign.run_jobs([foreign]), [])

    def test_job_lock_refuses_concurrent_execution(self):
        with self.campaign._job_lock(self.root/'jobs/q818'):
            with self.assertRaises(JobBusy):
                self.campaign.run_job(job())

    def test_invalid_audit_bound_never_qualifies(self):
        original = self.adapter.run_stage
        def bad_bound(stage, *args):
            result = original(stage, *args)
            if stage == 'audit': result['upper_bound'] = 0.0
            return result
        self.adapter.run_stage = bad_bound
        with self.assertRaises(ValueError):
            self.campaign.run_job(job())
        self.assertNotIn('export', self.adapter.calls)

    def test_typed_note_preserves_receipts_and_contract(self):
        self.campaign.run_job(job())
        receipts, _ = self.campaign._read_chain(self.root/'jobs/q818', digest(job()))
        inherited = 'INHERITED CONTRACT\nKeep these terms verbatim.'
        note = render_note(job(), receipts, self.campaign.registry.disclosure('froghop-818'),
                           model='Model-free', harness='lco', credits=['The public parent author wrote the arithmetic.'],
                           inherited_contract=inherited)
        for section in ['What changed', 'Measured results', 'Disclosure: variants screened in this region', 'Reproduce',
                        'Prior work and credit', 'Limitations', 'Inherited limiting contract']:
            self.assertIn('## ' + section, note)
        self.assertIn(OPS, note)
        self.assertIn('20,480 input pairs × 2 streams', note)
        self.assertIn('0 failed screens', note)
        self.assertTrue(note.endswith(inherited+'\n'))
        self.assertEqual(dependencies_for_submission(job())[0]['submissionId'], 'parent-id')
        with self.assertRaises(ValueError):
            validate_note_size('short note')
        bad = dict(receipts); bad['audit'] = {**receipts['audit'], 'operation_sha256': 'f'*64}
        with self.assertRaises(ValueError):
            render_note(job(), bad, [], model='Model-free', harness='lco', credits=['Parent author'])


if __name__ == '__main__':
    unittest.main()
