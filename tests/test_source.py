from pathlib import Path
import tempfile
import unittest

from llm_circuit_optimiser.source import init_family, materialize
from llm_circuit_optimiser.families import load_family


class SourceTests(unittest.TestCase):
    def test_template_is_editable_pinned_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = init_family('q839', Path(d) / 'my-family', family_id='my-family')
            family = load_family(p)
            self.assertEqual(family['backend'], 'source-conditional')
            self.assertEqual(family['provenance']['upstream_family'], 'froghop')
            self.assertIn('NOTICE', family['files_sha256'])
            built = materialize(family, 'my-family', Path(d) / 'build', 'mfsur')
            module = Path(built['source_dir']) / 'src/point_add/mod.rs'
            text = module.read_text()
            self.assertEqual(text.count('pub fn build()'), 1)
            self.assertIn('fn lco_source_build()', text)
            self.assertIn('lco_conditional_pass::run_with', text)
            self.assertIn('"mfsur"', text)
            control = materialize(family, 'my-family', Path(d) / 'control', 'none')
            self.assertEqual(control['policy'], 'none')
            module.write_text(text + '\n// Changed after freeze.\n')
            with self.assertRaises(ValueError):
                materialize(family, 'my-family', Path(d) / 'build', 'mfsur')


if __name__ == '__main__':
    unittest.main()
