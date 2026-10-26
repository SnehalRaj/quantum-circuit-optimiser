"""Check campaign ports against frozen records and hostile metadata."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from llm_circuit_optimiser.frontier import public
from llm_circuit_optimiser.frontier.generators import generate_jobs, generate_lilypad_sweep, write_jobs
from llm_circuit_optimiser.frontier.jobs import Job, load_jobs, normalize_job


FIXTURES = Path(__file__).parent / 'fixtures/frontier'


def read(name):
    return json.loads((FIXTURES/name).read_text())


def row(identifier, q, t, *, status='accepted', reason=None, user='solver', created='2026-10-25T00:00:00Z'):
    return {'id': identifier, 'officialScore': q*t, 'officialMetrics': {'qubits': q, 'toffoli': t},
            'status': status, 'rejectionReason': reason, 'solverUsername': user, 'createdAt': created}


class PublicDataTests(unittest.TestCase):
    def test_eligibility_distinguishes_score_rejection_from_failure(self):
        good = row('a', 10, 20)
        score = row('b', 11, 19, status='rejected', reason=public.SCORE_REJECTION)
        failed = row('c', 1, 1, status='rejected', reason='circuit mismatch')
        pending = row('d', 1, 1, status='pending')
        invalid = []
        for bad in [None, 0, -1, True, float('inf'), float('nan'), '5']:
            for field in ['qubits', 'toffoli', 'score']:
                value = copy.deepcopy(good)
                if field == 'score':
                    value['officialScore'] = bad
                else:
                    value['officialMetrics'][field] = bad
                invalid.append(value)
        admitted, curve, selected = public.select_frontier([good, score, failed, pending, {}, None] + invalid)
        self.assertEqual([r['id'] for r in admitted], ['a', 'b'])
        self.assertEqual(curve, [(10, 20), (11, 19)])
        self.assertEqual(selected, admitted)

    def test_same_q_and_equal_t_are_dominated_but_coordinate_ties_survive(self):
        rows = [row('late', 10, 20, created='2026-10-25T02:00:00Z'),
                row('early', 10, 20), row('wider', 11, 20), row('worse', 10, 21), row('tradeoff', 12, 18)]
        admitted, curve, selected = public.select_frontier(rows)
        self.assertEqual(len(admitted), 5)
        self.assertEqual(curve, [(10, 20), (12, 18)])
        self.assertEqual({r['id'] for r in selected}, {'early', 'late', 'tradeoff'})
        snapshot = public.build_snapshot({'submissions': rows})
        self.assertEqual([r['id'] for r in snapshot['curve']], ['early', 'tradeoff'])
        self.assertEqual(public.build_snapshot({'submissions': list(reversed(rows))}), snapshot)

    def test_frozen_curve_coordinates_and_ownership(self):
        frozen = read('curve-20261025T2201Z.json')
        # The frozen file contains curve points, not the complete public response.
        # Synthetic accepted rows test coordinate reconstruction only.
        synthetic = [row(r['id'], r['Q'], r['T'], user=r['user'], created=r['created']) for r in frozen['curve']]
        rebuilt = public.build_snapshot({'submissions': synthetic})
        self.assertEqual(rebuilt['curve'], frozen['curve'])
        self.assertEqual(len(rebuilt['curve']), 142)
        self.assertEqual(sum(r['user'] == 'SnehalRaj' for r in rebuilt['curve']), 15)

    def test_best_below_checks_all_narrower_points(self):
        curve = [{'Q': 12, 'T': 30}, {'Q': 8, 'T': 20}, {'Q': 10, 'T': 25}]
        self.assertIsNone(public.best_at_or_below(curve, 7))
        self.assertEqual(public.best_at_or_below(curve, 12), {'Q': 8, 'T': 20})
        self.assertTrue(public.is_dominated(curve, 12, 20))
        self.assertFalse(public.is_dominated(curve, 12, 19))
        self.assertEqual(public.gaps(curve, 8, 13), [9, 11, 13])
        self.assertEqual(public.gaps([], 2, 4), [2, 3, 4])
        self.assertEqual(public.gaps([]), [])
        with self.assertRaises(ValueError):
            public.gaps(curve, 5, 4)
        with self.assertRaises(ValueError):
            public.best_at_or_below(curve, float('nan'))

    def test_diff_reports_gains_and_losses(self):
        old = {'curve': [{'id': 'a', 'Q': 10, 'T': 20, 'user': 'other'}],
               'mine': [{'id': 'ours1', 'state': 'on curve'}]}
        new = {'curve': [{'id': 'b', 'Q': 10, 'T': 19, 'user': 'SnehalRaj'}],
               'mine': [{'id': 'ours1', 'state': 'superseded'}, {'id': 'ours2', 'state': 'on curve'}]}
        diff = public.diff_snapshots(old, new)
        self.assertEqual([r['id'] for r in diff['added']], ['b'])
        self.assertEqual([r['id'] for r in diff['dropped']], ['a'])
        self.assertEqual([r['id'] for r in diff['gained']], ['ours2'])
        self.assertEqual([r['id'] for r in diff['lost']], ['ours1'])

    def test_sync_pins_raw_response_without_network_or_overwrite(self):
        raw = json.dumps({'submissions': [row('a', 10, 20)]}, indent=3) + '\n'
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, raw, '')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'public.json'
            result = public.sync_public(output, runner=runner, now='fixed')
            self.assertEqual(output.read_text(), raw)
            self.assertEqual(result['receipt']['sha256'], hashlib.sha256(raw.encode()).hexdigest())
            self.assertEqual(result['receipt']['utc'], 'fixed')
            with self.assertRaises(FileExistsError):
                public.sync_public(output, runner=runner)
            self.assertEqual(output.read_text(), raw)
        self.assertEqual(calls[0][0], ['ecdsafail', 'submissions', '--all', '--json'])
        def failed(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, '', 'instrument error')
        with self.assertRaises(RuntimeError):
            public.sync_public(runner=failed)


class CampaignJobTests(unittest.TestCase):
    def setUp(self):
        self.curve = read('curve-20261025T2201Z.json')
        self.original = read('jobs-20261025T2201Z.json')['jobs']

    def test_froghop_generator_matches_every_frozen_field(self):
        generated = generate_jobs(self.curve)
        self.assertEqual([{key: job[key] for key in original}
                          for job, original in zip(generated, self.original)], self.original)
        self.assertEqual(len(generated), len(self.original))
        self.assertEqual(len(self.original), 37)
        self.assertEqual(sum(j['owner'] == 'cluster' for j in self.original), 33)

    def test_historical_jobs_cannot_reach_a_second_screen(self):
        from llm_circuit_optimiser.frontier.campaign import Campaign
        class NoExecution:
            def run_stage(self, *args):
                raise AssertionError('Historical jobs must not execute any stage')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'jobs.json'
            document = write_jobs(path, self.curve)
            jobs = load_jobs(path, owner='cluster')
            self.assertEqual(len(jobs), 33)
            self.assertTrue(all(job['state'] == 'blocked' and job['blocked_reasons'] for job in jobs),
                            'The historical handoff must not become an active job queue')
            self.assertTrue(all(job['metadata']['purpose'] == 'historical-comparison' for job in jobs))
            campaign = Campaign(Path(tmp)/'campaign', NoExecution(), owner='cluster')
            result = campaign.run_job(jobs[0], through='screen')
            self.assertEqual(result['state'], 'blocked')

    def test_old_job_files_and_renamed_handoff_jobs_stay_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'old-jobs.json'
            old = copy.deepcopy(self.original)
            old[0]['id'] = 'renamed-historical-candidate'
            path.write_text(json.dumps({'jobs': old}))
            jobs = load_jobs(path)
            self.assertTrue(all(job['state'] == 'blocked' and job['blocked_reasons'] for job in jobs),
                            'Previously exported handoff files also need the historical screen block')
            self.assertEqual(jobs[0]['metadata']['origin_commit'], 'da182c4')

    def test_lilypad_generator_matches_unchanged_script_on_same_snapshot(self):
        expected = read('lilypad-sweep-20261025T2201Z.json')['jobs']
        self.assertEqual(generate_lilypad_sweep(self.curve), expected)
        self.assertEqual(len(expected), 111)

    def test_new_curve_changes_threshold_but_not_source_cost_inference(self):
        updated = copy.deepcopy(self.curve)
        for r in updated['curve']:
            if r['Q'] == 821:
                r.update(T=1, id='unrelated-source', user='other')
        before = next(j for j in generate_jobs(self.curve) if j['Q'] == 821)
        after = next(j for j in generate_jobs(updated) if j['Q'] == 821)
        self.assertEqual(before['expected_T_inference'], after['expected_T_inference'])
        self.assertEqual(after['must_beat'], 1)
        self.assertFalse(after['live'])

    def test_job_normalization_preserves_order_hashes_and_roles(self):
        raw = next(j for j in self.original if j['id'] == 'q847-g8-cat')
        job = Job.from_dict(raw)
        data = job.to_dict()
        self.assertEqual([e['old'] for e in data['edits']], [e[1] for e in raw['edits']])
        self.assertTrue(all(e['count'] == 1 for e in data['edits']))
        self.assertEqual({f['path']: f['sha256'] for f in data['file_copies']}, raw['files_from_commit_4073f09c'])
        roles = {d['role'] for d in data['dependencies']}
        self.assertTrue({'parent', 'lever', 'catalog', 'setting', 'integrated_base'} <= roles)
        self.assertEqual(data['metadata']['pass_chance_inference'], raw['pass_chance_inference'])
        self.assertEqual(normalize_job(data), data)
        self.assertEqual(job.sha256, Job.from_dict(data).sha256)

    def test_lilypad_descriptions_cannot_become_executable_edits(self):
        normalized = [normalize_job(j) for j in generate_lilypad_sweep(self.curve)]
        self.assertTrue(all(j['state'] == 'blocked' and j['blocked_reasons'] for j in normalized))
        self.assertTrue(all(j['setting_requests'] and not j['edits'] for j in normalized))
        self.assertEqual(normalize_job(normalized[0]), normalized[0])

    def test_jobs_reject_unsafe_paths_hashes_counts_and_ids(self):
        baseline = normalize_job(self.original[0])
        changes = [lambda d: d.update(id='../escape'), lambda d: d.update(Q=True),
                   lambda d: d.update(base_commit='main; command'),
                   lambda d: d['edits'][0].update(path='../../escape'),
                   lambda d: d['edits'][0].update(path='/tmp/file'),
                   lambda d: d['edits'][0].update(path='src/point_add/../other'),
                   lambda d: d['edits'][0].update(path='src/point_add/./mod.rs'),
                   lambda d: d['edits'][0].update(count=True),
                   lambda d: d['edits'][0].update(count=0),
                   lambda d: d.update(expected_T_inference=float('nan')),
                   lambda d: d.update(base_files_sha256={'src/point_add/mod.rs': 'bad'}),
                   lambda d: d.update(state='unknown')]
        for mutate in changes:
            data = copy.deepcopy(baseline)
            mutate(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                normalize_job(data)

    def test_aliases_are_read_but_conflicts_fail(self):
        data = normalize_job(self.original[0])
        data['edits'][0]['file'] = data['edits'][0].pop('path')
        self.assertEqual(normalize_job(data)['edits'][0]['path'], self.original[0]['edits'][0][0])
        data['edits'][0]['path'] = 'src/point_add/other.rs'
        with self.assertRaises(ValueError):
            normalize_job(data)

    def test_collection_fields_are_strictly_typed(self):
        baseline = normalize_job(self.original[0])
        for field, bad in [('edits', {}), ('file_copies', {}), ('dependencies', {}),
                           ('blocked_reasons', 'blocked'), ('setting_requests', ['description']),
                           ('files_from_commit_4073f09c', [])]:
            value = copy.deepcopy(baseline)
            value[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                normalize_job(value)

    def test_load_filters_owner_and_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'jobs.json'
            write_jobs(path, self.curve)
            self.assertEqual(len(load_jobs(path, owner='mac')), 4)
            self.assertEqual(len(load_jobs(path, owner='cluster')), 33)
            path.write_text(json.dumps({'jobs': self.original[:1]*2}))
            with self.assertRaises(ValueError):
                load_jobs(path)


if __name__ == '__main__':
    unittest.main()
