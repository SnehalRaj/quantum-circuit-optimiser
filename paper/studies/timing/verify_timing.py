"""Recount saved inputs and outcomes without executing a circuit again."""
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
protocol = json.loads((HERE/'protocol.json').read_text())
expected = json.loads((HERE/'protocol.json.sha256').read_text())['sha256']
assert hashlib.sha256((HERE/'protocol.json').read_bytes()).hexdigest() == expected
for name, pinned in protocol['source_sha256'].items():
    assert hashlib.sha256((HERE/name).read_bytes()).hexdigest() == pinned
checks = []
for request in protocol['requests']:
    folder = HERE / request['id']
    result = json.loads((folder/'result.json').read_text())
    assert result['pairs'] == request['pairs'] == 100000
    assert result['streams'] == request['streams'] == 2
    assert result['operation_sha256'] == request['expected_operation_sha256']
    assert result['label'] == request['label']
    bad, shots, seen = set(), 0, set()
    if request['id'] == 'point_addition':
        with gzip.open(folder/'per-input.jsonl.gz', 'rt') as stream:
            for line in stream:
                row = json.loads(line)
                key = (row['idx'], row['stream'])
                assert key not in seen
                seen.add(key)
                assert 0 <= key[0] < 100000 and key[1] in [0, 1]
                assert row['passed'] == (not any(row['failures'].values()))
                if not row['passed']:
                    bad.add(key[0]); shots += 1
        assert len(seen) == 200000
        raw = result['raw']
        assert len(bad) == raw['failed_inputs']
        assert shots == raw['failed_shots']
        assert all(raw['timings'][k] >= 0 for k in ['load_seconds', 'preparation_seconds', 'simulation_seconds'])
        assert sum(raw['timings'][k] for k in ['load_seconds', 'preparation_seconds', 'simulation_seconds']) <= raw['timings']['total_seconds']
    else:
        h, n_inputs, offsets = hashlib.sha256(), 0, []
        with gzip.open(folder/'inputs.jsonl.gz', 'rb') as stream:
            for line in stream:
                h.update(line)
                row = json.loads(line)
                assert row['start'] == n_inputs
                assert len(row['x']) == len(row['y'])
                assert row['streams'] == 2
                assert len(row['x']) <= request['batch_size']
                offsets.append(row['start'])
                n_inputs += len(row['x'])
        assert n_inputs == 100000
        assert h.hexdigest() == result['input_sha256']
        with gzip.open(folder/'outcomes.jsonl.gz', 'rt') as stream:
            for line in stream:
                row = json.loads(line)
                i = row['index']
                assert i not in seen
                seen.add(i)
                assert len(row['stream_failed']) == 2
                assert bool(row['failed_channels']) == any(row['stream_failed'])
                if any(row['stream_failed']):
                    bad.add(i)
                shots += sum(row['stream_failed'])
        assert seen == set(range(100000))
        assert len(bad) == result['failures']['failed_inputs']
        assert shots == result['failures']['failed_shots']
    checks.append({'id': request['id'], 'inputs': 100000, 'shots': 200000,
                   'failed_inputs_recounted': len(bad), 'failed_shots_recounted': shots,
                   'operation_hash_matches_protocol': True, 'result_source': str(folder/'result.json'),
                   'result_sha256': hashlib.sha256((folder/'result.json').read_bytes()).hexdigest()})
out = {'verified_utc': datetime.now(timezone.utc).isoformat(), 'protocol_sha256': expected,
       'scope': 'Independent record recount and binding checks. No circuit rerun.',
       'frozen_source_files_checked': len(protocol['source_sha256']), 'checks': checks}
with (HERE/'verification.json').open('x') as stream:
    json.dump(out, stream, indent=2); stream.write('\n')
print(json.dumps(out, indent=2))
