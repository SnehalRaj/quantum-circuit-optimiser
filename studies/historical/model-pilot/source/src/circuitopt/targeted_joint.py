"""Source-pinned joint-array Skywalk constructors with fixed arithmetic precision."""
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

COMMIT = 'd690a7e3074bdfeff3a9f2e89fa9498bc54f43a2'
PARENT_COMMIT = '3836ec797d0998c8367ce46b0f5bdaca55873d6e'
ARCHIVE_SHA256 = '90c734494babb0d7485932b722c752effa3628527f2f9f90622d818e7c48c0e3'
CAPS = ('1111', '1112', '1113')
DEFAULTS = {
    'HEO_PIN_PP_WALK_MAX_QUBITS': '1111',
    'D2_A': '288', 'D2_TICK_WIN_K': '1', 'D2_STACK_MARGIN': '3', 'D2_PEBBLES': '6',
    'D2_GIDNEY': '0', 'D2_CELL_POOL': '1', 'D2_DETECT_AFTER_PUSH': '1',
    'D2_CELL_ROOM_PINS': '0', 'D2_RAIL_LEAN': '1', 'D2_RAIL_LEND': '1',
    'D2_STACK_BUDGET': '1',
}
SUPPLEMENTS = {
    'NOTICE': 'fdfff837a1a85c14fc8854cfb2fa8a43b2ac58a6b8fcc50e56fe6c9d1293c58f',
    'README.md': 'ac644b8d227144c34245d913b5cbe6857da02faa10c885bf0ad3b14a5d9d6f7a',
    'rust-toolchain': '92b454c1ccf330de9b94242f73bc3214370fb420d5152baeec12dc82ceef9dcc',
}
TAIL = '''    let nonce: u64 = required_env("TAIL_NONCE");
    let mut x = Op::empty();
    x.kind = OperationType::X;
    x.q_target = QubitId(0);
    ops.extend(std::iter::repeat_n(x, 96));
    ops = apply_tail_nonce(ops, nonce);
'''


def validate_recipe(changes: dict) -> dict[str, str]:
    """Admit supported caps while keeping every precision control fixed."""
    if not isinstance(changes, dict):
        raise ValueError('Joint settings must be an object')
    if set(changes) - DEFAULTS.keys():
        raise ValueError('Unsupported joint settings')
    result = dict(DEFAULTS)
    for key, value in changes.items():
        if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]*', value):
            raise ValueError(f'{key} requires canonical decimal text')
        if key == 'HEO_PIN_PP_WALK_MAX_QUBITS':
            if value not in CAPS:
                raise ValueError('Joint cap requires 1111, 1112 or 1113; cap1110 has a known detector overshoot')
        elif value != DEFAULTS[key]:
            raise ValueError(f'{key} stays fixed at {DEFAULTS[key]}')
        result[key] = value
    return result


def first_recipes() -> list[dict]:
    return [{'id': f'joint-cap{cap}', 'changes': {'HEO_PIN_PP_WALK_MAX_QUBITS': cap}} for cap in CAPS]


def _entry(snapshot: Path) -> dict:
    path = snapshot / 'snapshot/source-manifest.json'
    matches = [row for row in json.loads(path.read_text()) if row['commit'] == COMMIT]
    if len(matches) != 1 or matches[0]['sha256'] != ARCHIVE_SHA256:
        raise ValueError('Pinned source manifest identity differs')
    return matches[0]


