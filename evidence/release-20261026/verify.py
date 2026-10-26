#!/usr/bin/env python3
"""Recount the compact verdict records and check the recorded probability bounds."""
import gzip
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def upper_bound(k, n, alpha):
    low, high = 0.0, 1.0
    for _ in range(90):
        p = (low + high) / 2
        term = math.exp(n * math.log1p(-p))
        total = term
        for index in range(k):
            term *= (n - index) * p / ((index + 1) * (1 - p))
            total += term
        if total > alpha:
            low = p
        else:
            high = p
    return high


def main():
    summary = json.loads((ROOT / 'results.json').read_text())
    checked = 0
    packets = set()
    for mode in summary['modes']:
        assert mode['qualified'] and mode['reproduced_exact'] == 1
        assert mode['ops_sha256'] == summary['expected_ops_sha256']
        if mode['provider'] != 'offline':
            assert mode['model']['success'] and mode['model']['api_keys_removed']
        for audit in mode['audits']:
            data = (ROOT / audit['verdict_projection']).read_bytes()
            assert sha256(data) == audit['projection_sha256']
            pairs, streams = audit['pairs'], audit['streams']
            failed, seen = set(), set()
            shots = 0
            for line in gzip.decompress(data).decode().splitlines():
                index, stream, passed, *flags = map(int, line.split('\t'))
                assert 0 <= index < pairs and 0 <= stream < streams
                assert (index, stream) not in seen
                seen.add((index, stream))
                assert len(flags) == 4 and all(value in (0, 1) for value in [passed, *flags])
                assert bool(passed) == (not any(flags))
                if not passed:
                    failed.add(index)
                    shots += 1
            assert len(seen) == pairs * streams
            assert len(failed) == audit['failed_inputs']
            assert shots == audit['failed_shots']
            bound = upper_bound(len(failed), pairs, audit['alpha'])
            assert abs(bound - audit['upper_bound']) < 1e-11
            assert bound <= 0.001 and audit['protocol_valid']
            assert audit['packet_sha256'] not in packets
            packets.add(audit['packet_sha256'])
            checked += len(seen)
    source = json.loads((ROOT / 'execution-sources.json').read_text())
    repository = ROOT.parents[1]
    for name, expected in source.items():
        assert sha256((repository / name).read_bytes()) == expected, name
    print(json.dumps({'passed': True, 'modes': len(summary['modes']),
                      'audits': len(packets), 'stream_verdicts': checked,
                      'execution_sources': len(source)}, indent=2))


if __name__ == '__main__':
    main()
