"""Compare a fresh campaign with the retained Q818 and Q819 records."""
import copy
import hashlib
import json
from pathlib import Path
import re

from ..common import sha256
from .campaign import STAGES, artifact_path, read_envelope, validate_result
from .registry import RegistryTampered, digest


_CANDIDATES = {'q818-ur-cat', 'q819-p19-cat'}
_ROLES = {'d2583a60': 'parent', '59359890': 'lever', '6d08713c': 'lever',
          'd419f804': 'lever', '11613a51': 'catalog', 'cac0bf90': 'setting'}
_COUNTS = ('classical_mismatches', 'phase_garbage_batches', 'ancilla_garbage_batches')
_SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')


def note_sections(text):
    """Read level-two Markdown headings outside code fences."""
    sections, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        heading = re.match(r'^##\s+(.+?)\s*#*\s*$', line) if fence is None else None
        if heading:
            sections.append(heading[1])
    return sections


def extract_expected(pilot_root, candidate):
    """Extract portable expectations; retain hashes of all source records."""
    if candidate not in _CANDIDATES:
        raise ValueError('The retained replay fixtures cover Q818 and Q819 only')
    root = Path(pilot_root)
    prefix = Path('runs/pareto5-public-20261025')
    track = prefix/'tracks/froghop-catalog'/candidate
    publication = prefix/'publication'/candidate
    paths = {'emission': track/'emission.json', 'screen': track/'screen.json',
             'manifest': track/'manifest.json', 'note': publication/'note.md',
             'dependencies': publication/'dependencies.json', 'service': publication/'service-result.json'}
    raw = {key: (root/path).read_bytes() for key, path in paths.items()}
    records = {key: json.loads(value) for key, value in raw.items() if key != 'note'}
    emission, screen, manifest, service = [records[key] for key in ('emission', 'screen', 'manifest', 'service')]
    operation = emission['ops_sha256']
    if screen['ops_sha256'] != operation:
        raise ValueError('The retained emission and screen use different operation bytes')
    metrics = screen['score']['metrics']
    if service['officialMetrics'] != metrics or service['officialScore'] != screen['score']['score']:
        raise ValueError('The retained screen and public resource counts disagree')
    if screen['passed'] is not True or screen['counts']['shots'] != 9024 or any(screen['counts'][key] for key in _COUNTS):
        raise ValueError('The retained replay screen must pass all 9,024 shots')
    dependencies = []
    for source in records['dependencies']:
        identifier = source['submissionId']
        if identifier[:8] not in _ROLES:
            raise ValueError('The retained dependency needs an explicit role mapping')
        dependencies.append({'role': _ROLES[identifier[:8]], 'submission_id': identifier})
    value = {
        'schema': 'lco-frontier-replay-v1', 'candidate': candidate,
        'scope': 'Expectations extracted from archived campaign records. A fresh library replay remains required.',
        'source_records': {key: {'path': path.as_posix(), 'sha256': hashlib.sha256(raw[key]).hexdigest()}
                           for key, path in paths.items()},
        'base_commit': manifest['parent_commit'],
        'source_files_sha256': {edit['file']: edit['after_sha256'] for edit in manifest['edits'] if 'after_sha256' in edit},
        'expected': {'operation_sha256': operation, 'emitted_ops': emission['emitted_ops'],
                     'screen': {'shots': screen['counts']['shots'], 'counts': {key: screen['counts'][key] for key in _COUNTS},
                                'passed': screen['passed'], 'Q': metrics['qubits'], 'T': metrics['toffoli'],
                                'score': screen['score']['score']},
                     'required_note_sections': note_sections(raw['note'].decode()),
                     'required_dependencies': dependencies},
        'historical_submission': {key: service[key] for key in ('id', 'status', 'rejectionReason',
                                   'submissionCommitSha', 'officialScore', 'officialMetrics', 'on_public_QT_curve')},
        'role_mapping_scope': 'Roles follow the exact-edit descriptions and credits in the hashed public notes.',
    }
    return {**value, 'fixture_sha256': digest(value)}


def _validate_expected(value):
    value = copy.deepcopy(value)
    expected_hash = value.pop('fixture_sha256', None)
    if value.get('schema') != 'lco-frontier-replay-v1' or digest(value) != expected_hash:
        raise ValueError('The replay fixture schema or hash is invalid')
    if not _SAFE_ID.fullmatch(value.get('candidate', '')):
        raise ValueError('The replay candidate ID is invalid')
    for entry in value['source_records'].values():
        path = Path(entry['path'])
        if path.is_absolute() or '..' in path.parts or '\\' in entry['path']:
            raise ValueError('Replay source records need portable relative paths')
    return {**value, 'fixture_sha256': expected_hash}


def load_expected(path):
    """Load one checked fixture or every fixture in a directory."""
    path = Path(path)
    files = sorted(path.glob('*.json')) if path.is_dir() else [path]
    if not files:
        raise ValueError('No replay fixtures were found')
    values = [json.loads(file.read_text()) for file in files]
    result = [_validate_expected(value) for value in values if value.get('schema') != 'lco-frontier-replay-source-check-v1']
    if not result:
        raise ValueError('No replay expectations were found')
    if len({item['candidate'] for item in result}) != len(result):
        raise ValueError('Replay fixtures repeat a candidate')
    return result


