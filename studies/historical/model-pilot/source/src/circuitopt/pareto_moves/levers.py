"""Prepare, build, emit and check one candidate tree (from runs/frontier-levers-20261025/code/levers.py).

A candidate tree is the fixed harness archive with the parent's overlay folder (src/point_add)
and a list of counted edits. An edit is a whole-file take from a donor archive or a counted
string replacement. Build and emission products stay in the scratch area. The receipts in
tracks/<track>/<id>/ record every hash, so the operation file can be rebuilt from source.
"""
import gzip
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time

from .layout import MoveError, load, relative, require, save, sha, utc


def _tree_hashes(source):
    return {str(p.relative_to(source)): sha(p) for p in sorted(source.rglob('*')) if p.is_file()}


def _extract(archive, target, keep):
    with tarfile.open(archive) as tree:
        members = [m for m in tree.getmembers() if keep(m.name)]
        tree.extractall(target, members=members, filter='data')


def _build_tree(campaign, source, archive, planned, takes):
    """Extract the harness and the parent overlay, then apply the takes and the counted replacements."""
    overlay = campaign.overlay
    _extract(campaign.harness, source, lambda name: not name.startswith(overlay))
    _extract(archive, source, lambda name: name.startswith(overlay))
    edits = []
    for rel, donor in takes:
        donor_archive = campaign.source_archive(donor)
        with tarfile.open(donor_archive) as tree:
            try:
                data = tree.extractfile(rel).read()
            except (KeyError, AttributeError):
                raise MoveError(f'The donor archive {donor_archive.name} has no file {rel}') from None
        before = sha(source / rel) if (source / rel).exists() else None
        (source / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / rel).write_bytes(data)
        edits.append({'file': rel, 'taken_from_archive': relative(donor_archive, campaign.repo),
                      'donor_archive_sha256': sha(donor_archive), 'before_sha256': before,
                      'after_sha256': sha(source / rel)})
    for old, new, rel, count in planned:
        path = source / rel
        require(path.is_file(), f'The candidate tree has no file {rel}')
        text = path.read_text()
        found = text.count(old)
        require(found == count, f'{rel}: expected {count} occurrence(s) of {old!r}, found {found}')
        path.write_text(text.replace(old, new))
        edits.append({'file': rel, 'old': old, 'new': new, 'count': count})
    return edits


def prepare(campaign, cid, parent, replacements=(), takes=()):
    """Create the candidate tree and its manifest.

    `replacements` holds (old, new, file) or (old, new, file, count). Each `old` must occur exactly
    `count` times (default 1). `takes` holds (file, donor_commit): the file is copied byte for byte
    from the donor archive. Takes apply first, then replacements, in the given order.
    """
    home = campaign.candidate(cid)
    source = campaign.work(cid) / 'source'
    require(not home.exists() and not source.exists(), f'Preserve the existing candidate {cid}.')
    archive = campaign.source_archive(parent)
    overlay = campaign.overlay
    planned = [tuple(item) + (1,) * (4 - len(item)) for item in replacements]
    for old, _new, _rel, count in planned:
        require(isinstance(count, int) and count >= 1, f'A replacement count must be a positive integer: {old!r}')
    source.mkdir(parents=True)
    try:
        edits = _build_tree(campaign, source, archive, planned, takes)
    except Exception:
        shutil.rmtree(source)  # a refused edit leaves no partial tree, so a corrected retry can use the same id
        raise
    files = _tree_hashes(source)
    save(home / 'manifest.json', {
        'id': cid, 'created_utc': utc(), 'parent_commit_prefix': parent,
        'parent_archive': relative(archive, campaign.repo), 'parent_archive_sha256': sha(archive),
        'harness_archive': relative(campaign.harness, campaign.repo), 'harness_archive_sha256': sha(campaign.harness),
        'edits': edits, 'files_sha256': files,
        'scope': f'Fixed harness {campaign.harness.name[:8]} plus the parent {overlay} and the counted edits.'})
    return {'id': cid, 'files': len(files), 'edits': edits}


def verify_source(campaign, cid):
    """Return the candidate tree after a check against its manifest."""
    manifest = load(campaign.candidate(cid) / 'manifest.json')
    source = campaign.work(cid) / 'source'
    require(_tree_hashes(source) == manifest['files_sha256'], 'Source tree differs from its manifest.')
    return source


def build(campaign, cid, execute=subprocess.run):
    """Build build_circuit in a fresh target directory under the compiler lock."""
    source = verify_source(campaign, cid)
    target = campaign.work(cid) / 'target'
    require(not target.exists(), 'Use a fresh target directory.')
    chain = campaign.chain
    argv = [str(chain / 'cargo'), 'build', '--release', '--locked', '--offline', '--jobs', str(campaign.build_jobs),
            '--bin', 'build_circuit']
    with campaign.lock('native-compiler.lock'):
        started = time.monotonic()
        done = execute(argv, cwd=source, env=campaign.cargo_env(target), capture_output=True, text=True)
        wall = time.monotonic() - started
    binary = target / 'release/build_circuit'
    receipt = {'argv': argv, 'returncode': done.returncode, 'wall_seconds': wall, 'finished_utc': utc(),
               'cargo_sha256': sha(chain / 'cargo'), 'rustc_sha256': sha(chain / 'rustc'),
               'binary_sha256': sha(binary) if binary.is_file() else None, 'stderr_tail': done.stderr[-4000:]}
    save(campaign.candidate(cid) / 'build.json', receipt)
    require(done.returncode == 0, done.stderr[-2000:])
    return {k: receipt[k] for k in ('returncode', 'wall_seconds', 'binary_sha256')}


