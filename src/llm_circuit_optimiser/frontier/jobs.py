"""Validate portable campaign jobs without building their source."""
from dataclasses import asdict, dataclass, field
import copy
import json
import math
from pathlib import Path, PurePosixPath
import re

from ..common import digest


SCHEMA = 'lco-frontier-job-v1'
STATES = frozenset({'planned', 'materialized', 'built', 'emitted', 'static_passed',
                    'developed', 'screened', 'audited', 'exported', 'submitted',
                    'recorded', 'blocked', 'failed', 'rejected', 'stale', 'refused'})
ROLES = frozenset({'parent', 'lever', 'catalog', 'setting', 'integrated_base', 'source'})
CATALOG_COMMIT = '4073f09cfa7fe733fdeb64841193282aaff293d9'


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    return value


def _path(value):
    _text(value, 'Source path')
    path = PurePosixPath(value)
    if path.is_absolute() or '\\' in value or '..' in path.parts or str(path) != value or '\x00' in value:
        raise ValueError('Source paths must be normalized relative paths')
    if not value.startswith('src/point_add/') and value not in ('NOTICE', 'NOTICE.md', 'LICENSE', 'LICENSE.md', 'LICENSE.txt', 'COPYING'):
        raise ValueError('Candidate paths must stay in src/point_add or retained notices')
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('File hashes must be lowercase SHA-256 values')
    return value


def _commit(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{7,40}', value):
        raise ValueError('A base or copy commit must be a hexadecimal commit ID')
    return value


def _positive(value, name, *, optional=False):
    if optional and value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')
    return value


@dataclass(frozen=True)
class CountedEdit:
    path: str
    old: str
    new: str
    count: int = 1

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, (tuple, list)) and len(value) == 3:
            value = dict(zip(('path', 'old', 'new'), value))
        if not isinstance(value, dict):
            raise ValueError('Each edit must name a path, old text, new text, and count')
        if set(value) - {'path', 'file', 'old', 'new', 'count'}:
            raise ValueError('Unknown counted-edit field')
        path = value.get('path', value.get('file'))
        if 'path' in value and 'file' in value and value['path'] != value['file']:
            raise ValueError('The edit path aliases disagree')
        old = _text(value.get('old'), 'Old text')
        new = value.get('new')
        if not isinstance(new, str) or old == new:
            raise ValueError('An edit needs different new text')
        count = value.get('count', 1)
        if type(count) is not int or count < 1:
            raise ValueError('An edit count must be a positive integer')
        return cls(_path(path), old, new, count)


@dataclass(frozen=True)
class FileCopy:
    path: str
    source_commit: str
    source_path: str
    sha256: str

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError('Each copied file must identify its bytes and source commit')
        if set(value) - {'path', 'file', 'source_commit', 'source_path', 'source', 'sha256'}:
            raise ValueError('Unknown file-copy field')
        if 'path' in value and 'file' in value and value['path'] != value['file']:
            raise ValueError('The file-copy path aliases disagree')
        if 'source_path' in value and 'source' in value and value['source_path'] != value['source']:
            raise ValueError('The file-copy source aliases disagree')
        path = value.get('path', value.get('file'))
        source = value.get('source_path', value.get('source', path))
        return cls(_path(path), _commit(value.get('source_commit')), _path(source), _sha(value.get('sha256')))


@dataclass(frozen=True)
class Dependency:
    role: str
    submission_id: str
    rationale: str
    evidence: str

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - {'role', 'submission_id', 'submissionId', 'rationale', 'evidence'}:
            raise ValueError('Invalid role dependency')
        role = value.get('role')
        if role not in ROLES:
            raise ValueError('Unknown dependency role')
        identifier = value.get('submission_id', value.get('submissionId'))
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?', identifier):
            raise ValueError('Dependencies require a submission ID or an explicit eight-character prefix')
        return cls(role, identifier, _text(value.get('rationale'), 'Dependency rationale'),
                   _text(value.get('evidence'), 'Dependency evidence'))


def _historical_dependencies(raw):
    result = []
    identifiers = raw.get('declare', [])
    if not isinstance(identifiers, list) or not all(isinstance(v, str) for v in identifiers):
        raise ValueError('Declared dependencies must be a list of submission IDs')
    for identifier in identifiers:
        prefix = identifier[:8]
        if prefix == (raw.get('setting_source_row') or '')[:8]:
            role, rationale = 'setting', 'Source of the reused layout setting.'
        elif prefix == (raw.get('base_row') or '')[:8]:
            role, rationale = 'integrated_base', 'Base circuit used by the counted edits.'
        elif prefix == '11613a51':
            role, rationale = 'catalog', 'Source of the exact local gate catalog.'
        elif prefix in {'59359890', '6d08713c', 'd419f804'}:
            role, rationale = 'lever', 'Source of the conditional-AND pass setting.'
        elif prefix in {'d2583a60', 'a45bcccd', 'a3b6c26c'}:
            role, rationale = 'parent', 'Upstream arithmetic and layout source.'
        else:
            role, rationale = 'source', 'Source declared in the retained campaign recipe.'
        result.append(Dependency.from_dict({'role': role, 'submission_id': identifier,
                      'rationale': rationale,
                      'evidence': 'Campaign handoff at da182c4: jobs.json; retain the public source note.'}))
    return result


