"""Pinned ordinary Skywalk with typed, static rail-table overlays."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import difflib
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile

from .targeted_frontier import digest, run_process, save, verify_hash

COMMIT = '6e148cfc3aa800ce881aaa48e8c9136d39da6430'
ARCHIVE_SHA256 = '555fce410caf48d42cd121c9db1dc3a790a3db64a0c1f2dbe4c1ca516786377a'
ZERO_OPS_SHA256 = '526d250b3ddced0f407a5e9210eb192d7b977be7ddff83c0be1bd0e0cd0b8028'
ROUNDS = 395
INSTALLED_CAP = 1142
TABLES = {leg: f'src/point_add/skywalk_data/hyb_{leg}_R395_m10_t0_p1t300.txt' for leg in ('div', 'mul')}
SUPPLEMENTS = {
    'NOTICE': 'fdfff837a1a85c14fc8854cfb2fa8a43b2ac58a6b8fcc50e56fe6c9d1293c58f',
    'README.md': 'ac644b8d227144c34245d913b5cbe6857da02faa10c885bf0ad3b14a5d9d6f7a',
    'rust-toolchain': '92b454c1ccf330de9b94242f73bc3214370fb420d5152baeec12dc82ceef9dcc',
}
TAIL = ('''    let nonce: u64 = required_env("TAIL_NONCE");
    let mut x = Op::empty();
    x.kind = OperationType::X;
    x.q_target = QubitId(0);
    ops.extend(std::iter::repeat_n(x, 96));
    ops = apply_tail_nonce(ops, nonce);
''').replace('\n', '\r\n').encode()
TAIL_REPLACEMENT = b'    // The research derivative omits the reviewed 48 identity pairs.\r\n'


def validate_overlay(overlay: list) -> list[dict]:
    """Validate absolute widths. Native constructor guards still apply."""
    if not isinstance(overlay, list):
        raise ValueError('The rail overlay must be an array')
    result, seen = [], set()
    for row in overlay:
        if not isinstance(row, dict) or set(row) - {'leg', 'tick', 'swap_width', 'add_width'}:
            raise ValueError('A rail record has unsupported fields')
        if not {'leg', 'tick'} <= row.keys() or not {'swap_width', 'add_width'} & row.keys():
            raise ValueError('A rail record requires leg, tick, and at least one width')
        if row['leg'] not in ('div', 'mul') or type(row['tick']) is not int or not 0 <= row['tick'] < ROUNDS:
            raise ValueError('A rail record requires leg div|mul and integer tick0..394')
        key = (row['leg'], row['tick'])
        if key in seen:
            raise ValueError('Duplicate rail leg and tick')
        seen.add(key)
        record = {'leg': row['leg'], 'tick': row['tick']}
        for field in ('swap_width', 'add_width'):
            if field in row:
                value = row[field]
                if type(value) is not int or not 1 <= value <= 257:
                    raise ValueError(f'{field} requires an integer1..257')
                record[field] = value
        result.append(record)
    return sorted(result, key=lambda row: (row['leg'], row['tick']))


def _inside(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError('A source path escapes its root')
    return path


def _entry(snapshot: Path) -> dict:
    rows = json.loads((snapshot / 'snapshot/source-manifest.json').read_text())
    matches = [row for row in rows if row['commit'] == COMMIT]
    if len(matches) != 1 or matches[0]['sha256'] != ARCHIVE_SHA256:
        raise ValueError('The pinned source manifest identity differs')
    return matches[0]


def _archive_sources(archive: Path) -> dict[str, bytes]:
    verify_hash(archive, ARCHIVE_SHA256)
    files = {}
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('An archive path escapes its source root')
            if member.isdir():
                continue
            if not member.isfile() or member.name in files:
                raise ValueError('The archive has a link, special file, or duplicate member')
            files[member.name] = stream.extractfile(member).read()
    return files


def original_sources(snapshot: Path, archive: Path | None = None) -> dict[str, bytes]:
    """Read only the pinned archive and its fixed supplemental files."""
    snapshot = Path(snapshot).resolve()
    entry = _entry(snapshot)
    files = _archive_sources(Path(archive) if archive else _inside(snapshot, entry['archive']))
    if {name: hashlib.sha256(data).hexdigest() for name, data in files.items()} != entry['file_sha256']:
        raise ValueError('The original source closure differs')
    seen = set()
    for item in entry.get('supplemental_files', []):
        name = item['original_path']
        if name not in SUPPLEMENTS or name in seen or item['sha256'] != SUPPLEMENTS[name]:
            raise ValueError('The pinned source supplements differ')
        seen.add(name)
        path = _inside(snapshot, item['path'])
        verify_hash(path, SUPPLEMENTS[name])
        data = path.read_bytes()
        if name in files and files[name] != data:
            raise ValueError('A supplement conflicts with the archive')
        files[name] = data
    if seen != SUPPLEMENTS.keys():
        raise ValueError('The pinned source supplements are incomplete')
    return files


def _modified_sources(original: dict[str, bytes], overlay: list) -> dict[str, bytes]:
    overlay = validate_overlay(overlay)
    files = dict(original)
    name = 'src/point_add/mod.rs'
    if files[name].count(TAIL) != 1:
        raise ValueError('The reviewed identity-tail source differs')
    files[name] = files[name].replace(TAIL, TAIL_REPLACEMENT)
    selected = {(row['leg'], row['tick']): row for row in overlay}
    pattern = re.compile(rb'(?P<lead>[ \t]*)(?P<swap_width>[0-9]+)(?P<gap>[ \t]+)(?P<add_width>[0-9]+)(?P<rest>[^\r\n]*)(?P<end>\r?\n)?')
    for leg, name in TABLES.items():
        lines = original[name].splitlines(keepends=True)
        tick = 0
        for index, line in enumerate(lines):
            if not line.strip() or line.lstrip().startswith(b'#'):
                continue
            match = pattern.fullmatch(line)
            if match is None:
                raise ValueError('A pinned rail-table row differs')
            row = selected.get((leg, tick), {})
            edits = [(match.span(field), str(row[field]).encode()) for field in ('swap_width', 'add_width')
                     if field in row and row[field] != int(match[field])]
            for (start, end), value in sorted(edits, reverse=True):
                line = line[:start] + value + line[end:]
            lines[index] = line
            tick += 1
        if tick != ROUNDS:
            raise ValueError('A pinned rail table does not contain395 ticks')
        files[name] = b''.join(lines)
    return files


def _patch(original: dict[str, bytes], files: dict[str, bytes]) -> str:
    return ''.join(''.join(difflib.unified_diff(original[name].decode().splitlines(True), data.decode().splitlines(True),
                          fromfile='a/' + name, tofile='b/' + name))
                   for name, data in files.items() if data != original[name])


def prepare(root: Path, snapshot: Path, overlay: list | None = None) -> dict:
    """Prepare source only. Numerical reliability requires later measurement."""
    root, snapshot = Path(root).resolve(), Path(snapshot).resolve()
    records = validate_overlay([] if overlay is None else overlay)
    original = original_sources(snapshot)
    files = _modified_sources(original, records)
    root.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        path = root / 'source' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    entry = _entry(snapshot)
    shutil.copy2(_inside(snapshot, entry['archive']), root / 'source.tar.gz')
    save(root / 'source-entry.json', {'entry': entry, 'manifest_sha256': digest(snapshot / 'snapshot/source-manifest.json')})
    (root / 'source.sha256.json').write_text(json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                                                      indent=2, sort_keys=True) + '\n')
    (root / 'source.patch').write_text(_patch(original, files))
    save(root / 'identity-tail-witness.json', {
        'rule': '48 adjacentX/X pairs equal identity', 'removed_operations': 96, 'removed_measurements': 0,
        'proof': 'Both X records in each original pair use the same wire0 or1.',
        'preserved': ['coordinates', 'phase', 'workspace', 'addend', 'measurement draws', 'Q', 'T'],
        'official_hash_packet_changes': True, 'official_pass_claim': False})
    for row in entry.get('public_submissions', []):
        if isinstance(row.get('note'), str):
            (root / 'original-submission-note.md').write_text(row['note'])
    recipe = {'source_commit': COMMIT, 'source_archive_sha256': ARCHIVE_SHA256, 'overlay': records,
              'rounds': ROUNDS, 'installed_cap': INSTALLED_CAP, 'removed_identity_operations': 96,
              'constructor_guards_unchanged': True, 'precision_decoder_history_scaling_unchanged': True,
              'source_manifest_sha256': digest(root / 'source.sha256.json'), 'patch_sha256': digest(root / 'source.patch'),
              'postprocess': 'none', 'Q': None, 'T': None, 'audits': 0, 'official_calls': 0}
    save(root / 'recipe.json', recipe)
    return recipe


def verify_source(root: Path) -> dict:
    root = Path(root).resolve()
    recipe = json.loads((root / 'recipe.json').read_text())
    overlay = validate_overlay(recipe.get('overlay', []))
    if recipe['source_commit'] != COMMIT or recipe['source_archive_sha256'] != ARCHIVE_SHA256:
        raise ValueError('The prepared source identity differs from the fixed pin')
    if recipe['rounds'] != ROUNDS or recipe['installed_cap'] != INSTALLED_CAP:
        raise ValueError('The source round count and installed cap stay fixed')
    original = _archive_sources(root / 'source.tar.gz')
    for name, expected in SUPPLEMENTS.items():
        verify_hash(root / 'source' / name, expected)
        original[name] = (root / 'source' / name).read_bytes()
    generated = _modified_sources(original, overlay)
    expected = {name: hashlib.sha256(data).hexdigest() for name, data in generated.items()}
    verify_hash(root / 'source.sha256.json', recipe['source_manifest_sha256'])
    recorded = json.loads((root / 'source.sha256.json').read_text())
    actual = {}
    for path in (root / 'source').rglob('*'):
        if path.is_symlink():
            raise ValueError('A prepared source contains a symbolic link')
        if path.is_file():
            actual[str(path.relative_to(root / 'source'))] = digest(path)
    if actual != expected or recorded != expected:
        raise ValueError('Prepared source hash drift from the admitted rail overlay')
    verify_hash(root / 'source.patch', recipe['patch_sha256'])
    if (root / 'source.patch').read_bytes() != _patch(original, generated).encode():
        raise ValueError('The source patch differs from the admitted rail overlay')
    return recipe


def verify_binary(root: Path) -> dict:
    root = Path(root).resolve()
    verify_source(root)
    binding = json.loads((root / 'binary.json').read_text())
    verify_hash(root / 'build_circuit.bin', binding['sha256'])
    verify_hash(root / 'source.sha256.json', binding['source_manifest_sha256'])
    verify_hash(root / 'build/receipt.json', binding['build_receipt_sha256'])
    if json.loads((root / 'build/receipt.json').read_text())['returncode'] != 0:
        raise ValueError('The bound source build did not pass')
    return binding


def verify_zero_control(root: Path) -> dict:
    """Require the known zero operation stream and its source/binary bindings."""
    root = Path(root).resolve()
    recipe = verify_source(root)
    if validate_overlay(recipe.get('overlay', [])):
        raise ValueError('The required zero control contains a rail overlay')
    binding = verify_binary(root)
    result = json.loads((root / 'emission.json').read_text())
    if result.get('status') != 'emitted' or result.get('source_commit') != COMMIT or result.get('ops_sha256') != ZERO_OPS_SHA256:
        raise ValueError('The known zero-control emission binding differs')
    if result.get('source_manifest_sha256', recipe['source_manifest_sha256']) != recipe['source_manifest_sha256']:
        raise ValueError('The zero-control source binding differs')
    if 'binary_sha256' in result and result['binary_sha256'] != binding['sha256']:
        raise ValueError('The zero-control binary binding differs')
    path = _inside(root, result['ops'])
    verify_hash(path, ZERO_OPS_SHA256)
    return {'root': str(root), 'ops_sha256': ZERO_OPS_SHA256, 'binary_sha256': binding['sha256'],
            'source_manifest_sha256': recipe['source_manifest_sha256']}


def target_for(root: Path) -> Path:
    return Path(root) / 'target'


@contextmanager
def native_slot(root: Path, kind: str, locks_root: Path | None = None):
    directory = Path(locks_root).resolve() if locks_root else root.parent
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f'native-{kind}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def build(root: Path, rust_tools: Path, locks_root: Path | None = None) -> dict:
    root, rust_tools = Path(root).resolve(), Path(rust_tools).resolve()
    verify_source(root)
    chain = rust_tools / 'rustup/toolchains/1.93.0-aarch64-apple-darwin/bin'
    environment = {'PATH': str(chain) + ':/usr/bin:/bin', 'CARGO_HOME': str(rust_tools / 'cargo'),
                   'RUSTUP_HOME': str(rust_tools / 'rustup'), 'RUSTC': str(chain / 'rustc'),
                   'CARGO_TARGET_DIR': str(target_for(root)), 'CARGO_NET_OFFLINE': 'true', 'TMPDIR': str(root)}
    with native_slot(root, 'compiler', locks_root):
        verify_source(root)
        receipt = run_process([str(chain / 'cargo'), 'build', '--release', '--locked', '--offline',
                               '--jobs', '1', '--bin', 'build_circuit'], root / 'source', root / 'build', environment)
        verify_source(root)
        if receipt['returncode']:
            raise RuntimeError('The offline Skywalk build failed; inspect build/receipt.json')
        shutil.copy2(target_for(root) / 'release/build_circuit', root / 'build_circuit.bin')
    binding = {'sha256': digest(root / 'build_circuit.bin'), 'rustc_sha256': digest(chain / 'rustc'),
               'source_manifest_sha256': digest(root / 'source.sha256.json'),
               'build_receipt_sha256': digest(root / 'build/receipt.json')}
    save(root / 'binary.json', binding)
    return binding


def emit(root: Path, locks_root: Path | None = None, zero_control: Path | None = None) -> dict:
    root = Path(root).resolve()
    recipe = verify_source(root)
    binding = verify_binary(root)
    mutant = bool(validate_overlay(recipe.get('overlay', [])))
    if mutant and zero_control is None:
        raise ValueError('A verified zero control is required before mutant emission')
    control = verify_zero_control(zero_control) if mutant else None
    directory = root / 'emit'
    directory.mkdir(parents=True, exist_ok=False)
    policy = '(version 1)(allow default)(deny network*)(deny file-write*)'
    policy += f'(allow file-write* (subpath {json.dumps(str(directory))}))'
    policy += '(allow file-write* (subpath "/dev"))'
    (directory / 'sandbox.sb').write_text(policy)
    with native_slot(root, 'emission', locks_root):
        verify_binary(root)
        if mutant:
            verify_zero_control(zero_control)
        receipt = run_process(['/usr/bin/sandbox-exec', '-f', str(directory / 'sandbox.sb'), str(root / 'build_circuit.bin')],
                              directory, directory / 'process', {'PATH': '/usr/bin:/bin', 'TMPDIR': str(directory)})
        verify_binary(root)
        if mutant:
            verify_zero_control(zero_control)
    result = {'status': 'constructor_rejected' if receipt['returncode'] else 'emitted', 'source_commit': COMMIT,
              'source_manifest_sha256': digest(root / 'source.sha256.json'), 'binary_sha256': binding['sha256'],
              'zero_control': control, 'Q': None, 'T': None, 'audits': 0, 'official_calls': 0}
    if not receipt['returncode']:
        path = directory / 'ops.bin'
        with path.open('rb') as stream:
            header = stream.read(16)
        if len(header) != 16 or header[:8] != b'QECCOPSZ':
            raise ValueError('The emitted operation framing differs')
        result.update(ops='emit/ops.bin', ops_sha256=digest(path), bytes=path.stat().st_size,
                      operations=int.from_bytes(header[8:16], 'little'))
        if not mutant and result['ops_sha256'] != ZERO_OPS_SHA256:
            result['status'] = 'zero_control_hash_mismatch'
    save(root / 'emission.json', result)
    return result


def _overlay_file(path: Path) -> list:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('The overlay JSON contains a duplicate field')
            value[key] = item
        return value
    return validate_overlay(json.loads(Path(path).read_text(), object_pairs_hook=unique))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--locks-root', type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    preparation = sub.add_parser('prepare')
    preparation.add_argument('--snapshot', type=Path, required=True)
    preparation.add_argument('--overlay', type=Path)
    compilation = sub.add_parser('build')
    compilation.add_argument('--rust-tools', type=Path, required=True)
    emission = sub.add_parser('emit')
    emission.add_argument('--zero-control', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        result = prepare(args.root, args.snapshot, _overlay_file(args.overlay) if args.overlay else [])
    elif args.action == 'build':
        result = build(args.root, args.rust_tools, args.locks_root)
    else:
        result = emit(args.root, args.locks_root, args.zero_control)
    print(json.dumps(result, indent=2), flush=True)
    return int(result.get('status') in ('constructor_rejected', 'zero_control_hash_mismatch'))


if __name__ == '__main__':
    raise SystemExit(main())
