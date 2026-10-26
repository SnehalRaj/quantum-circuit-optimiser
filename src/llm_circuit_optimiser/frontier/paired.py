"""Compare complete parent and child verdicts on one fixed input packet."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile

from ..common import canonical, digest, load, save, sha256

HASH = re.compile(r'[0-9a-f]{64}')
CHANNELS = {'coordinate', 'phase', 'workspace', 'addend'}
REQUIRED = {'idx', 'stream', 'input', 'observed_target', 'observed_addend', 'dirty_qubits', 'failures', 'passed'}
METADATA = {'ops', 'ops_path', 'ops_sha256', 'source', 'source_path', 'source_sha256',
            'source_hashes', 'checker_path', 'checker_binary_sha256', 'artifact_path', 'elapsed_seconds',
            'toffoli', 'toffoli_count', 'executed_ccx_ccz'}
MAX_ROW_BYTES = 1024 * 1024


def _receipt(value):
    if isinstance(value, (str, Path)):
        path = Path(value)
        return load(path), sha256(path)
    if not isinstance(value, dict):
        raise ValueError('A paired comparison needs checker receipts')
    return value, digest(value)


def _pairs(value):
    if type(value) is not int or value <= 0:
        raise ValueError('The input pair count must be a positive integer')
    return value


def _sha(value, field):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError(f'The checker receipt needs {field} as a SHA-256 hash')
    return value


def _check_receipts(parent, child, pairs, streams):
    for receipt in (parent, child):
        if receipt.get('pairs') != pairs or type(receipt.get('pairs')) is not int:
            raise ValueError('The checker pair count differs from the paired protocol')
        if receipt.get('streams') != streams or type(receipt.get('streams')) is not int:
            raise ValueError('The checker stream count differs from the paired protocol')
        for field in ('ops_sha256', 'packet_sha256', 'per_input_sha256'):
            _sha(receipt.get(field), field)
        if not isinstance(receipt.get('label'), str) or not receipt['label']:
            raise ValueError('The checker receipt needs its fixed input label')
        if 'shots' in receipt and receipt['shots'] != pairs * streams:
            raise ValueError('The checker shot count differs from the paired protocol')
    for field in ('packet_sha256', 'label', 'protocol', 'field_width', 'input_recipe',
                  'measurement_recipe', 'checker_source_sha256', 'checker_binary_sha256', 'checker_sha256'):
        if parent.get(field) != child.get(field):
            raise ValueError(f'Parent and child use different {field}')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'A verdict contains a repeated JSON key: {key}')
        result[key] = value
    return result


def _normalize(row, pairs, streams):
    if not isinstance(row, dict) or not REQUIRED.issubset(row):
        raise ValueError('Each verdict must include inputs, outputs, all checks, and its input/stream indices')
    index, stream = row['idx'], row['stream']
    if type(index) is not int or not 0 <= index < pairs:
        raise ValueError('A verdict input index is outside the paired protocol')
    if type(stream) is not int or not 0 <= stream < streams:
        raise ValueError('A verdict stream index is outside the paired protocol')
    if (not isinstance(row['input'], dict) or type(row['input'].get('idx')) is not int
            or row['input'].get('idx') != index):
        raise ValueError('The input record does not match its verdict index')
    if not {'target', 'addend', 'expected'}.issubset(row['input']):
        raise ValueError('The input record needs both addends and the expected sum')
    for field in ('observed_target', 'observed_addend'):
        if not isinstance(row[field], list) or len(row[field]) != 2:
            raise ValueError('A verdict must retain both observed coordinates')
    failures = row['failures']
    if (not isinstance(failures, dict) or not CHANNELS.issubset(failures)
            or any(type(value) is not bool for value in failures.values())):
        raise ValueError('A verdict needs Boolean coordinate, phase, workspace, and addend checks')
    if type(row['passed']) is not bool or row['passed'] != (not any(failures.values())):
        raise ValueError('The pass verdict conflicts with its failure channels')
    if (row['observed_target'] != row['input']['expected']) != failures['coordinate']:
        raise ValueError('The coordinate verdict conflicts with its observed output')
    if (row['observed_addend'] != row['input']['addend']) != failures['addend']:
        raise ValueError('The addend verdict conflicts with its observed output')
    dirty = row['dirty_qubits']
    if (not isinstance(dirty, list) or any(type(value) is not int or value < 0 for value in dirty)
            or len(set(dirty)) != len(dirty)):
        raise ValueError('The dirty-qubit record must contain distinct nonnegative wire indices')
    if bool(dirty) != failures['workspace']:
        raise ValueError('The workspace verdict conflicts with its dirty-qubit record')
    result = {key: value for key, value in row.items() if key not in METADATA}
    return index, stream, canonical(result).decode(), canonical(row['input']).decode(), int(not row['passed'])


def _read_rows(connection, side, path, receipt, pairs, streams):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Verdicts must be regular JSONL or gzip files')
    compressed_sha = sha256(path)
    opener = gzip.open if path.suffix == '.gz' else open
    content_hash = hashlib.sha256()
    count = 0
    with opener(path, 'rb') as source:
        while True:
            line = source.readline(MAX_ROW_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_ROW_BYTES:
                raise ValueError('A verdict row exceeds the bounded reader size')
            content_hash.update(line)
            try:
                row = json.loads(line, object_pairs_hook=_unique_object,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))
            except (json.JSONDecodeError, UnicodeError) as error:
                raise ValueError('A verdict row is not valid JSON') from error
            index, stream, record, input_row, failed = _normalize(row, pairs, streams)
            try:
                connection.execute('INSERT INTO verdicts VALUES (?, ?, ?, ?, ?, ?)',
                                   (side, index, stream, record, input_row, failed))
            except sqlite3.IntegrityError as error:
                raise ValueError(f'Duplicate input/stream verdict: {index}/{stream}') from error
            count += 1
            if count % 512 == 0:
                connection.commit()
    connection.commit()
    if count != pairs * streams:
        raise ValueError(f'Missing verdicts: expected {pairs * streams}, found {count}')
    if content_hash.hexdigest() != receipt['per_input_sha256']:
        raise ValueError('The verdict file differs from its checker receipt hash')
    if 'per_input_gz_sha256' in receipt and compressed_sha != receipt['per_input_gz_sha256']:
        raise ValueError('The compressed verdict file differs from its receipt hash')
    inconsistent = connection.execute('SELECT idx FROM verdicts WHERE side=? GROUP BY idx HAVING COUNT(DISTINCT input_row)>1 LIMIT 1', (side,)).fetchone()
    if inconsistent:
        raise ValueError('Measurement streams contain different input records for one input index')
    failed_shots, failed_inputs = connection.execute(
        'SELECT SUM(failed), COUNT(DISTINCT CASE WHEN failed=1 THEN idx END) FROM verdicts WHERE side=?', (side,)).fetchone()
    for key, actual in [('failed_shots', failed_shots), ('failed_inputs', failed_inputs)]:
        if key in receipt and receipt[key] != actual:
            raise ValueError(f'The checker {key} count differs from the saved verdicts')
    if 'failed_indices' in receipt:
        found = connection.execute('SELECT DISTINCT idx FROM verdicts WHERE side=? AND failed=1 ORDER BY idx', (side,))
        expected = receipt['failed_indices']
        if (not isinstance(expected, list) or expected != sorted(set(expected))
                or len(expected) != failed_inputs or any(row[0] != value for row, value in zip(found, expected))):
            raise ValueError('The checker failed_indices differ from the saved verdicts')
    normalized_hash = hashlib.sha256()
    for record, in connection.execute('SELECT record FROM verdicts WHERE side=? ORDER BY idx, stream', (side,)):
        normalized_hash.update(record.encode() + b'\n')
    if sha256(path) != compressed_sha:
        raise ValueError('The verdict file changed during comparison')
    return {'file_sha256': compressed_sha, 'per_input_sha256': content_hash.hexdigest(),
            'normalized_sha256': normalized_hash.hexdigest(), 'rows': count,
            'failed_inputs': failed_inputs, 'failed_shots': failed_shots}


def validate_verdicts(receipt, rows, *, pairs, streams=2):
    """Recount a complete checker packet before a caller qualifies its failure rate."""
    pairs = _pairs(pairs)
    if type(streams) is not int or streams != 2:
        raise ValueError('The verdict protocol requires exactly two measurement streams')
    record, receipt_sha = _receipt(receipt)
    _check_receipts(record, record, pairs, streams)
    with tempfile.TemporaryDirectory(prefix='lco-verdicts-') as directory:
        connection = sqlite3.connect(str(Path(directory) / 'verdicts.sqlite'))
        try:
            connection.execute('PRAGMA cache_size=-2048')
            connection.execute('PRAGMA temp_store=FILE')
            connection.execute('CREATE TABLE verdicts (side INTEGER, idx INTEGER, stream INTEGER, record TEXT, input_row TEXT, failed INTEGER, PRIMARY KEY(side, idx, stream)) WITHOUT ROWID')
            summary = _read_rows(connection, 0, rows, record, pairs, streams)
        finally:
            connection.close()
    return {'schema': 'frontier-verdict-validation-v1', **summary,
            'receipt_sha256': receipt_sha, 'ops_sha256': record['ops_sha256'],
            'packet_sha256': record['packet_sha256'], 'label': record['label'],
            'pairs': pairs, 'streams': streams, 'complete': True,
            'scope': 'All saved verdicts passed structural and count checks. Reliability qualification remains a separate calculation.'}


def compare_pair(parent_receipt, child_receipt, parent_rows, child_rows, *, pairs=8192, streams=2, output=None):
    """Check sampled parent/child agreement; exact local proof remains separate.

    Verdicts can arrive in any order. A temporary disk index bounds memory use.
    Receipts must bind the uncompressed JSONL through per_input_sha256. Merge an
    archived process receipt when its checker receipt omits that field.
    """
    pairs = _pairs(pairs)
    if type(streams) is not int or streams != 2:
        raise ValueError('The paired protocol requires exactly two measurement streams')
    parent, parent_sha = _receipt(parent_receipt)
    child, child_sha = _receipt(child_receipt)
    _check_receipts(parent, child, pairs, streams)
    with tempfile.TemporaryDirectory(prefix='lco-paired-') as directory:
        connection = sqlite3.connect(str(Path(directory) / 'verdicts.sqlite'))
        try:
            connection.execute('PRAGMA cache_size=-2048')
            connection.execute('PRAGMA temp_store=FILE')
            connection.execute('CREATE TABLE verdicts (side INTEGER, idx INTEGER, stream INTEGER, record TEXT, input_row TEXT, failed INTEGER, PRIMARY KEY(side, idx, stream)) WITHOUT ROWID')
            a = _read_rows(connection, 0, parent_rows, parent, pairs, streams)
            b = _read_rows(connection, 1, child_rows, child, pairs, streams)
            join = 'FROM verdicts a JOIN verdicts b ON a.idx=b.idx AND a.stream=b.stream WHERE a.side=0 AND b.side=1'
            differences = connection.execute('SELECT COUNT(*) ' + join + ' AND a.record<>b.record').fetchone()[0]
            failure_differences = connection.execute('SELECT COUNT(*) ' + join + ' AND a.failed<>b.failed').fetchone()[0]
            examples = [{'idx': index, 'stream': stream, 'parent': json.loads(before), 'child': json.loads(after)}
                        for index, stream, before, after in connection.execute(
                            'SELECT a.idx,a.stream,a.record,b.record ' + join + ' AND a.record<>b.record ORDER BY a.idx,a.stream LIMIT 10')]
        finally:
            connection.close()
    result = {'schema': 'frontier-paired-validation-v1', 'passed': differences == 0,
              'all_rows_equal': differences == 0, 'label': parent['label'], 'pairs': pairs, 'streams': streams,
              'packet_sha256': parent['packet_sha256'], 'packet_equal': True,
              'parent_ops_sha256': parent['ops_sha256'], 'child_ops_sha256': child['ops_sha256'],
              'parent_receipt_sha256': parent_sha, 'child_receipt_sha256': child_sha,
              'parent_verdicts': a, 'child_verdicts': b,
              'per_input_rows_compared': pairs * streams, 'per_input_rows_different': differences,
              'failing_shot_sets_equal': failure_differences == 0,
              'failed_inputs': [a['failed_inputs'], b['failed_inputs']],
              'failed_shots': [a['failed_shots'], b['failed_shots']],
              'first_different_rows': examples,
              'scope': 'Finite paired validation on the declared inputs and measurement streams. This is not a proof for every input or a reliability qualification.'}
    if output is not None:
        output = Path(output)
        if output.exists():
            if load(output) != result:
                raise ValueError('An existing paired result has different evidence')
        else:
            save(output, result)
    return result


compare_records = compare_pair
