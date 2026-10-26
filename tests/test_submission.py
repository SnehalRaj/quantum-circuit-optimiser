from pathlib import Path
import json
import tempfile
import unittest

from llm_circuit_optimiser.common import save, sha256, digest, load
from llm_circuit_optimiser.submission import export_result


class SubmissionTests(unittest.TestCase):
    def test_stale_screen_cannot_override_a_failed_final_audit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stale = {'slot': 0, 'qualified': True, 'audit': {'upper_bound': .0001}}
            current = {**stale, 'qualified': False, 'final_audit': {'upper_bound': .01}}
            save(root / 'trials/000/result.json', stale)
            save(root / 'results.json', {'contract': {'audit_pairs': 4096}, 'rows': [current]})
            with self.assertRaisesRegex(ValueError, 'probability limit'):
                export_result(root, 0, root / 'export')

    def test_screening_only_run_cannot_export(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ops = root / 'ops.bin'
            ops.write_bytes(b'checked circuit')
            source = root / 'source'
            source.mkdir()
            (source / 'NOTICE').write_text('Fixture source credit.')
            audit = {'upper_bound': .0001, 'ops_sha256': sha256(ops), 'streams': 2}
            row = {'slot': 0, 'qualified': True, 'audit': audit,
                   'ops_path': str(ops), 'ops_sha256': sha256(ops),
                   'candidate': {'target': 'custom'}, 'Q': 10, 'T': 20,
                   'grammar_sha256': 'fixture', 'large': {
                       'source_dir': str(source), 'source_hashes': {'NOTICE': sha256(source / 'NOTICE')},
                       'provenance': {'description': 'Preserved fixture attribution. ' * 200}}}
            save(root / 'grammar.json', {})
            save(root / 'results.json', {'contract': {'audit_pairs': 0,
                 'family': {'backend': 'source-conditional'}}, 'rows': [row]})
            with self.assertRaisesRegex(ValueError, 'fresh final audit'):
                export_result(root, 0, root / 'export')
            self.assertFalse((root / 'export').exists())

    def audited_run(self, root):
        ops = root / 'ops.bin'
        ops.write_bytes(b'checked circuit')
        source = root / 'source'
        source.mkdir()
        (source / 'NOTICE').write_text('Fixture source credit.')
        contract = {'audit_pairs': 8192, 'family': {'backend': 'source-conditional'},
                    'tools_sha256': {'circuit_check': 'a' * 64}}
        candidate = {'target': 'custom'}
        audit = {'upper_bound': .0004, 'ops_sha256': sha256(ops), 'streams': 2,
                 'pairs': 8192, 'field_width': 256, 'alpha': .05, 'protocol_valid': True,
                 'checker_binary_sha256': 'a' * 64, 'qubits': 10, 'mean_toffoli': 19,
                 'label': digest(contract) + '/fresh-final/0'}
        row = {'slot': 0, 'qualified': True, 'audit': {**audit, 'mean_toffoli': 20},
               'final_audit': audit, 'ops_path': str(ops), 'ops_sha256': sha256(ops),
               'candidate': candidate, 'Q': 10, 'T': 20, 'grammar_sha256': 'fixture',
               'large': {'source_dir': str(source),
                         'source_hashes': {'NOTICE': sha256(source / 'NOTICE')},
                         'provenance': {'description': 'Preserved fixture attribution. ' * 200}}}
        save(root / 'grammar.json', {})
        save(root / 'selected.json', [{'candidate': candidate, 'ops_sha256': sha256(ops)}])
        save(root / 'results.json', {'contract': contract, 'rows': [row]})
        return row

    def test_passing_final_audit_can_export(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            row = self.audited_run(root)
            record = export_result(root, 0, root / 'export')
            self.assertEqual(load(root / 'export/audit.json'), row['final_audit'])
            self.assertEqual(record['ops_sha256'], sha256(root / 'ops.bin'))
            self.assertIn('"mean_T_on_fresh_inputs": 19',
                          (root / 'export/submission-note.md').read_text())

    def test_development_packet_cannot_be_relabelled_as_a_final_audit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.audited_run(root)
            result = load(root / 'results.json')
            result['rows'][0]['final_audit']['label'] = 'development-packet'
            save(root / 'results.json', result)
            with self.assertRaisesRegex(ValueError, 'frozen final audit'):
                export_result(root, 0, root / 'export')
            self.assertFalse((root / 'export').exists())

    def test_requested_final_audit_is_required(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            save(root / 'results.json', {'contract': {'audit_pairs': 4096}, 'rows': [
                {'slot': 0, 'qualified': True, 'audit': {'upper_bound': .0001}}]})
            with self.assertRaisesRegex(ValueError, 'fresh final audit'):
                export_result(root, 0, root / 'export')


if __name__ == '__main__':
    unittest.main()
