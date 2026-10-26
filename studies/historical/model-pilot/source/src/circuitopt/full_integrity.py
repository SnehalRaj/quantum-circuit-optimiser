"""Validate the complete evidence behind a native test result."""
import gzip
import hashlib
import json
from pathlib import Path

from . import full_native as native


def seal(path):
    path = Path(path)
    proof = path.with_name(path.name + '.sha256')
    digest = native.file_hash(path)
    if proof.exists():
        native.assert_hash(path, proof.read_text().strip())
    else:
        with proof.open('x') as output:
            output.write(digest + '\n')
    return digest


def read_sealed(path):
    path = Path(path)
    native.assert_hash(path, path.with_name(path.name + '.sha256').read_text().strip())
    return json.loads(path.read_text())


def validate_contract(contract, *, pairs, circuit_ids, stage, streams, epsilon, confidence):
    for key, expected in [('pairs', pairs), ('circuit_ids', sorted(circuit_ids)), ('stage', stage)]:
        if contract.get(key) != expected:
            raise ValueError(f'Audit contract {key} changed')
    for key, expected in [('streams', streams), ('epsilon', epsilon), ('confidence', confidence)]:
        if key in contract and contract[key] != expected:
            raise ValueError(f'Audit contract {key} changed')
    chunks = contract.get('chunks')
    if not isinstance(chunks, list) or not chunks:
        raise ValueError('Provide the complete audit chunk plan')
    if any(not isinstance(c, dict) or type(c.get('pairs')) is not int or not 0 < c['pairs'] <= 1000000 for c in chunks):
        raise ValueError('Invalid audit chunk size')
    if sum(c['pairs'] for c in chunks) != pairs:
        raise ValueError('Audit chunk total differs from the declared denominator')
    labels = [c.get('label') for c in chunks]
    if any(not isinstance(label, str) or not label for label in labels) or len(set(labels)) != len(labels):
        raise ValueError('Each audit chunk needs a unique nonempty label')
    if not 0 < confidence < 1 or not 0 < epsilon < 1 or type(streams) is not int or streams < 1:
        raise ValueError('Invalid qualification settings')
    return contract


def read_outcomes(path, *, pairs, streams):
    seen = [0] * pairs
    input_digests = [None] * pairs
    bad_inputs = set()
    channel_inputs = {key: set() for key in ['coordinate', 'phase', 'workspace', 'addend']}
    channel_shots = {key: 0 for key in channel_inputs}
    failed_shots = rows = 0
    content = hashlib.sha256()
    packet = hashlib.sha256()
    failure_records = hashlib.sha256()
    last = (-1, -1)
    with gzip.open(path, 'rb') as source:
        for line in source:
            content.update(line)
            row = json.loads(line)
            idx, sid = row['idx'], row['stream']
            if type(idx) is not int or type(sid) is not int or not 0 <= idx < pairs or not 0 <= sid < streams:
                raise ValueError('Outcome index is outside the declared cohort')
            if seen[idx] & (1 << sid):
                raise ValueError('Duplicate input and measurement stream')
            if (idx, sid) <= last:
                raise ValueError('Outcome rows are not in the fixed evaluator order')
            last = idx, sid
            seen[idx] |= 1 << sid
            encoded = json.dumps(row['input'], sort_keys=True, separators=(',', ':')).encode()
            digest = hashlib.sha256(encoded).digest()
            if input_digests[idx] is None:
                input_digests[idx] = digest
            elif input_digests[idx] != digest:
                raise ValueError('Measurement streams use different input values')
            if sid == 0:
                packet.update(encoded + b'\n')
            flags = row['failures']
            if set(flags) != set(channel_inputs) or any(type(v) is not bool for v in flags.values()):
                raise ValueError('Invalid failure channels')
            bad = any(flags.values())
            if type(row['passed']) is not bool or row['passed'] != (not bad):
                raise ValueError('Outcome pass flag disagrees with its failure channels')
            if bad:
                bad_inputs.add(idx)
                failed_shots += 1
                failure_records.update(json.dumps(row, sort_keys=True, separators=(',', ':')).encode() + b'\n')
            for key, value in flags.items():
                if value:
                    channel_inputs[key].add(idx)
                    channel_shots[key] += 1
            rows += 1
    if rows != pairs * streams or any(mask != (1 << streams) - 1 for mask in seen):
        raise ValueError('Saved outcomes do not cover the declared cohort')
    return {'rows': rows, 'pairs': pairs, 'streams': streams,
            'failed_inputs': len(bad_inputs), 'failed_shots': failed_shots,
            'failed_indices': sorted(bad_inputs),
            'channel_failed_inputs': {key: len(value) for key, value in channel_inputs.items()},
            'channel_failed_shots': channel_shots, 'inputs_sha256': content.hexdigest(),
            'packet_sha256': packet.hexdigest(), 'failure_records_sha256': failure_records.hexdigest()}


def validate_evaluation(root, directory, artifact, *, label, pairs, streams):
    root, directory = Path(root).resolve(), Path(directory).resolve()
    if not directory.is_relative_to(root):
        raise ValueError('Evaluation evidence must stay inside the snapshot')
    native.assert_hash(root / artifact['operation_file'], artifact['sha256'])
    tools = native.load_tools(root)
    native.assert_hash(root / tools['study-check']['file'], tools['study-check']['sha256'])
    result = json.loads((directory / 'result.json').read_text())
    raw = json.loads((directory / 'raw.json').read_text())
    for key, expected in [('label', label), ('pairs', pairs), ('streams', streams)]:
        if result.get(key) != expected or raw.get(key) != expected:
            raise ValueError(f'Saved evaluation {key} changed')
    if raw['ops_sha256'] != artifact['sha256'] or result['artifact_sha256'] != artifact['sha256']:
        raise ValueError('Saved evaluation uses a different operation stream')
    normalized = native.normalize(raw)
    for key in ['Q', 'T', 'S', 'failed_inputs', 'failed_shots', 'channel_failed_inputs']:
        if result[key] != normalized[key]:
            raise ValueError(f'Saved summary disagrees with the raw result: {key}')
    outcomes = read_outcomes(directory / 'per-input.jsonl.gz', pairs=pairs, streams=streams)
    for key in ['failed_inputs', 'failed_shots', 'channel_failed_inputs', 'inputs_sha256', 'packet_sha256']:
        if result[key] != outcomes[key]:
            raise ValueError(f'Saved summary disagrees with the input outcomes: {key}')
    if raw['packet_sha256'] != outcomes['packet_sha256'] or raw['failed_indices'] != outcomes['failed_indices']:
        raise ValueError('Raw result disagrees with the saved input packet')
    failures = hashlib.sha256()
    for row in raw['all_failures']:
        failures.update(json.dumps(row, sort_keys=True, separators=(',', ':')).encode() + b'\n')
    if failures.hexdigest() != outcomes['failure_records_sha256']:
        raise ValueError('Raw failure examples disagree with the saved input outcomes')
    proof = {'artifact_sha256': artifact['sha256'],
             'raw_sha256': native.file_hash(directory / 'raw.json'),
             'result_sha256': native.file_hash(directory / 'result.json'),
             'compressed_inputs_sha256': native.file_hash(directory / 'per-input.jsonl.gz'),
             'checker_sha256': tools['study-check']['sha256'],
             'outcomes': outcomes}
    return result, proof
