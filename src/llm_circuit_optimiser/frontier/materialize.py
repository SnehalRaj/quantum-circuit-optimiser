"""Apply counted edits to a pinned point-addition tree without changing the checker."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time

from ..common import ROOT, digest, load, sha256

HEX = re.compile(r'[0-9a-f]{64}')
COMMIT = re.compile(r'[0-9a-f]{7,40}')
NOTICE_NAMES = {'NOTICE', 'NOTICE.md', 'LICENSE', 'LICENSE.md', 'LICENSE.txt', 'COPYING'}
PROTECTED = re.compile(r'nonce|padding|\btail\b|\b[a-z_]*_tail\b|\btail_[a-z_]*\b', re.I)


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ValueError('Source paths must be relative POSIX paths')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value or ':' in value:
        raise ValueError(f'Unsafe source path: {value!r}')
    return value


def _candidate_path(value):
    value = _relative(value)
    if not value.startswith('src/point_add/'):
        raise ValueError('Edits and copies must stay under src/point_add/')
    return value


def _hash(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError('Expected a lowercase SHA-256 hash')
    return value


def _root(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f'The source root must be a real directory: {path}')
    return path.resolve()


def _file(root, name):
    name = _relative(name)
    target = root / name
    cursor = root
    for part in PurePosixPath(name).parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(f'Symbolic links are not admitted: {name}')
    if not target.is_file():
        raise ValueError(f'Missing source file: {name}')
    return target


def _tree(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('A source tree must be a real directory')
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Source trees cannot contain symbolic links')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = sha256(path)
    return files


def _pins(value):
    if not isinstance(value, dict) or not value:
        raise ValueError('A directory base needs its complete base_files_sha256 map')
    return {_relative(name): _hash(expected) for name, expected in value.items()}


def normalize_recipe(job):
    """Accept canonical jobs and the frozen campaign's counted-edit form."""
    if not isinstance(job, dict):
        if hasattr(job, 'to_dict'):
            job = job.to_dict()
        else:
            raise ValueError('A materialization job must be an object')
    if job.get('blocked_reasons') or job.get('setting_requests') or job.get('state') == 'blocked':
        raise ValueError('The job has unresolved settings or blocked source requirements')
    commit = job.get('base_commit')
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ValueError('A base commit must be a hexadecimal Git commit ID')
    edits = []
    for item in job.get('edits', []):
        if isinstance(item, (tuple, list)) and len(item) == 3:
            item = dict(zip(('path', 'old', 'new'), item))
        if not isinstance(item, dict):
            raise ValueError('An edit needs a path and exact old and new text')
        if set(item) - {'path', 'file', 'old', 'new', 'count'}:
            raise ValueError('Unknown counted-edit field')
        if 'path' in item and 'file' in item and item['path'] != item['file']:
            raise ValueError('The edit path aliases disagree')
        path = _candidate_path(item.get('path', item.get('file')))
        old, new, count = item.get('old'), item.get('new'), item.get('count', 1)
        if not isinstance(old, str) or not old or not isinstance(new, str) or old == new:
            raise ValueError('Counted edits need nonempty old text and a different replacement')
        if type(count) is not int or count <= 0:
            raise ValueError('The expected edit count must be a positive integer')
        edits.append({'path': path, 'old': old, 'new': new, 'count': count})
    if 'file_copies' in job and 'files' in job and job['file_copies'] != job['files']:
        raise ValueError('The file-copy aliases disagree')
    copies = job.get('file_copies', job.get('files'))
    if copies is None:
        copies = [{'path': path, 'source_path': path, 'source_commit': '4073f09c', 'sha256': value}
                  for path, value in job.get('files_from_commit_4073f09c', {}).items()]
    if not isinstance(copies, list):
        raise ValueError('file_copies must be a list')
    normalized = []
    for item in copies:
        if not isinstance(item, dict):
            raise ValueError('Each file copy must be an object')
        if set(item) - {'path', 'file', 'source_path', 'source', 'source_commit', 'sha256'}:
            raise ValueError('Unknown file-copy field')
        for first, second in [('path', 'file'), ('source_path', 'source')]:
            if first in item and second in item and item[first] != item[second]:
                raise ValueError('The file-copy path aliases disagree')
        path = _candidate_path(item.get('path', item.get('file')))
        source = _relative(item.get('source_path', item.get('source', path)))
        source_commit = item.get('source_commit')
        if source_commit is not None and (not isinstance(source_commit, str) or not COMMIT.fullmatch(source_commit)):
            raise ValueError('A copy source commit must be a hexadecimal Git commit ID')
        normalized.append({'path': path, 'source_path': source, 'source_commit': source_commit,
                           'sha256': _hash(item.get('sha256'))})
    if len({r['path'] for r in normalized}) != len(normalized):
        raise ValueError('A destination cannot appear twice in file_copies')
    pins = job.get('base_files_sha256')
    if pins == {}:
        pins = None
    return {'id': job.get('id'), 'base_commit': commit, 'base_files_sha256': _pins(pins) if pins is not None else None,
            'edits': edits, 'file_copies': normalized, 'expected_Q': job.get('Q', job.get('expected_Q')),
            'expected_T': job.get('expected_T_inference', job.get('expected_T')),
            'expected_ops_sha256': job.get('expected_ops_sha256')}


