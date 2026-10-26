import unittest
from llm_circuit_optimiser.rewrite_rules import parse_program, signature, certify, source_form


class RewriteRuleTests(unittest.TestCase):
    def test_source_and_grammar_have_equal_capacity(self):
        gates = [['CCX', 2, 0, 1], ['CZ', 0, 2], ['X', 1]]
        self.assertEqual(parse_program(source_form(gates), 'source', 3), gates)
        self.assertEqual(parse_program('CCX 2 0 1; CZ 0 2; X 1', 'grammar', 3), gates)

    def test_proves_factoring_for_all_inputs(self):
        before = [['CCX', 2, 0, 1], ['X', 0], ['CCX', 2, 0, 1]]
        after = [['CX', 2, 1], ['X', 0]]
        proof = certify(before, after, 3)
        self.assertTrue(proof['equivalent'])
        self.assertTrue(proof['accepted'])
        self.assertEqual(proof['toffoli_saved'], 2)

    def test_phase_mismatch_is_a_counterexample(self):
        proof = certify([['CCZ', 0, 1, 2]], [], 3)
        self.assertFalse(proof['equivalent'])
        self.assertEqual(proof['counterexample']['input'], 7)

    def test_rejects_equal_cost(self):
        self.assertFalse(certify([['CCX', 2, 0, 1]], [['CCX', 2, 0, 1]], 3)['accepted'])

    def test_source_never_executes_calls(self):
        for code in ["import os\ndef rewrite(q): return []", "def rewrite(q): return open('bad')", "def rewrite(q): return [X(q[True])]", "def rewrite(q): return [__import__('os').system('bad')]", "def rewrite(q):\n q[0]=1\n return []"]:
            with self.subTest(code=code), self.assertRaises(ValueError):
                parse_program(code, 'source', 3)

    def test_rejects_aliases_bounds_and_unknown_gates(self):
        for text in ['CX 0 0', 'X -1', 'X 4', 'H 0', 'X 0;; X 1', 'CCX 0 1', 'X 0; ' * 8]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_program(text, 'grammar', 4)

    def test_independent_truth_tables(self):
        self.assertEqual(signature([['CX', 1, 0]], 2), (0, 6, 4, 2))
        self.assertEqual(signature([['CZ', 1, 0]], 2), (0, 2, 4, 7))
        self.assertEqual(signature([['SWAP', 0, 1]], 2), (0, 4, 2, 6))
        self.assertEqual(signature([['NEG']], 1), (1, 3))

    def test_nonlinear_lower_bound_excludes_exhausted_windows(self):
        from llm_circuit_optimiser.rewrite_rules import nonlinear_rank
        self.assertEqual(nonlinear_rank([['CCX', 0, 1, 2], ['CCX', 3, 1, 2]], 4), 1)
        self.assertEqual(nonlinear_rank([['CCX', 0, 1, 2], ['CCX', 3, 1, 4]], 5), 2)
        self.assertEqual(nonlinear_rank([['CCX', 0, 1, 2], ['X', 1], ['CCX', 0, 1, 2]], 3), 0)