def _read_replay(directory):
    frozen = read_envelope(directory/'job.json')
    job = frozen['job']
    job_hash = digest(job)
    if frozen['job_sha256'] != job_hash or job['id'] != directory.name:
        raise ValueError('The frozen replay job identity changed')
    results, previous, missing = {}, job_hash, []
    for index, stage in enumerate(STAGES[:STAGES.index('export')+1]):
        path = directory/f'{index:02d}-{stage}.json'
        if not path.is_file():
            missing.append(path.name)
            continue
        if missing:
            raise ValueError('The replay receipt chain has a missing predecessor')
        receipt = read_envelope(path)
        if (receipt['stage'], receipt['job_sha256'], receipt['previous_sha256']) != (stage, job_hash, previous):
            raise ValueError('The replay receipt chain changed')
        for artifact in receipt['result'].get('artifacts', []):
            candidate = artifact_path(directory, artifact['path'])
            if not candidate.is_file():
                raise FileNotFoundError(2, 'A replay artifact is missing', str(candidate))
        validate_result(stage, receipt['result'], results, receipt['context'], directory)
        results[stage] = receipt['result']
        previous = receipt['receipt_sha256']
    return job, results, missing


def _roles(dependencies):
    found = set()
    for dependency in dependencies:
        role = dependency.get('role')
        if role is None:
            role = dependency.get('rationale', '').split(':', 1)[0]
        identifier = dependency.get('submission_id') or dependency.get('submissionId')
        found.add((role, identifier))
    return found


def _compare_one(directory, fixture):
    candidate = fixture['candidate']
    report = {'candidate': candidate, 'job_id': directory.name, 'status': 'incomplete', 'checks': [], 'missing': []}
    if not (directory/'job.json').is_file():
        report['missing'] = ['job.json and replay receipts']
        return report
    try:
        job, receipts, missing = _read_replay(directory)
    except FileNotFoundError as error:
        report['missing'] = [Path(error.filename).name]
        return report
    except (ValueError, KeyError, TypeError, RegistryTampered) as error:
        report.update(status='mismatch', error=str(error))
        return report
    if missing:
        report['missing'] = missing
        return report
    expected = fixture['expected']
    def check(name, observed, wanted):
        passed = type(observed) is type(wanted) and observed == wanted
        report['checks'].append({'check': name, 'matched': passed, 'expected': wanted, 'observed': observed})
    try:
        emission, screen, exported = [receipts[stage] for stage in ('emit', 'screen', 'export')]
        operations = artifact_path(directory, emission['ops_path'])
        if not operations.is_file():
            report['missing'] = ['emitted operation bytes']
            return report
        check('emitted operation bytes', sha256(operations), expected['operation_sha256'])
        check('emission receipt operation hash', emission.get('operation_sha256'), expected['operation_sha256'])
        check('screen operation hash', screen.get('operation_sha256'), expected['operation_sha256'])
        check('export operation hash', exported.get('operation_sha256'), expected['operation_sha256'])
        for key, wanted in expected['screen'].items():
            check('screen ' + key, screen.get(key), wanted)
        note_path = artifact_path(directory, exported['note_path'])
        dependencies_path = artifact_path(directory, exported['dependencies_path'])
        if not note_path.is_file() or not dependencies_path.is_file():
            report['missing'] = ['exported note or dependencies']
            return report
        pinned = {artifact_path(directory, item['path']).resolve(): item['sha256'] for item in exported['artifacts']}
        for path in (note_path, dependencies_path):
            if pinned.get(path.resolve()) != sha256(path):
                raise ValueError('The exported note and dependencies need artifact hashes')
        sections = note_sections(note_path.read_text())
        for section in expected['required_note_sections']:
            check('note section: ' + section, section in sections, True)
        declared = _roles(json.loads(dependencies_path.read_text()))
        typed = _roles(job.get('dependencies', []))
        for dependency in expected['required_dependencies']:
            identity = (dependency['role'], dependency['submission_id'])
            check('typed dependency: ' + ':'.join(identity), identity in typed, True)
            check('exported dependency: ' + ':'.join(identity), identity in declared, True)
        source_hashes = exported.get('source_hashes', {})
        for path, expected_hash in fixture['source_files_sha256'].items():
            check('source hash: ' + path, source_hashes.get(path), expected_hash)
            source_file = artifact_path(directory, exported['source_dir'])/path
            if not source_file.is_file():
                report['missing'].append(path)
                continue
            check('source bytes: ' + path, sha256(source_file), expected_hash)
    except (ValueError, KeyError, TypeError, OSError) as error:
        report.update(status='mismatch', error=str(error))
        return report
    report['status'] = ('mismatch' if any(not item['matched'] for item in report['checks'])
                        else 'incomplete' if report['missing'] else 'matched')
    return report


def compare_campaign(campaign_dir, expected, *, job_ids=None):
    """Check fresh replay receipts and files; this function never runs a circuit."""
    fixtures = load_expected(expected) if isinstance(expected, (str, Path)) else [_validate_expected(item) for item in expected]
    if not fixtures:
        raise ValueError('At least one replay fixture is required')
    mapping = job_ids or {}
    if set(mapping) - {fixture['candidate'] for fixture in fixtures}:
        raise ValueError('A replay job mapping names an unknown fixture')
    results = []
    for fixture in fixtures:
        identifier = mapping.get(fixture['candidate'], fixture['candidate'])
        if not isinstance(identifier, str) or not _SAFE_ID.fullmatch(identifier):
            raise ValueError('The replay job ID is not a safe path component')
        results.append(_compare_one(Path(campaign_dir)/'jobs'/identifier, fixture))
    status = ('mismatch' if any(row['status'] == 'mismatch' for row in results)
              else 'matched' if all(row['status'] == 'matched' for row in results) else 'incomplete')
    return {'schema': 'lco-frontier-replay-comparison-v1', 'status': status, 'results': results,
            'scope': 'Receipt and artifact comparison through export. A match does not claim a new public submission or general circuit equivalence.'}