def _git(repo, *args):
    done = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if done.returncode:
        raise ValueError('The pinned Git source is unavailable: ' + done.stderr.decode(errors='replace').strip())
    return done.stdout


def _resolve(repo, commit):
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ValueError('A Git source needs a pinned commit ID')
    resolved = _git(repo, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    if not re.fullmatch(r'[0-9a-f]{40}', resolved):
        raise ValueError('The Git source did not resolve to one commit')
    return resolved


def _git_files(repo, commit):
    files = {}
    for entry in _git(repo, 'ls-tree', '-r', '-z', '--full-tree', commit).split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        mode, kind, object_id = metadata.decode().split()
        name = raw_name.decode('utf-8')
        if not (name.startswith('src/point_add/') or name in NOTICE_NAMES):
            continue
        _relative(name)
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise ValueError(f'The pinned source contains a link or unsupported object: {name}')
        files[name] = _git(repo, 'cat-file', 'blob', object_id)
    if 'src/point_add/mod.rs' not in files:
        raise ValueError('The pinned source has no point-addition entry point')
    return files


def _directory_files(directory, expected):
    expected = _pins(expected)
    root = _root(directory)
    actual = _tree(root)
    # Vendored manifests describe every payload file and exclude themselves.
    if 'manifest.json' not in expected:
        actual.pop('manifest.json', None)
    if actual != expected:
        raise ValueError('The base source closure differs from base_files_sha256')
    files = {name: _file(root, name).read_bytes() for name in expected
             if name.startswith('src/point_add/') or name in NOTICE_NAMES}
    if any(hashlib.sha256(data).hexdigest() != expected[name] for name, data in files.items()):
        raise ValueError('The base source changed while reading it')
    if 'src/point_add/mod.rs' not in files:
        raise ValueError('The pinned source has no point-addition entry point')
    return files, expected


def _protected_regions(data):
    """Conservatively pin inherited nonce constants, tail functions and loops."""
    text = data.decode('utf-8')
    lines = [line for line in text.splitlines() if PROTECTED.search(line)]
    blocks = []
    # Tail routines and loops with nonce bounds must stay byte-identical.
    pattern = re.compile(r'\b(?:fn\s+\w+[^\n{]*|(?:for|while)\b[^\n{]*)\{')
    for match in pattern.finditer(text):
        if not PROTECTED.search(match.group()):
            continue
        depth, end = 1, match.end()
        while end < len(text) and depth:
            depth += (text[end] == '{') - (text[end] == '}')
            end += 1
        if depth:
            raise ValueError('Cannot identify an inherited nonce or tail block')
        blocks.append(text[match.start():end])
    return {'lines': lines, 'blocks': blocks}


def _assert_protected(before, after):
    for name in set(before) | set(after):
        if not name.endswith('.rs'):
            continue
        old = _protected_regions(before.get(name, b''))
        new = _protected_regions(after.get(name, b''))
        if old != new:
            raise ValueError(f'The edit changes inherited nonce or tail source: {name}')


def _apply_recipe(base, recipe, copy_data):
    changed = dict(base)
    edit_receipts = []
    for edit in recipe['edits']:
        before = changed.get(edit['path'])
        if before is None:
            raise ValueError(f'An edit targets an absent base file: {edit["path"]}')
        old, new = edit['old'].encode('utf-8'), edit['new'].encode('utf-8')
        count = before.count(old)
        if count != edit['count']:
            raise ValueError(f'Expected {edit["count"]} matches in {edit["path"]}; found {count}')
        after = before.replace(old, new)
        changed[edit['path']] = after
        edit_receipts.append({**edit, 'before_sha256': hashlib.sha256(before).hexdigest(),
                              'after_sha256': hashlib.sha256(after).hexdigest()})
    copies = []
    for item, data, resolved_copy in copy_data:
        previous = changed.get(item['path'])
        if previous is not None and previous != data:
            raise ValueError('File copies cannot overwrite an existing different base file; use a counted edit')
        changed[item['path']] = data
        copies.append({**item, 'resolved_source_commit': resolved_copy,
                       'before_sha256': hashlib.sha256(previous).hexdigest() if previous is not None else None})
    _assert_protected(base, changed)
    return changed, edit_receipts, copies


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


@contextmanager
def _locked(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def materialize(job, workdir, *, base_dir=None, git_repo=None, files_root=None, benchmark_dir=None):
    """Build a source closure from a pinned base, counted edits and pinned file copies.

    Directory sources require an exact file-hash map. Git sources use resolved
    immutable commits. Runtime paths stay outside the canonical recipe hash.
    This function performs no fetch, build, evaluation or public submission.
    """
    recipe = normalize_recipe(job)
    if (base_dir is None) == (git_repo is None):
        raise ValueError('Choose one base source: base_dir or git_repo')
    if base_dir is not None:
        base, base_pins = _directory_files(base_dir, recipe['base_files_sha256'])
        base_identity = {'kind': 'directory', 'declared_commit': recipe['base_commit'], 'files_sha256': base_pins}
    else:
        repo = _root(git_repo)
        resolved = _resolve(repo, recipe['base_commit'])
        base = _git_files(repo, resolved)
        base_pins = {name: hashlib.sha256(data).hexdigest() for name, data in base.items()}
        if recipe['base_files_sha256'] is not None and recipe['base_files_sha256'] != base_pins:
            raise ValueError('The pinned Git source differs from base_files_sha256')
        base_identity = {'kind': 'git', 'commit': resolved, 'files_sha256': base_pins}
    benchmark_dir = _root(benchmark_dir or ROOT / 'vendor/benchmark')
    benchmark = load(benchmark_dir / 'manifest.json')
    benchmark_pins = _pins(benchmark['files_sha256'])
    actual = _tree(benchmark_dir)
    actual.pop('manifest.json', None)
    if actual != benchmark_pins:
        raise ValueError('The fixed benchmark differs from its manifest')
    copy_data = []
    for item in recipe['file_copies']:
        resolved_copy = None
        if files_root is not None:
            data = _file(_root(files_root), item['source_path']).read_bytes()
        elif git_repo is not None:
            resolved_copy = _resolve(repo, item['source_commit'])
            mode = _git(repo, 'ls-tree', resolved_copy, '--', item['source_path']).decode()
            if not mode.startswith(('100644 blob ', '100755 blob ')):
                raise ValueError('The copied Git path is absent, a link or a directory')
            data = _git(repo, 'show', resolved_copy + ':' + item['source_path'])
        else:
            raise ValueError('File copies need files_root or the pinned Git repository')
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError(f'The copied file differs from its hash: {item["source_path"]}')
        copy_data.append((item, data, resolved_copy))
    binding = {'recipe': recipe, 'base': base_identity, 'benchmark_files_sha256': benchmark_pins,
               'copy_commits': [entry[2] for entry in copy_data], 'materializer_sha256': sha256(__file__)}
    recipe_hash = digest(binding)
    workdir = Path(workdir)
    if workdir.is_symlink():
        raise ValueError('The work directory cannot be a symbolic link')
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    source, receipt_path = workdir / 'source', workdir / 'materialization.json'
    with _locked(workdir / '.materialize.lock'):
        if receipt_path.is_symlink():
            raise ValueError('The materialization receipt cannot be a symbolic link')
        if receipt_path.exists():
            receipt = load(receipt_path)
            changed, edits, copies = _apply_recipe(base, recipe, copy_data)
            expected_hashes = {name: value for name, value in benchmark_pins.items()
                               if not name.startswith('src/point_add/')}
            expected_hashes.update({name: hashlib.sha256(data).hexdigest() for name, data in changed.items()})
            if (receipt.get('recipe_sha256') != recipe_hash or _tree(source) != expected_hashes
                    or receipt.get('source_hashes') != expected_hashes or receipt.get('edits') != edits
                    or receipt.get('file_copies') != copies or receipt.get('base') != base_identity
                    or receipt.get('source_dir') != str(source)
                    or receipt.get('base_tree_sha256') != digest(base_pins)
                    or receipt.get('benchmark_files_sha256') != benchmark_pins
                    or receipt.get('materializer_sha256') != binding['materializer_sha256']
                    or any(receipt.get(key) != recipe[key] for key in
                           ('expected_Q', 'expected_T', 'expected_ops_sha256'))):
                raise ValueError('The saved source or recipe changed after materialization')
            return receipt
        if source.exists():
            raise ValueError('The source directory has no frozen materialization receipt')
        staging = Path(tempfile.mkdtemp(prefix='.materialize-', dir=workdir))
        try:
            for name in benchmark_pins:
                if name.startswith('src/point_add/'):
                    continue
                origin = _file(benchmark_dir, name)
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, target)
                target.chmod(origin.stat().st_mode & 0o777)
            for name, data in base.items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            changed, edit_receipts, copies = _apply_recipe(base, recipe, copy_data)
            for name, data in changed.items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            hashes = _tree(staging)
            expected_hashes = {name: value for name, value in benchmark_pins.items()
                               if not name.startswith('src/point_add/')}
            expected_hashes.update({name: hashlib.sha256(data).hexdigest() for name, data in changed.items()})
            if hashes != expected_hashes:
                raise ValueError('The fixed source changed while copying it')
            receipt = {'schema': 'frontier-materialization-v1', 'target_id': recipe['id'],
                       'source_dir': str(source), 'source_hashes': hashes, 'recipe_sha256': recipe_hash,
                       'parent_commit': base_identity.get('commit', recipe['base_commit']),
                       'base': base_identity, 'base_tree_sha256': digest(base_pins),
                       'benchmark_files_sha256': benchmark_pins, 'edits': edit_receipts,
                       'file_copies': copies, 'materializer_sha256': binding['materializer_sha256'],
                       'expected_Q': recipe['expected_Q'], 'expected_T': recipe['expected_T'],
                       'expected_ops_sha256': recipe['expected_ops_sha256'],
                       'nonce_policy': 'Inherited nonce and tail source are unchanged.',
                       'scope': 'Pinned source materialization; validation and exactness evidence remain separate.'}
            staging.rename(source)
            _write(receipt_path, receipt)
            return receipt
        except Exception as error:
            attempts = workdir / 'attempts'
            attempts.mkdir(exist_ok=True)
            retained = attempts / ('materialize-' + str(time.time_ns()))
            if staging.exists():
                staging.rename(retained)
                _write(retained / 'failure.json', {'error': str(error), 'recipe_sha256': recipe_hash})
            raise
