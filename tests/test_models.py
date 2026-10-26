import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

from llm_circuit_optimiser.models import ModelError, load_config, parse_response, select


class ModelTests(unittest.TestCase):
    def test_offline_never_calls_model(self):
        with tempfile.TemporaryDirectory() as d:
            choice, receipt = select([{'policy': 'mfs'}, {'policy': 'mfsur'}],
                                     {'provider': 'offline'}, Path(d), context={})
            self.assertEqual(choice, {'policy': 'mfs'})
            self.assertFalse(receipt['model_called'])

    def test_bad_provider_and_unknown_fields_fail(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config.json'
            for config in ({'provider': 'magic'}, {'provider': 'offline', 'shell': 'echo x'}):
                p.write_text(json.dumps(config))
                with self.assertRaises(ModelError):
                    load_config(p)

    def test_claude_json_reports_identity_and_usage(self):
        raw = {'structured_output': {'action_index': 1, 'reason': 'Use measured cleanup.'},
               'modelUsage': {'test-model': {'inputTokens': 5, 'outputTokens': 2}},
               'usage': {'input_tokens': 5, 'output_tokens': 2}, 'is_error': False}
        answer, meta = parse_response('claude', json.dumps(raw), 'test-model')
        self.assertEqual(answer['action_index'], 1)
        self.assertEqual(meta['resolved_models'], ['test-model'])
        self.assertEqual(meta['usage']['input_tokens'], 5)
        with self.assertRaises(ModelError):
            parse_response('claude', json.dumps(raw), 'other-model')

    def test_invalid_response_does_not_fall_back(self):
        for raw in ('nonsense', '[]', '{"is_error":true}'):
            with self.assertRaises(ModelError):
                parse_response('claude', raw, 'test-model')

    def test_codex_extracts_final_message(self):
        raw = '\n'.join(json.dumps(e) for e in [
            {'type': 'item.completed', 'item': {'type': 'agent_message',
                'text': '{"action_index":0,"reason":"control"}'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 9, 'output_tokens': 2}}])
        answer, meta = parse_response('codex', raw, 'gpt-6.1-sol')
        self.assertEqual(answer['action_index'], 0)
        self.assertIsNone(meta['resolved_models'])

    def test_codex_malformed_events_fail_cleanly(self):
        for raw in ('[]', 'true', '42', '"text"', '{"type":"item.completed","item":null}'):
            with self.assertRaises(ModelError):
                parse_response('codex', raw, 'gpt-6.1-sol')

    def test_failed_call_cannot_repeat_and_overwrite_its_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = {'provider': 'claude', 'model': 'test-model', 'max_calls': 1}
            command = [sys.executable, '-c', 'print("failed attempt");raise SystemExit(1)']
            with patch('llm_circuit_optimiser.models.arguments', return_value=command) as args:
                for _ in range(2):
                    with self.assertRaises(ModelError):
                        select([{'policy': 'none'}], cfg, Path(d), context={}, call_index=0)
                self.assertEqual(args.call_count, 1)
            self.assertIn('failed attempt', (Path(d) / 'stdout.txt').read_text())


if __name__ == '__main__':
    unittest.main()
