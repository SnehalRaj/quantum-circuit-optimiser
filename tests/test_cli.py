import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from llm_circuit_optimiser.cli import main


class CliTests(unittest.TestCase):
    def test_plan_lists_the_actual_search_menu(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(['plan', '--family', 'froghop', '--targets', 'q839', '--out', d])
            self.assertEqual(code, 0)
            plan = json.loads((Path(d) / 'plan.json').read_text())
            self.assertEqual(len(plan['candidates']), 64)
            self.assertEqual({c['cancel_adjacent'] for c in plan['candidates']}, {False, True})
            self.assertEqual(plan['model_calls'], 0)


if __name__ == '__main__':
    unittest.main()