def _inside(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError('A source path escapes the snapshot')
    return path


def _pinned_archive_files(archive: Path) -> dict[str, bytes]:
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
                raise ValueError('The archive has a link, special file or duplicate member')
            files[member.name] = stream.extractfile(member).read()
    return files


def original_sources(snapshot: Path, archive: Path | None = None) -> dict[str, bytes]:
    """Verify the complete original archive and its pinned supplemental files."""
    snapshot = Path(snapshot).resolve()
    entry = _entry(snapshot)
    archive = Path(archive) if archive else _inside(snapshot, entry['archive'])
    files = _pinned_archive_files(archive)
    if {name: hashlib.sha256(data).hexdigest() for name, data in files.items()} != entry['file_sha256']:
        raise ValueError('The original source file closure differs')
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
            raise ValueError('A source supplement conflicts with the archive')
        files[name] = data
    if seen != SUPPLEMENTS.keys():
        raise ValueError('The original source supplements are incomplete')
    return files


def _modified_sources(original: dict[str, bytes], settings: dict) -> dict[str, bytes]:
    files = dict(original)
    name = 'src/point_add/mod.rs'
    text = files[name].decode()
    anchor = '("HEO_PIN_PP_WALK_MAX_QUBITS", "1111")'
    if text.count(anchor) != 1 or text.count(TAIL) != 1:
        raise ValueError('The reviewed source anchors differ')
    cap = settings['HEO_PIN_PP_WALK_MAX_QUBITS']
    text = text.replace(anchor, f'("HEO_PIN_PP_WALK_MAX_QUBITS", "{cap}")')
    text = text.replace(TAIL, '    // The research derivative omits the reviewed 48 identity pairs.\n')
    files[name] = text.encode()
    return files


def prepare(root: Path, snapshot: Path, changes: dict | None = None) -> dict:
    """Retain the source and exact patch. This step performs no native execution."""
    root, snapshot = Path(root).resolve(), Path(snapshot).resolve()
    settings = validate_recipe(changes if changes is not None else {})
    original = original_sources(snapshot)
    files = _modified_sources(original, settings)
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'source'
    source.mkdir()
    for name, data in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    entry = _entry(snapshot)
    shutil.copy2(_inside(snapshot, entry['archive']), root / 'source.tar.gz')
    save(root / 'source-entry.json', {'entry': entry, 'manifest_sha256': digest(snapshot / 'snapshot/source-manifest.json')})
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    # This serialization matches the verified campaign source manifests.
    (root / 'source.sha256.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    name = 'src/point_add/mod.rs'
    patch = ''.join(difflib.unified_diff(original[name].decode().splitlines(True), files[name].decode().splitlines(True),
                                       fromfile='a/' + name, tofile='b/' + name))
    (root / 'source.patch').write_text(patch)
    witness = {'rule': '48 adjacent X/X pairs equal identity', 'removed_operations': 96,
               'measurement_operations_removed': 0, 'source_file': name, 'original_lines': '571-576',
               'proof': 'apply_tail_nonce chooses the same wire, zero or one, for both X gates in each pair.',
               'preserved': ['coordinates', 'phase', 'workspace', 'addend', 'measurement draws', 'Q', 'T'],
               'official_hash_packet_changes': True, 'official_pass_claim': False}
    save(root / 'identity-tail-witness.json', witness)
    for row in entry.get('public_submissions', []):
        if isinstance(row.get('note'), str):
            (root / 'original-submission-note.md').write_text(row['note'])
    recipe = {'source_commit': COMMIT, 'source_archive_sha256': ARCHIVE_SHA256,
              'family_parent': PARENT_COMMIT, 'settings': settings, 'removed_identity_operations': 96,
              'rounds': 395, 'ticks': 394, 'checkpoints': 27, 'numerical_envelopes_unchanged': True,
              'constructor_guards_unchanged': True, 'source_manifest_sha256': digest(root / 'source.sha256.json'),
              'patch_sha256': digest(root / 'source.patch'), 'postprocess': 'none',
              'audits': 0, 'official_calls': 0}
    save(root / 'recipe.json', recipe)
    return recipe


def verify_source(root: Path) -> dict:
    recipe = json.loads((root / 'recipe.json').read_text())
    settings = validate_recipe(recipe['settings'])
    if recipe['source_commit'] != COMMIT or recipe['source_archive_sha256'] != ARCHIVE_SHA256:
        raise ValueError('Prepared source identity differs from the fixed pin')
    archive = root / 'source.tar.gz'
    if not archive.exists():
        archive = root / f'{COMMIT}.tar.gz'
    original = _pinned_archive_files(archive)
    for name, expected_hash in SUPPLEMENTS.items():
        verify_hash(root / 'source' / name, expected_hash)
        original[name] = (root / 'source' / name).read_bytes()
    generated = _modified_sources(original, settings)
    pinned_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in generated.items()}
    verify_hash(root / 'source.sha256.json', recipe['source_manifest_sha256'])
    expected = json.loads((root / 'source.sha256.json').read_text())
    actual = {str(path.relative_to(root / 'source')): digest(path)
              for path in (root / 'source').rglob('*') if path.is_file()}
    if actual != expected or actual != pinned_hashes:
        raise ValueError('Prepared source hash drift from the fixed constructor')
    return recipe


def verify_binary(root: Path) -> dict:
    verify_source(root)
    binding = json.loads((root / 'binary.json').read_text())
    verify_hash(root / 'build_circuit.bin', binding['sha256'])
    verify_hash(root / 'source.sha256.json', binding['source_manifest_sha256'])
    return binding


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
        receipt = run_process([str(chain / 'cargo'), 'build', '--release', '--locked', '--offline',
                               '--jobs', '1', '--bin', 'build_circuit'], root / 'source', root / 'build', environment)
        verify_source(root)
        if receipt['returncode']:
            raise RuntimeError('The offline joint builder failed; inspect build/receipt.json')
        binary = root / 'build_circuit.bin'
        shutil.copy2(target_for(root) / 'release/build_circuit', binary)
    result = {'sha256': digest(binary), 'rustc_sha256': digest(chain / 'rustc'),
              'source_manifest_sha256': digest(root / 'source.sha256.json'),
              'build_receipt_sha256': digest(root / 'build/receipt.json')}
    save(root / 'binary.json', result)
    return result


def native_test(root: Path, kind: str = 'integration', locks_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    if kind not in ('integration', 'primitive'):
        raise ValueError('The native test kind requires integration or primitive')
    verify_binary(root)
    environment = {'PATH': '/usr/bin:/bin', 'TMPDIR': str(root)}
    if kind == 'integration':
        directory = root / 'integration-test'
        environment.update(D2_INT_SELFTEST='1', D2_INT_SELFTEST_ONLY='L', D2_INT_SELFTEST_LANES='64',
                           D2_INT_SELFTEST_MAXFAIL='0', D2_INT_SELFTEST_SEED='20261024')
    else:
        directory = root / 'native-test'
        environment.update(D2_TICK_SELFTEST='1', D2_TICK_SELFTEST_ONLY='sweep,full', D2_TICK_SELFTEST_FULL_LANES='64')
    with native_slot(root, 'emission', locks_root):
        receipt = run_process([str(root / 'build_circuit.bin')], root, directory, environment)
    verify_binary(root)
    receipt['scope'] = 'Native constructor simulation; no independent qualification or official check.'
    save(directory / 'receipt.json', receipt)
    return receipt


def require_constructor_test(root: Path) -> None:
    root = Path(root)
    record = root / 'integration-test'
    receipt = json.loads((record / 'receipt.json').read_text())
    settings = validate_recipe(json.loads((root / 'recipe.json').read_text())['settings'])
    cap = settings['HEO_PIN_PP_WALK_MAX_QUBITS']
    text = (record / 'stderr.txt').read_text()
    found = re.findall(r'D2_INT_SELFTEST tests=L lanes=64 seed=20261024 cap=(\d+)\b', text)
    if receipt['returncode'] != 0 or found != [cap] or 'D2_INT_SELFTEST PASS' not in text:
        raise ValueError('The native constructor test must pass at this exact cap before emission')


def emit(root: Path, locks_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    verify_binary(root)
    require_constructor_test(root)
    directory = root / 'emit'
    directory.mkdir(parents=True, exist_ok=False)
    with native_slot(root, 'emission', locks_root):
        receipt = run_process([str(root / 'build_circuit.bin')], directory, directory / 'process',
                              {'PATH': '/usr/bin:/bin', 'TMPDIR': str(directory)})
    verify_binary(root)
    result = {'status': 'constructor_rejected' if receipt['returncode'] else 'emitted',
              'source_commit': COMMIT, 'audits': 0, 'official_calls': 0, 'Q': None, 'T': None}
    if not receipt['returncode']:
        path = directory / 'ops.bin'
        with path.open('rb') as stream:
            header = stream.read(16)
        if header[:8] != b'QECCOPSZ':
            raise ValueError('The emitted stream has unsupported framing')
        result.update(ops='emit/ops.bin', ops_sha256=digest(path), bytes=path.stat().st_size,
                      operations=int.from_bytes(header[8:16], 'little'))
    save(root / 'emission.json', result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--locks-root', type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    preparation = sub.add_parser('prepare')
    preparation.add_argument('--snapshot', type=Path, required=True)
    preparation.add_argument('--cap', choices=CAPS, default='1111')
    compilation = sub.add_parser('build')
    compilation.add_argument('--rust-tools', type=Path, required=True)
    sub.add_parser('recipes')
    sub.add_parser('native-test')
    sub.add_parser('integration-test')
    sub.add_parser('emit')
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        result = prepare(args.root, args.snapshot, {'HEO_PIN_PP_WALK_MAX_QUBITS': args.cap})
    elif args.action == 'build':
        result = build(args.root, args.rust_tools, args.locks_root)
    elif args.action == 'recipes':
        result = first_recipes()
    elif args.action in ('native-test', 'integration-test'):
        result = native_test(args.root, 'primitive' if args.action == 'native-test' else 'integration', args.locks_root)
    else:
        result = emit(args.root, args.locks_root)
    print(json.dumps(result, indent=2), flush=True)
    if isinstance(result, dict) and (result.get('returncode', 0) or result.get('status') == 'constructor_rejected'):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
