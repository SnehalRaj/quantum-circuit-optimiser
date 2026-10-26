"""Check full-width setting admission without weakening shared rewrite guards."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from llm_circuit_optimiser.grammar import compose, GrammarError

ROOT = Path(__file__).resolve().parents[1]


def setting():
    base = json.loads((ROOT / 'grammars/base.json').read_text())['rules'][0]
    rule = copy.deepcopy(base)
    rule.update(id='froghop.pool_one', kind='setting', status='executable',
                title='Pool size', description='Change the pinned full-width pool literal.',
                families=['froghop'], requires=[],
                parameters={'guards': {'type': 'boolean', 'default': True},
                            'value': {'type': 'integer', 'default': 18, 'minimum': 1, 'maximum': 256}},
                bindings={'fast': None, 'fast_reason': 'secp256k1 layout literal',
                          'large': {'froghop': 'frontier.counted_setting'}})
    return rule


class FrontierGrammarTests(unittest.TestCase):
    def compile_rule(self, rule, directory):
        path = Path(directory) / 'rule.json'
        path.write_text(json.dumps({'schema_version': 1, 'id': 'frontier-test', 'revision': 1,
                                    'definitions': {}, 'rules': [rule]}))
        return compose([path])

    def test_large_only_setting_admits_typed_frontier_action(self):
        with tempfile.TemporaryDirectory() as d:
            grammar = self.compile_rule(setting(), d)
            self.assertIn('froghop.pool_one', grammar.availability('froghop')['active'])
            actions = grammar.validate_frontier_actions([
                {'rule': 'froghop.pool_one', 'parameters': {'value': 19}}], 'froghop')
            self.assertIsNone(actions[0]['fast_binding'])
            self.assertEqual(actions[0]['fast_reason'], 'secp256k1 layout literal')
            with self.assertRaisesRegex(GrammarError, 'frontier runner'):
                grammar.validate_candidate({'target': 'q839', 'actions': [
                    {'rule': 'froghop.pool_one', 'parameters': {'value': 19}}]}, 'froghop')

    def test_missing_reason_and_unknown_binding_fail_admission(self):
        with tempfile.TemporaryDirectory() as d:
            rule = setting()
            del rule['bindings']['fast_reason']
            with self.assertRaisesRegex(GrammarError, 'reason'):
                self.compile_rule(rule, d)
            rule = setting()
            rule['bindings']['large']['froghop'] = 'arbitrary.command'
            self.assertEqual(self.compile_rule(rule, d).availability('froghop')['active'], [])

    def test_shared_rewrite_still_needs_fast_binding(self):
        with tempfile.TemporaryDirectory() as d:
            rule = setting()
            rule['kind'] = 'rewrite'
            self.assertEqual(self.compile_rule(rule, d).availability('froghop')['active'], [])

    def test_setting_parameters_and_guards_remain_strict(self):
        with tempfile.TemporaryDirectory() as d:
            grammar = self.compile_rule(setting(), d)
            for params in [{'value': True}, {'value': 0}, {'guards': False}, {'typo': 19}]:
                with self.subTest(params=params), self.assertRaises(GrammarError):
                    grammar.validate_frontier_actions([{'rule': 'froghop.pool_one', 'parameters': params}], 'froghop')


if __name__ == '__main__':
    unittest.main()
