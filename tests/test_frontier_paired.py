from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from llm_circuit_optimiser.frontier.paired import compare_pair, validate_verdicts


def row(index, stream, *, failed=False):
    coordinates = [hex(index + 1), hex(index + 2)]
    return {'idx': index, 'stream': stream,
            'input': {'idx': index, 'target': coordinates, 'addend': ['0x3', '0x4'],
                      'expected': coordinates, 'k_target': str(index+1), 'k_addend': '2'},
            'observed_target': coordinates, 'observed_addend': ['0x3', '0x4'],
            'dirty_qubits': [], 'passed': not failed,
            'failures': {'coordinate': False, 'phase': failed, 'workspace': False, 'addend': False}}


class PairedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [row(i, stream) for i in range(3) for stream in range(2)]
        self.serial = 0

    def fixture(self, rows=None, *, compressed=False, extra=None):
        rows = deepcopy(self.rows if rows is None else rows)
        self.serial += 1
        raw = b''.join(json.dumps(r).encode() + b'\n' for r in rows)
        path = self.root / (str(self.serial) + ('.jsonl.gz' if compressed else '.jsonl'))
        path.write_bytes(gzip.compress(raw, mtime=0) if compressed else raw)
        failed = [r for r in rows if not r['passed']]
        receipt = {'ops_sha256': str(self.serial % 10) * 64, 'packet_sha256': 'a' * 64,
                   'label': 'frozen-paired-fixture', 'pairs': 3, 'streams': 2,
                   'per_input_sha256': hashlib.sha256(raw).hexdigest(),
                   'failed_inputs': len({r['idx'] for r in failed}), 'failed_shots': len(failed),
                   'failed_indices': sorted({r['idx'] for r in failed}), 'protocol': 'fixed-check-v1',
                   'checker_source_sha256': 'b' * 64}
        if compressed:
            receipt['per_input_gz_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        if extra:
            receipt.update(extra)
        return receipt, path

    def compare(self, a, b, **kwargs):
        return compare_pair(a[0], b[0], a[1], b[1], pairs=3, **kwargs)

    def test_unordered_gzip_and_plain_rows_match(self):
        a = self.fixture()
        changed = list(reversed(deepcopy(self.rows)))
        for r in changed:
            r['ops_path'] = '/different/child.bin'
            r['source_sha256'] = 'c' * 64
            r['toffoli_count'] = 9
        b = self.fixture(changed, compressed=True)
        output = self.root / 'comparison.json'
        result = self.compare(a, b, output=output)
        self.assertTrue(result['passed'])
        self.assertTrue(result['all_rows_equal'])
        self.assertEqual(result['per_input_rows_compared'], 6)
        self.assertEqual(result['parent_verdicts']['normalized_sha256'], result['child_verdicts']['normalized_sha256'])
        self.assertEqual(self.compare(a, b, output=output), result)
        self.assertIn('not a proof', result['scope'])
        self.assertIn('not a proof', output.read_text())

    def test_equal_failures_pass_paired_comparison_without_qualification(self):
        rows = deepcopy(self.rows)
        rows[1] = row(0, 1, failed=True)
        a, b = self.fixture(rows), self.fixture(rows, compressed=True)
        result = self.compare(a, b)
        self.assertTrue(result['passed'])
        self.assertEqual(result['failed_inputs'], [1, 1])
        self.assertEqual(result['failed_shots'], [1, 1])
        self.assertNotIn('qualified', result)

    def test_audit_recount_retains_failure_units_and_hashes(self):
        rows = deepcopy(self.rows)
        rows[0] = row(0, 0, failed=True)
        rows[1] = row(0, 1, failed=True)
        receipt, path = self.fixture(list(reversed(rows)), compressed=True)
        result = validate_verdicts(receipt, path, pairs=3)
        self.assertTrue(result['complete'])
        self.assertEqual((result['rows'], result['failed_inputs'], result['failed_shots']), (6, 1, 2))
        self.assertEqual(result['per_input_sha256'], receipt['per_input_sha256'])
        self.assertEqual(result['file_sha256'], receipt['per_input_gz_sha256'])
        self.assertNotIn('qualified', result)

    def test_audit_recount_refuses_missing_rows_and_false_summary(self):
        for fixture in [self.fixture(self.rows[:-1]), self.fixture(extra={'failed_inputs': 1}),
                        self.fixture(extra={'per_input_sha256': 'f' * 64}),
                        self.fixture(extra={'streams': 1})]:
            with self.subTest(receipt=fixture[0]), self.assertRaises(ValueError):
                validate_verdicts(*fixture, pairs=3)

    def test_phase_difference_is_a_failed_comparison_with_example(self):
        rows = deepcopy(self.rows)
        rows[3] = row(1, 1, failed=True)
        result = self.compare(self.fixture(), self.fixture(rows))
        self.assertFalse(result['passed'])
        self.assertFalse(result['failing_shot_sets_equal'])
        self.assertEqual(result['per_input_rows_different'], 1)
        self.assertEqual(result['first_different_rows'][0]['idx'], 1)
        self.assertEqual(result['first_different_rows'][0]['stream'], 1)

    def test_output_and_dirty_qubit_differences_are_compared(self):
        for change in ('coordinate', 'workspace', 'addend'):
            rows = deepcopy(self.rows)
            r = rows[0]
            r['passed'] = False
            r['failures'][change] = True
            if change == 'coordinate':
                r['observed_target'] = ['0xff', '0xff']
            elif change == 'addend':
                r['observed_addend'] = ['0xff', '0xff']
            else:
                r['dirty_qubits'] = [5]
            with self.subTest(change=change):
                self.assertFalse(self.compare(self.fixture(), self.fixture(rows))['passed'])

    def test_duplicate_missing_or_out_of_range_rows_fail(self):
        malformed = [self.rows + [self.rows[0]], self.rows[:-1]]
        wrong = deepcopy(self.rows)
        wrong[0]['idx'] = 3
        wrong[0]['input']['idx'] = 3
        malformed.append(wrong)
        wrong = deepcopy(self.rows)
        wrong[0]['stream'] = 2
        malformed.append(wrong)
        for rows in malformed:
            with self.subTest(rows=len(rows)):
                with self.assertRaises(ValueError):
                    self.compare(self.fixture(), self.fixture(rows))

    def test_packet_label_checker_and_protocol_mismatches_fail(self):
        for key, value in [('packet_sha256', 'f' * 64), ('label', 'different'),
                           ('pairs', 4), ('streams', 1), ('protocol', 'different'),
                           ('checker_source_sha256', 'f' * 64)]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.compare(self.fixture(), self.fixture(extra={key: value}))

    def test_changed_file_or_forged_summary_fails(self):
        a, b = self.fixture(), self.fixture()
        b[1].write_bytes(b[1].read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            self.compare(a, b)
        for extra in ({'per_input_sha256': 'f' * 64}, {'failed_inputs': 2}, {'failed_shots': 1}, {'failed_indices': [1]}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    self.compare(self.fixture(), self.fixture(extra=extra))

    def test_missing_checks_and_inconsistent_pass_flag_fail(self):
        for field in ('observed_target', 'dirty_qubits', 'failures', 'input'):
            rows = deepcopy(self.rows)
            rows[0].pop(field)
            with self.assertRaises(ValueError):
                self.compare(self.fixture(), self.fixture(rows))
        rows = deepcopy(self.rows)
        rows[0]['failures']['phase'] = True
        with self.assertRaisesRegex(ValueError, 'pass verdict'):
            self.compare(self.fixture(), self.fixture(rows))

    def test_streams_for_one_index_require_the_same_input(self):
        rows = deepcopy(self.rows)
        rows[1]['input']['k_target'] = 'different'
        with self.assertRaisesRegex(ValueError, 'different input'):
            self.compare(self.fixture(), self.fixture(rows))

    def test_output_does_not_replace_different_evidence(self):
        output = self.root / 'comparison.json'
        self.compare(self.fixture(), self.fixture(), output=output)
        original = output.read_bytes()
        with self.assertRaisesRegex(ValueError, 'different evidence'):
            self.compare(self.fixture(), self.fixture(), output=output)
        self.assertEqual(output.read_bytes(), original)

    def test_digest_and_boolean_indices_are_strict(self):
        a, b = self.fixture(), self.fixture()
        b[0].pop('per_input_sha256')
        with self.assertRaisesRegex(ValueError, 'per_input_sha256'):
            self.compare(a, b)
        rows = deepcopy(self.rows)
        rows[0]['idx'] = False
        with self.assertRaisesRegex(ValueError, 'input index'):
            self.compare(self.fixture(), self.fixture(rows))
        with self.assertRaisesRegex(ValueError, 'exactly two'):
            self.compare(self.fixture(), self.fixture(), streams=1)


if __name__ == '__main__':
    unittest.main()
