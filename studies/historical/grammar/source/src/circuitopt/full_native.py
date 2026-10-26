"""Emit frozen operation files and evaluate them in a separate fixed process."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

from . import full_path


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def assert_hash(path, expected):
    if file_hash(path) != expected:
        raise ValueError(f'Frozen artifact changed: {path}')


def normalize(raw):
    failed = raw.get('all_failures', [])
    channels = {key: len({r['idx'] for r in failed if r['failures'].get(key)})
                for key in ['coordinate', 'phase', 'workspace', 'addend']}
    return {'Q': raw['qubits'], 'T': raw['mean_toffoli'], 'S': raw['shared_rounded_score'],
            'pairs': raw['pairs'], 'streams': raw['streams'],
            'failed_inputs': raw['failed_inputs'], 'failed_shots': raw['failed_shots'],
            'channel_failed_inputs': channels,
            'epsilon_upper95': full_path.failure_upper(raw['failed_inputs'], raw['pairs']),
            'first_failures': failed[:2]}


def reserve_fresh_test(root, candidate, label, pairs):
    directory = Path(root) / 'fresh-tests'
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f'{candidate}\n{label}\n{pairs}'.encode()).hexdigest()
    with (directory / (key + '.json')).open('x') as stream:
        json.dump({'candidate': candidate, 'label': label, 'pairs': pairs,
                   'status': 'consumed before evaluation'}, stream)


def freeze_tools(project, root):
    project, root = Path(project), Path(root)
    target = root / 'tools'
    target.mkdir(parents=True, exist_ok=False)
    tools = {}
    for name in ['full-point-add-generator', 'study-check']:
        source = project / 'native/target/release' / name
        shutil.copy2(source, target / name)
        tools[name] = {'file': f'tools/{name}', 'sha256': file_hash(target / name)}
    sources = {}
    for path in sorted((project / 'native').rglob('*')):
        relative = path.relative_to(project)
        if not path.is_file() or any(p in relative.parts for p in ['target', 'logs', '__pycache__']):
            continue
        sources[str(relative)] = file_hash(path)
        destination = root / 'source' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    for path in sorted((project / 'src/circuitopt').rglob('*.py')):
        relative = path.relative_to(project)
        destination = root / 'source' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        sources[str(relative)] = file_hash(path)
    save(root / 'tools.json', {'executables': tools, 'native_sources': sources})
    return tools


def command(root, argv, *, directory, timeout=1800, sandbox=False):
    root, directory = Path(root), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    actual = list(map(str, argv))
    if sandbox:
        if sys.platform != 'darwin':
            raise RuntimeError('This native emission runner requires the declared macOS sandbox')
        policy = '(version 1)(allow default)(deny network*)(deny file-write*)' \
                 f'(allow file-write* (subpath {json.dumps(str(directory.resolve()))}))' \
                 '(allow file-write* (subpath "/dev"))'
        (directory / 'sandbox.sb').write_text(policy)
        actual = ['/usr/bin/sandbox-exec', '-f', str(directory / 'sandbox.sb')] + actual
    started = time.monotonic()
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    environment = {'PATH': '/usr/bin:/bin', 'TMPDIR': str(directory.resolve())}
    receipt = {'command': actual, 'environment': environment, 'status': 'started'}
    save(directory / 'process.json', receipt)
    try:
        run = subprocess.run(actual, cwd=directory, env=environment, capture_output=True,
                             text=True, timeout=timeout)
        (directory / 'stdout.txt').write_text(run.stdout)
        (directory / 'stderr.txt').write_text(run.stderr)
        receipt.update(returncode=run.returncode, status='completed')
    except (subprocess.TimeoutExpired, OSError) as exc:
        for name in ['stdout', 'stderr']:
            output = getattr(exc, name, '') or ''
            if isinstance(output, bytes):
                output = output.decode(errors='replace')
            (directory / (name + '.txt')).write_text(output)
        receipt.update(returncode=None, status='failed', error=type(exc).__name__)
        raise
    finally:
        cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        receipt.update(wall_seconds=time.monotonic() - started,
                       cpu_seconds=cpu_after.ru_utime + cpu_after.ru_stime - cpu_before.ru_utime - cpu_before.ru_stime,
                       children_peak_rss_high_water=cpu_after.ru_maxrss,
                       rss_scope='Cumulative child high-water mark in platform units; not an isolated per-command peak.')
        save(directory / 'process.json', receipt)
    return run, receipt


def load_tools(root):
    from . import full_integrity
    return full_integrity.read_sealed(Path(root) / 'tools.json')['executables']


def emit(root, recipe):
    root = Path(root).resolve()
    tools = load_tools(root)
    binary = root / tools['full-point-add-generator']['file']
    assert_hash(binary, tools['full-point-add-generator']['sha256'])
    cid = full_path.recipe_id(recipe)
    directory = root / 'circuits' / cid
    directory.mkdir(parents=True, exist_ok=False)
    save(directory / 'recipe.json', recipe)
    argv = [binary, *full_path.native_arguments(recipe), '--out', directory / 'ops.bin']
    proc, receipt = command(root, argv, directory=directory, sandbox=True)
    if proc.returncode != 0:
        raise RuntimeError(f'Emission failed: {proc.stderr[-1200:]}')
    metadata = {'recipe': recipe, 'id': cid, 'operation_file': f'circuits/{cid}/ops.bin',
                'sha256': file_hash(directory / 'ops.bin'), 'bytes': (directory / 'ops.bin').stat().st_size,
                'emission': receipt}
    save(directory / 'artifact.json', metadata)
    return metadata


def evaluate(root, artifact, *, label, pairs, name, fresh=False, threads=4):
    root = Path(root).resolve()
    cid = artifact['id']
    if fresh:
        reserve_fresh_test(root, cid, label, pairs)
    tools = load_tools(root)
    binary = root / tools['study-check']['file']
    assert_hash(binary, tools['study-check']['sha256'])
    ops = root / artifact['operation_file']
    assert_hash(ops, artifact['sha256'])
    directory = root / 'evaluations' / name
    directory.mkdir(parents=True, exist_ok=False)
    argv = [binary, '--ops', ops, '--pairs', str(pairs), '--streams', '2', '--threads', str(threads),
            '--label', label, '--out', directory / 'raw.json',
            '--per-input', directory / 'per-input.jsonl', '--progress', directory / 'progress.json']
    proc, receipt = command(root, argv, directory=directory, timeout=14400 if pairs > 16384 else 1800)
    if proc.returncode not in [0, 1]:
        raise RuntimeError(f'Evaluator instrument error: {proc.stderr[-1200:]}')
    raw = json.loads((directory / 'raw.json').read_text())
    if raw['ops_sha256'] != artifact['sha256'] or raw['pairs'] != pairs or raw['label'] != label:
        raise ValueError('Evaluator receipt does not match the requested frozen circuit and cohort')
    result = {**normalize(raw), 'recipe': artifact['recipe'], 'id': cid,
              'artifact_sha256': artifact['sha256'], 'label': label, 'fresh': fresh,
              'evaluation_file': str((directory / 'raw.json').relative_to(root)),
              'packet_sha256': raw['packet_sha256'], 'wall_seconds': receipt['wall_seconds']}
    raw_inputs = directory / 'per-input.jsonl'
    with raw_inputs.open('rb') as source, (directory / 'per-input.jsonl.gz').open('wb') as destination:
        with gzip.GzipFile(filename='', mode='wb', fileobj=destination, mtime=0) as compressed:
            shutil.copyfileobj(source, compressed)
    result['inputs_sha256'] = file_hash(raw_inputs)
    raw_inputs.unlink()
    save(directory / 'result.json', result)
    return result
