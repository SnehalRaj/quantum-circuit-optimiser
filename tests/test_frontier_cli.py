"""Check command failures and owner selection before expensive execution."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm_circuit_optimiser.cli import main, parser
from llm_circuit_optimiser.frontier.cli import dispatch


class FrontierCliTests(unittest.TestCase):
    def test_missing_replay_evidence_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()) as output:
            expected = Path(__file__).resolve().parents[1]/'evidence/frontier/replay'
            code = main(['frontier', 'compare-replay', temp, '--expected', str(expected)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'incomplete')

    def test_blocked_jobs_return_failure_to_the_shell(self):
        for state, expected in [('ready', 0), ('complete', 0), ('blocked', 2),
                                ('waiting', 2), ('rejected', 2), ('interrupted', 2)]:
            with self.subTest(state=state), patch(
                'llm_circuit_optimiser.frontier.cli.dispatch', return_value=[{'state': state}]
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['frontier', 'run', 'jobs.json', '--config', 'config.json',
                                       '--owner', 'worker', '--out', 'work/run']), expected)

    def test_empty_job_selection_cannot_report_a_successful_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'config.json').write_text('{}')
            (root/'jobs.json').write_text('[]')
            args = parser().parse_args(['frontier', 'run', str(root/'jobs.json'),
                '--config', str(root/'config.json'), '--owner', 'worker', '--out', str(root/'out')])
            with self.assertRaisesRegex(ValueError, 'No jobs match'):
                dispatch(args)
            self.assertFalse((root/'out').exists())

    def test_submit_needs_the_explicit_execution_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp)/'config.json'
            config.write_text('{}')
            args = parser().parse_args(['frontier', 'run', 'missing.json', '--config', str(config),
                                       '--out', temp, '--owner', 'worker', '--through', 'send'])
            with self.assertRaisesRegex(ValueError, 'execute-submit'):
                dispatch(args)

    def test_tree_discovery_receives_owner_and_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp)/'snapshot.json'
            snapshot.write_text('{"submissions": []}')
            args = parser().parse_args(['public', 'trees', str(snapshot), '--git-repo', temp,
                                       '--out', temp, '--owner', 'cluster', '--limit', '12'])
            with patch('llm_circuit_optimiser.frontier.discovery.discover_trees', return_value={}) as run:
                dispatch(args)
            self.assertEqual(run.call_args.kwargs['owner'], 'cluster')
            self.assertEqual(run.call_args.kwargs['limit'], 12)

    def test_grammar_paths_are_resolved_before_runtime_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp)/'config.json'
            config.write_text('{}')
            args = parser().parse_args(['frontier', 'run', 'jobs.json', '--config', str(config),
                '--grammar', 'grammars/frontier/catalog.json', '--owner', 'worker', '--out', temp])
            with patch('llm_circuit_optimiser.frontier.jobs.load_jobs', return_value=[{'id': 'one'}]), \
                 patch('llm_circuit_optimiser.frontier.runtime.RuntimeAdapter') as adapter, \
                 patch('llm_circuit_optimiser.frontier.campaign.Campaign') as campaign:
                dispatch(args)
            self.assertEqual(adapter.call_args.args[0]['grammar_files'],
                             [str(Path('grammars/frontier/catalog.json').resolve())])
            campaign.return_value.run_jobs.assert_called_once()


if __name__ == '__main__':
    unittest.main()
