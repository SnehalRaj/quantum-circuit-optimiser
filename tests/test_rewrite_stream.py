import tempfile
import unittest
from pathlib import Path
from llm_circuit_optimiser.rewrite_stream import NO, lift, normalize, transform, write_ops, read_ops, mine


class RewriteStreamTests(unittest.TestCase):
    def setUp(self):
        self.rule = {'wires': 3, 'before': [['CCX', 0, 1, 2], ['X', 1], ['CCX', 0, 1, 2]],
                     'after': [['CX', 0, 2], ['X', 1]]}
        self.ops = [lift(g, [19, 5, 27]) for g in self.rule['before']]

    def test_relabels_and_rewrites(self):
        stats = {}
        output = list(transform(self.ops, [self.rule], stats))
        self.assertEqual(output, [lift(g, [19, 5, 27]) for g in self.rule['after']])
        self.assertEqual(stats['static_toffoli_saved'], 2)

    def test_preserves_condition_and_measurement_barriers(self):
        for barrier in [(12, 0, NO, NO, 19, 7, NO, NO), (15, 0, NO, NO, NO, NO, 3, NO)]:
            ops = self.ops[:1] + [barrier] + self.ops[1:]
            self.assertEqual(list(transform(ops, [self.rule], {})), ops)
        conditioned = [tuple([*o[:6], 8, o[7]]) for o in self.ops]
        self.assertIsNone(normalize(conditioned))

    def test_invalid_rule_rejected_before_stream_consumption(self):
        with self.assertRaises(ValueError):
            list(transform(self.ops, [{**self.rule, 'after': []}], {}))

    def test_roundtrip_and_bad_count(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ops.zst'
            write_ops(path, iter(self.ops))
            self.assertEqual(list(read_ops(path)), self.ops)
            with self.assertRaises(FileExistsError):
                write_ops(path, iter(self.ops))
            data = bytearray(path.read_bytes())
            data[8:16] = (4).to_bytes(8, 'little')
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                list(read_ops(path))

    def test_task_mining_uses_occurrences(self):
        tasks = mine(self.ops + [(1, 0, NO, NO, NO, NO, NO, 0)] + self.ops, limit=10)
        self.assertEqual(tasks[0]['occurrences'], 2)
        self.assertEqual(tasks[0]['before'], self.rule['before'])