@dataclass(frozen=True)
class Job:
    id: str
    base_commit: str
    Q: int
    edits: tuple[CountedEdit, ...] = ()
    file_copies: tuple[FileCopy, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    expected_T_inference: float | None = None
    must_beat: float | None = None
    owner: str = 'unassigned'
    state: str = 'planned'
    base_files_sha256: dict = field(default_factory=dict)
    setting_requests: tuple[dict, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError('A job must be an object')
        if raw.get('schema', SCHEMA) != SCHEMA:
            raise ValueError('Unknown frontier job schema')
        identifier = raw.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', identifier):
            raise ValueError('A job ID must be a safe directory name')
        q = raw.get('Q')
        if type(q) is not int or q <= 0:
            raise ValueError('A job Q must be a positive integer')
        edits, requests = [], copy.deepcopy(raw.get('setting_requests', []))
        reasons = copy.deepcopy(raw.get('blocked_reasons', []))
        if not isinstance(requests, list) or not isinstance(reasons, list):
            raise ValueError('Setting requests and blocked reasons must be lists')
        if not all(isinstance(request, dict) for request in requests):
            raise ValueError('Each setting request must be an object')
        edit_values = raw.get('edits', [])
        if not isinstance(edit_values, list):
            raise ValueError('Edits must be an ordered list')
        for value in edit_values:
            if isinstance(value, list) and len(value) == 3 and isinstance(value[1], str) and 'literal' in value[1] and 'PEAK0' in value[1]:
                requests.append({'path': _path(value[0]), 'description': value[1], 'requested': value[2]})
                reasons.append('The PEAK0 setting needs an exact source line before materialization.')
            else:
                edits.append(CountedEdit.from_dict(value))
        if 'file_copies' in raw and 'files' in raw and raw['file_copies'] != raw['files']:
            raise ValueError('The file-copy list aliases disagree')
        copies = raw.get('file_copies', raw.get('files', []))
        if not isinstance(copies, list):
            raise ValueError('File copies must be a list')
        copies = [FileCopy.from_dict(value) for value in copies]
        historical_files = raw.get('files_from_commit_4073f09c', {})
        if not isinstance(historical_files, dict):
            raise ValueError('Historical copied files must map paths to hashes')
        for path, sha in historical_files.items():
            copies.append(FileCopy(_path(path), CATALOG_COMMIT, _path(path), _sha(sha)))
        if len({r.path for r in copies}) != len(copies):
            raise ValueError('A job cannot copy the same destination twice')
        deps = raw.get('dependencies', raw.get('declare_by_role'))
        if deps is not None and not isinstance(deps, list):
            raise ValueError('Role dependencies must be a list')
        deps = _historical_dependencies(raw) if deps is None else [Dependency.from_dict(r) for r in deps]
        hashes = raw.get('base_files_sha256', {})
        if not isinstance(hashes, dict):
            raise ValueError('Base file hashes must be an object')
        hashes = {_path(k): _sha(v) for k, v in hashes.items()}
        state = raw.get('state', 'planned')
        if state not in STATES:
            raise ValueError('Unknown job state')
        if raw.get('gate'):
            reasons.append(_text(raw['gate'], 'Gate'))
        if requests:
            reasons.append('Resolve setting requests before running this job.')
        reasons = tuple(dict.fromkeys(_text(r, 'Blocked reason') for r in reasons))
        if reasons and state == 'planned':
            state = 'blocked'
        metadata = copy.deepcopy(raw.get('metadata', {}))
        if not isinstance(metadata, dict):
            raise ValueError('Job metadata must be an object')
        fields = {'schema', 'id', 'base_commit', 'Q', 'edits', 'file_copies', 'files', 'dependencies',
                  'declare_by_role', 'expected_T_inference', 'must_beat', 'owner', 'state',
                  'base_files_sha256', 'setting_requests', 'blocked_reasons', 'metadata', 'files_from_commit_4073f09c'}
        metadata.update({k: copy.deepcopy(v) for k, v in raw.items() if k not in fields})
        json.dumps(metadata, allow_nan=False)
        return cls(identifier, _commit(raw.get('base_commit')), q, tuple(edits), tuple(copies), tuple(deps),
                   _positive(raw.get('expected_T_inference'), 'Expected T', optional=True),
                   _positive(raw.get('must_beat'), 'Must-beat T', optional=True),
                   _text(raw.get('owner', 'unassigned'), 'Owner'), state, hashes, tuple(requests), reasons, metadata)

    def to_dict(self):
        result = asdict(self)
        for key in ('edits', 'file_copies', 'dependencies', 'setting_requests', 'blocked_reasons'):
            result[key] = list(result[key])
        return {'schema': SCHEMA, **result}

    @property
    def sha256(self):
        return digest(self.to_dict())


def normalize_job(value):
    return Job.from_dict(value).to_dict()


def load_jobs(path, *, owner=None):
    document = json.loads(Path(path).read_text())
    rows = document.get('jobs') if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise ValueError('A job document must contain a jobs list')
    result = [normalize_job(row) for row in rows]
    if len({r['id'] for r in result}) != len(result):
        raise ValueError('A job document contains repeated IDs')
    # Recognize old exports by source recipe, even when their job names changed.
    from .generators import generate_jobs
    def recipe_key(job):
        return digest({key: job[key] for key in ('base_commit', 'edits', 'file_copies')})
    historical = {recipe_key(job): job for job in
                  (normalize_job(row) for row in generate_jobs({'curve': []}))}
    for job in result:
        original = historical.get(recipe_key(job))
        if original is not None:
            job['state'] = 'blocked'
            job['blocked_reasons'] = list(dict.fromkeys(job['blocked_reasons'] + original['blocked_reasons']))
            job['metadata'].update({key: original['metadata'][key] for key in
                                    ('origin', 'origin_commit', 'purpose', 'screen_history')})
    return [r for r in result if owner is None or r['owner'] == owner]