def emit(campaign, cid, execute=subprocess.run):
    """Run build_circuit once in an empty folder with a minimal environment."""
    home = campaign.candidate(cid)
    build_receipt = load(home / 'build.json')
    binary = campaign.work(cid) / 'target/release/build_circuit'
    require(sha(binary) == build_receipt['binary_sha256'], 'The built binary differs from its build receipt.')
    folder = campaign.work(cid) / 'emission'
    folder.mkdir()
    env = {'PATH': campaign.child_path}
    with campaign.lock('native-emission.lock'):
        started = time.monotonic()
        done = execute([*campaign.time_wrapper, str(binary)], cwd=folder, env=env, capture_output=True, text=True)
        wall = time.monotonic() - started
    rss = re.search(r'(\d+)\s+maximum resident set size', done.stderr)
    ops = folder / 'ops.bin'
    (home / 'emission.stdout.txt').write_text(done.stdout)
    (home / 'emission.stderr.txt').write_text(done.stderr)
    counted = re.search(r'emitted ops\s*:\s*(\d+)', done.stdout + done.stderr)
    receipt = {'argv': [str(binary)], 'env': env, 'returncode': done.returncode,
               'wall_seconds': wall, 'peak_rss_bytes': int(rss.group(1)) if rss else None, 'finished_utc': utc(),
               'binary_sha256': sha(binary), 'emitted_ops': int(counted.group(1)) if counted else None,
               'ops_sha256': sha(ops) if ops.is_file() else None, 'ops_bytes': ops.stat().st_size if ops.is_file() else None,
               'ops_location': str(ops)}
    save(home / 'emission.json', receipt)
    require(done.returncode == 0, f'build_circuit returned {done.returncode}')
    return {k: receipt[k] for k in ('wall_seconds', 'peak_rss_bytes', 'emitted_ops', 'ops_sha256')}


def checker_for(campaign, emitted_ops):
    """Return the frozen checker, or the validated large copy above its operation guard."""
    require(emitted_ops is not None, 'The emission receipt has no operation count.')
    return campaign.checker if emitted_ops <= campaign.large_checker_from_ops else campaign.large_checker


def development_argv(checker, ops, out, *, pairs, streams, threads, label):
    return [str(checker), '--ops', str(ops), '--pairs', str(pairs), '--streams', str(streams), '--threads', str(threads),
            '--label', label, '--out', str(out / 'raw.json'), '--per-input', str(out / 'per-input.jsonl'),
            '--progress', str(out / 'progress.json')]


def gzip_outcomes(folder):
    """Compress per-input.jsonl reproducibly (mtime 0) and remove the plain file."""
    with (folder / 'per-input.jsonl').open('rb') as src, (folder / 'per-input.jsonl.gz').open('wb') as dst:
        with gzip.GzipFile(filename='', mode='wb', fileobj=dst, mtime=0) as packed:
            shutil.copyfileobj(src, packed)
    (folder / 'per-input.jsonl').unlink()


def develop(campaign, cid, execute=subprocess.run):
    """Run the development check on the fixed development label. Development data never qualifies a circuit."""
    home = campaign.candidate(cid)
    receipt = load(home / 'emission.json')
    ops = Path(receipt['ops_location'])
    require(sha(ops) == receipt['ops_sha256'], 'The operation file differs from its emission receipt.')
    out = home / 'development'
    out.mkdir()
    checker = checker_for(campaign, receipt['emitted_ops'])
    argv = development_argv(checker, ops, out, pairs=campaign.dev_pairs, streams=campaign.streams,
                            threads=campaign.threads, label=campaign.dev_label)
    started = time.monotonic()
    done = execute(argv, capture_output=True, text=True, env={'PATH': campaign.child_path, 'TMPDIR': str(out)})
    wall = time.monotonic() - started
    (out / 'stdout.txt').write_text(done.stdout)
    (out / 'stderr.txt').write_text(done.stderr)
    require(done.returncode in (0, 1), done.stderr[-2000:])
    gzip_outcomes(out)
    raw = load(out / 'raw.json')
    require(raw['ops_sha256'] == receipt['ops_sha256'] and raw['label'] == campaign.dev_label
            and raw['pairs'] == campaign.dev_pairs, 'The checker measured other bytes, inputs or sizes.')
    save(out / 'process.json', {'argv': argv, 'returncode': done.returncode, 'wall_seconds': wall,
                                'checker_sha256': sha(checker), 'finished_utc': utc()})
    return {'id': cid, 'Q': raw['qubits'], 'T': raw['mean_toffoli'], 'failed_inputs': raw['failed_inputs'],
            'failed_shots': raw['failed_shots'], 'wall_seconds': wall}


def compare(campaign, cid, parent):
    """Paired A/B on one development packet. An exact edit leaves the failing input set unchanged."""
    a = load(campaign.candidate(parent) / 'development/raw.json')
    b = load(campaign.candidate(cid) / 'development/raw.json')
    result = {'child': cid, 'parent': parent, 'label': campaign.dev_label,
              'packet_equal': a['packet_sha256'] == b['packet_sha256'],
              'Q': [a['qubits'], b['qubits']], 'T': [a['mean_toffoli'], b['mean_toffoli']],
              'T_change': b['mean_toffoli'] - a['mean_toffoli'],
              'failed_indices_equal': a['failed_indices'] == b['failed_indices'],
              'failed_indices': [a['failed_indices'], b['failed_indices']],
              'failed_shots': [a['failed_shots'], b['failed_shots']]}
    save(campaign.candidate(cid) / 'compare.json', result)
    return result
