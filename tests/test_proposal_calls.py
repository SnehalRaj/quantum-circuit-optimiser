import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from llm_circuit_optimiser.common import save, digest
from llm_circuit_optimiser.proposal_calls import request, decode


class ProposalCallTests(unittest.TestCase):
    def test_missing_usage_remains_unknown(self):
        answer, meta = decode('claude', '{"structured_output":{"proposals":[""]}}', 'model')
        self.assertEqual(answer, {'proposals': ['']})
        self.assertIsNone(meta['usage'])

    def test_rejects_tool_calls(self):
        with self.assertRaises(ValueError):
            decode('codex', '{"type":"item.completed","item":{"type":"command_execution"}}', 'model')

    def test_failed_call_consumes_directory(self):
        with tempfile.TemporaryDirectory() as d:
            save(Path(d) / 'receipt.json', {'success': False})
            with patch('subprocess.Popen') as spawn, self.assertRaises(ValueError):
                request({}, {'provider': 'claude', 'model': 'model'}, d, 0)
            spawn.assert_not_called()

    def test_changed_answer_or_prompt_blocks_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            answer = {'proposals': ['X 0']}
            save(Path(d) / 'answer.json', answer)
            save(Path(d) / 'receipt.json', {'success': True, 'request_hash': 'wrong', 'answer_hash': digest(answer)})
            with patch('subprocess.Popen') as spawn, self.assertRaises(ValueError):
                request({}, {'provider': 'claude', 'model': 'model'}, d, 0)
            spawn.assert_not_called()

    def test_allowance(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            request({}, {'provider': 'claude', 'model': 'model', 'max_calls': 1}, d, 1)

    def test_malformed_envelopes_become_value_errors(self):
        for raw in ['[]', '{"type":"item.completed","item":null}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode('codex', raw, 'model')

    def test_failed_answer_keeps_known_usage(self):
        import json
        from llm_circuit_optimiser.common import load
        response={'structured_output': {'proposals':17}, 'usage':{'input_tokens':123,'output_tokens':456}}
        with tempfile.TemporaryDirectory() as d, patch('llm_circuit_optimiser.proposal_calls.arguments', return_value=['/bin/echo']), patch('subprocess.Popen') as spawn:
            spawn.return_value.communicate.return_value=(json.dumps(response),'')
            spawn.return_value.returncode=0
            with self.assertRaises(ValueError):
                request({}, {'provider':'claude','model':'model'}, d, 0)
            self.assertEqual(load(Path(d)/'receipt.json')['usage'], response['usage'])
