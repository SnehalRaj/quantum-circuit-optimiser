import unittest
import sys
import tempfile
from pathlib import Path

from llm_circuit_optimiser.common import pareto, run
from llm_circuit_optimiser.runner import admit_small, audit_qualified, default_paths, evolutionary_order


class RunnerTests(unittest.TestCase):
    def test_front_keeps_both_directions(self):
        rows = [{'Q': q, 'T': t, 'qualified': ok} for q, t, ok in
                [(10, 30, True), (20, 10, True), (20, 20, True), (5, 5, False)]]
        self.assertEqual([(r['Q'], r['T']) for r in pareto(rows)], [(10, 30), (20, 10)])

    def test_inactive_mechanism_cannot_promote(self):
        receipt = {'guards_active': True, 'activation_counts': {'m': 3, 'f': 1, 's': 0, 'u': 0, 'r': 2}}
        with self.assertRaises(ValueError):
            admit_small('mfsur', receipt, inherited_policy='mfs')
        receipt['activation_counts']['u'] = 4
        admitted = admit_small('mfsur', receipt, inherited_policy='mfs')
        self.assertEqual(admitted['inherited_inactive'], ['s'])
        with self.assertRaises(ValueError):
            admit_small('mfsur', receipt)
        receipt['guards_active'] = False
        with self.assertRaises(ValueError):
            admit_small('mfsur', receipt)

    def test_probability_limit_is_exact(self):
        self.assertTrue(audit_qualified({'upper_bound': 0.001}))
        self.assertFalse(audit_qualified({'upper_bound': 0.00101}))
        self.assertFalse(audit_qualified({'upper_bound': 0.0001, 'protocol_valid': False}))
        with self.assertRaises(ValueError):
            audit_qualified({})

    def test_checker_failures_are_data(self):
        with tempfile.TemporaryDirectory() as d:
            output = run([sys.executable, '-c', 'print("failed input");raise SystemExit(1)'],
                         d, Path(d) / 'process.json', accepted_returncodes=(0, 1))
            self.assertIn('failed input', output)
            with self.assertRaises(RuntimeError):
                run([sys.executable, '-c', 'raise SystemExit(2)'], d, Path(d) / 'error.json',
                    accepted_returncodes=(0, 1))

    def test_evolution_counts_parent_cancellation(self):
        parent = {'qualified': True, 'Q': 10, 'T': 20,
                  'candidate': {'target': 'q839', 'policy': 'mfsu', 'cancel_adjacent': True}}
        one = {'target': 'q839', 'policy': 'mfsu', 'cancel_adjacent': False}
        two = {'target': 'q839', 'policy': 'mfs', 'cancel_adjacent': False}
        menu = [two, one]
        evolutionary_order(menu, [parent])
        self.assertEqual(menu[0], one)

    def test_alias_extra_grammars_are_retained(self):
        extra = Path('/tmp/extra-grammar.json')
        paths = default_paths({'backend': 'froghop', 'grammar_files': [str(extra)]})
        self.assertEqual(paths[-1], extra)


if __name__ == '__main__':
    unittest.main()
