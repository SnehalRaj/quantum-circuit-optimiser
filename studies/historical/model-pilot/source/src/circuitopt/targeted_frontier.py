"""Pinned SKY-COF constructors. This module performs no circuit evaluation."""
from __future__ import annotations

import argparse
import difflib
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time

COMMIT = 'ceffcd216c2a4a987987686fee48d89f54b60c41'
ARCHIVE_SHA256 = '7e2569ab252eb2b6613e80378bbc8b99dbfdccc1ea5eb2b24e60554868ecb5ae'
ORIGINAL_SHA256 = '17101bf04a6ab54e1ad01809e5a90363ce28ed4dc4799e228df97da496b5acab'
CLEAN_SHA256 = 'd56bc40cf50ae8bc7b6693b73d04fe93f3d478cfe0d85592108ac13c047e4837'
GRAMMAR_PATH = Path(__file__).parent / 'data/grammars/targeted-frontier-v1.json'
GRAMMAR = json.loads(GRAMMAR_PATH.read_text())
SETTINGS = GRAMMAR['backends']['skycof_pinned']['settings']
DEFAULTS = {name: spec['default'] for name, spec in SETTINGS.items()}
# These are admission ranges for this backend, rather than general constructor claims.
RANGES = {name: (spec['min'], spec['max']) for name, spec in SETTINGS.items()
          if spec['kind'] == 'int'}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path: Path, value) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(path) + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def verify_hash(path: Path, expected: str) -> None:
    if digest(path) != expected:
        raise ValueError(f'Pinned source hash differs: {path}')


def validate_recipe(changes: dict, *, rail_profile: bool = False) -> dict[str, str]:
    if not isinstance(changes, dict):
        raise ValueError('Recipe settings must be an object')
    changes = dict(changes)
    has_margin = 'SKYCOF_RAIL_MARGIN' in changes
    margin = changes.pop('SKYCOF_RAIL_MARGIN', None)
    if has_margin:
        if not rail_profile:
            raise ValueError('The original builder does not admit a rail profile margin')
        if not isinstance(margin, str) or margin not in ('0', '1', '2'):
            raise ValueError('SKYCOF_RAIL_MARGIN requires 0, 1, or 2')
    unknown = set(changes) - DEFAULTS.keys()
    if unknown:
        raise ValueError(f'Unsupported settings: {sorted(unknown)}')
    result = dict(DEFAULTS)
    for key, value in changes.items():
        if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]*', value):
            raise ValueError(f'{key} requires canonical decimal text')
        if key in RANGES:
            low, high = RANGES[key]
            if not low <= int(value) <= high:
                raise ValueError(f'{key} must be in {low}..{high}')
        elif value not in ('0', '1'):
            raise ValueError(f'{key} requires 0 or 1')
        result[key] = value
    if result['SKYCOF_RESEARCH'] != '1' or result['SKYCOF_FRAME'] != '1':
        raise ValueError('This adapter requires research activation and the frame route')
    # Preserve the active lowq_adapter room>=90 assertions in the untouched source.
    if rail_profile and margin not in (None, '0'):
        result['SKYCOF_RAIL_MARGIN'] = margin
    return result


def recipe_environment(changes: dict, *, rail_profile: bool = False) -> dict[str, str]:
    return {'PATH': '/usr/bin:/bin', **validate_recipe(changes, rail_profile=rail_profile)}


def first_recipes() -> list[dict]:
    return [
        {'id': 'control', 'changes': {}},
        {'id': 'cap1096', 'changes': {'SKYCOF_CAP': '1096'}},
        {'id': 'cap1096-guard26', 'changes': {'SKYCOF_CAP': '1096', 'SKYCOF_MM_GUARD': '26'}},
        {'id': 'cap1095', 'changes': {'SKYCOF_CAP': '1095'}},
        {'id': 'cap1095-guard26', 'changes': {'SKYCOF_CAP': '1095', 'SKYCOF_MM_GUARD': '26'}},
        {'id': 'decoder49', 'changes': {'SKYCOF_WDEC': '49'}},
        {'id': 'fold59', 'changes': {'SKYCOF_FOLDW': '59'}},
        {'id': 'decoder49-fold59', 'changes': {'SKYCOF_WDEC': '49', 'SKYCOF_FOLDW': '59'}},
    ]


def extract_verified(archive: Path, destination: Path, expected: dict[str, str]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    files = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            path = (root / member.name).resolve()
            if not path.is_relative_to(root) or Path(member.name).is_absolute():
                raise ValueError('Archive path escapes its root')
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile() or member.name in files:
                raise ValueError('Archive has a link, special file, or duplicate path')
            path.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as incoming, path.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            files[member.name] = digest(path)
    if files != expected:
        raise ValueError('Archive file hashes differ from the pinned manifest')


CLEANUP_RUST = r'''
    // The original source appends 48 adjacent unconditional X/X pairs.
    // X_q X_q = I on every quantum state, with no phase or classical effect.
    let start = ops.len().checked_sub(96).expect("missing identity tail");
    let mut depth: i64 = 0;
    for op in &ops[..start] {
        match op.kind {
            circuit::OperationType::PushCondition => depth += 1,
            circuit::OperationType::PopCondition => { depth -= 1; assert!(depth >= 0); },
            _ => {}
        }
    }
    assert_eq!(depth, 0, "tail must start outside all condition scopes");
    let references: Vec<&Op> = ops[start..].iter().collect();
    let mut targets = Vec::new();
    for pair in references.chunks_exact(2) {
        assert!(pair[0].q_target.0 <= 1, "unexpected nonce wire");
        let mut expected = Op::empty();
        expected.kind = circuit::OperationType::X;
        expected.q_target = pair[0].q_target;
        assert_eq!(*pair[0], expected);
        assert_eq!(*pair[1], expected);
        targets.push(expected.q_target.0);
    }
    assert_eq!(targets.len(), 48);
    if keep_original {
        write_ops(&ops, Path::new("ops-original.bin")).expect("write original ops");
    }
    ops.truncate(start);
    fs::write("cleanup-proof.json", format!(
        "{{\"rule\":\"48 adjacent unconditional X/X pairs equal identity\",\"removed_operations\":96,\"body_operations\":{},\"condition_depth\":0,\"pair_targets\":{:?},\"full_op_fields_checked\":true}}\n",
        start, targets)).expect("write cleanup proof");
'''


def instrument_driver(original: bytes) -> bytes:
    anchor = b'    let ops = point_add::build();'
    if original.count(anchor) != 1:
        raise ValueError('Original emitter anchor changed')
    replacement = ('    let keep_original = std::env::args().any(|v| v == "--original-and-clean");\n'
                   '    let mut ops = point_add::build();\n' + CLEANUP_RUST).encode()
    return original.replace(anchor, replacement)


RAIL_PROFILE_RUST = r'''
        let margin = super::pointadd::knob("SKYCOF_RAIL_MARGIN")
            .map(|value| value.parse::<usize>().expect("SKYCOF_RAIL_MARGIN: integer required"))
            .unwrap_or(0);
        assert!(margin <= 2, "SKYCOF_RAIL_MARGIN: require 0, 1, or 2");
        for width in &mut rail {
            *width = width.checked_add(margin).expect("rail width overflow");
        }
        eprintln!("SKYCOF_RAIL_PROFILE margin={} min={} max={}", margin,
                  rail.iter().min().unwrap(), rail.iter().max().unwrap());
'''


def instrument_rail_profile(original: bytes) -> bytes:
    """Widen signed rails through the existing captured source settings."""
    anchor = b'        Envelope { rail, cof, h }'
    if original.count(anchor) != 1:
        raise ValueError('Original rail envelope anchor changed')
    return original.replace(anchor, RAIL_PROFILE_RUST.encode() + anchor)


def _rss(pgid: int) -> int:
    text = subprocess.check_output(['/bin/ps', '-A', '-o', 'pgid=,rss='], text=True)
    return sum(int(parts[1]) * 1024 for line in text.splitlines()
               if len(parts := line.split()) == 2 and int(parts[0]) == pgid)


def run_process(command: list[str], cwd: Path, directory: Path,
                environment: dict[str, str], memory_bytes: int = 4 << 30) -> dict:
    """Keep a process receipt and enforce an aggregate memory bound."""
    directory.mkdir(parents=True, exist_ok=False)
    request = {'command': list(map(str, command)), 'cwd': str(cwd), 'environment': environment,
               'memory_limit_bytes': memory_bytes, 'wall_time_limit': None}
    save(directory / 'request.json', request)
    started = time.monotonic()
    peak = 0
    stopped = None
    monitor_error = None
    process = None
    rc = None
    with (directory / 'stdout.txt').open('w') as stdout, (directory / 'stderr.txt').open('w') as stderr:
        try:
            _rss(os.getpgrp())
        except BaseException as exc:
            monitor_error = exc
            stopped = 'monitor_exception'
        if monitor_error is None:
            process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=stdout,
                                       stderr=stderr, start_new_session=True)
            try:
                while process.poll() is None:
                    peak = max(peak, _rss(process.pid))
                    if peak > memory_bytes:
                        stopped = 'aggregate_memory_limit'
                        os.killpg(process.pid, signal.SIGKILL)
                        break
                    time.sleep(.5)
            except BaseException as exc:
                monitor_error = exc
                stopped = 'monitor_exception'
            finally:
                if monitor_error is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                rc = process.wait()
    receipt = {**request, 'returncode': rc, 'wall_seconds': time.monotonic() - started,
               'peak_sampled_rss_bytes': peak, 'resource_stop': stopped,
               'status': 'monitor_failed' if monitor_error else 'completed',
               'monitor_exception': None if monitor_error is None else {
                   'type': type(monitor_error).__name__, 'message': str(monitor_error)},
               'audits': 0, 'official_calls': 0}
    with (directory / 'receipt.json').open('x') as stream:
        stream.write(json.dumps(receipt, indent=2, allow_nan=False) + '\n')
    if monitor_error is not None:
        if isinstance(monitor_error, (KeyboardInterrupt, SystemExit)):
            raise monitor_error
        raise RuntimeError('Native process monitoring failed; inspect the preserved process receipt') from monitor_error
    return receipt


def _file_manifest(source: Path) -> dict[str, str]:
    return {str(path.relative_to(source)): digest(path)
            for path in sorted(source.rglob('*')) if path.is_file()}


def verify_builder(root: Path, manifest: dict | None = None, *, require_profile_control: bool = False) -> dict:
    """Verify the current source, binary, and optional profile control binding."""
    root = Path(root).resolve()
    manifest = manifest if manifest is not None else json.loads((root / 'builder.json').read_text())
    rail_profile = manifest.get('rail_profile') is True
    if require_profile_control:
        control_file = root / 'profile-control.json'
        if not control_file.is_file():
            raise ValueError('A reproduced zero-margin control is required before profile mutations')
        control = json.loads(control_file.read_text())
        expected = {'binary_sha256': manifest['binary_sha256'],
                    'builder_manifest_sha256': digest(root / 'builder.json'),
                    'ops_sha256': CLEAN_SHA256, 'original_ops_sha256': ORIGINAL_SHA256}
        if any(control.get(key) != value for key, value in expected.items()):
            raise ValueError('The zero-margin control binding differs')
        for field, expected_hash in [('ops', CLEAN_SHA256), ('original_ops', ORIGINAL_SHA256)]:
            path = (root / control[field]).resolve()
            if not path.is_relative_to(root):
                raise ValueError('The zero-margin control path escapes its root')
            verify_hash(path, expected_hash)
    if manifest.get('source_commit') != COMMIT or manifest.get('archive_sha256') != ARCHIVE_SHA256:
        raise ValueError('Pinned builder source identity differs')
    verify_hash(root / 'build_circuit.bin', manifest['binary_sha256'])
    if _file_manifest(root / 'source') != manifest['prepared_files']:
        raise ValueError('Prepared source hash drift')
    if rail_profile:
        proof = manifest.get('rail_profile_patch')
        if not isinstance(proof, dict) or proof.get('file') != 'src/point_add/skycof/walk.rs':
            raise ValueError('The compiled profile hook proof is absent')
        verify_hash(root / 'source' / proof['file'], proof['patched_sha256'])
        verify_hash(root / proof['patch_file'], proof['patch_sha256'])
    if manifest.get('precompile_source_sha256'):
        record = root / 'precompile-source.json'
        verify_hash(record, manifest['precompile_source_sha256'])
        if json.loads(record.read_text())['files'] != manifest['prepared_files']:
            raise ValueError('The precompile source binding differs')
    return manifest


def prepare(root: Path, snapshot: Path, rust_tools: Path, *, rail_profile: bool = False) -> dict:
    """Extract pinned sources and build one reusable offline emitter."""
    root, snapshot, rust_tools = root.resolve(), snapshot.resolve(), rust_tools.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'builder.json').exists():
        manifest = json.loads((root / 'builder.json').read_text())
        if bool(manifest.get('rail_profile', False)) != rail_profile:
            raise ValueError('The prepared builder profile differs from this request')
        return verify_builder(root, manifest)
    manifest_file = snapshot / 'snapshot/source-manifest.json'
    entries = [row for row in json.loads(manifest_file.read_text()) if row['commit'] == COMMIT]
    if len(entries) != 1 or entries[0]['sha256'] != ARCHIVE_SHA256:
        raise ValueError('Pinned source manifest identity differs')
    entry = entries[0]
    archive = snapshot / entry['archive']
    verify_hash(archive, ARCHIVE_SHA256)
    shutil.copy2(archive, root / 'source.tar.gz')
    save(root / 'source-entry.json', {'manifest_sha256': digest(manifest_file), 'entry': entry})
    source = root / 'source'
    extract_verified(archive, source, entry['file_sha256'])
    prepared_files = dict(entry['file_sha256'])
    for item in entry.get('supplemental_files', []):
        prepared_files[item['original_path']] = item['sha256']
        incoming = snapshot / item['path']
        verify_hash(incoming, item['sha256'])
        destination = (source / item['original_path']).resolve()
        if not destination.is_relative_to(source.resolve()):
            raise ValueError('Supplement escapes source root')
        if destination.exists():
            verify_hash(destination, item['sha256'])
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(incoming, destination)
    if not (source / 'NOTICE').is_file():
        raise ValueError('The pinned attribution notice is required')
    driver = source / 'src/bin/build_circuit.rs'
    original = driver.read_bytes()
    if hashlib.sha256(original).hexdigest() != entry['file_sha256']['src/bin/build_circuit.rs']:
        raise ValueError('Original driver source drift')
    (root / 'original-build_circuit.rs').write_bytes(original)
    changed_driver = instrument_driver(original)
    driver.write_bytes(changed_driver)
    prepared_files['src/bin/build_circuit.rs'] = hashlib.sha256(changed_driver).hexdigest()
    profile_patch = None
    if rail_profile:
        walk_file = source / 'src/point_add/skycof/walk.rs'
        original_walk = walk_file.read_bytes()
        if hashlib.sha256(original_walk).hexdigest() != entry['file_sha256']['src/point_add/skycof/walk.rs']:
            raise ValueError('Original rail source drift')
        changed_walk = instrument_rail_profile(original_walk)
        walk_file.write_bytes(changed_walk)
        prepared_files['src/point_add/skycof/walk.rs'] = hashlib.sha256(changed_walk).hexdigest()
        patch = ''.join(difflib.unified_diff(original_walk.decode().splitlines(True),
                                            changed_walk.decode().splitlines(True),
                                            fromfile='a/src/point_add/skycof/walk.rs',
                                            tofile='b/src/point_add/skycof/walk.rs'))
        (root / 'rail-profile.patch').write_text(patch)
        profile_patch = {'file': 'src/point_add/skycof/walk.rs',
                         'original_sha256': hashlib.sha256(original_walk).hexdigest(),
                         'patched_sha256': hashlib.sha256(changed_walk).hexdigest(),
                         'patch_file': 'rail-profile.patch',
                         'patch_sha256': digest(root / 'rail-profile.patch')}
    chain = rust_tools / 'rustup/toolchains/1.93.0-aarch64-apple-darwin/bin'
    environment = {'PATH': str(chain) + ':/usr/bin:/bin', 'CARGO_HOME': str(rust_tools / 'cargo'),
                   'RUSTUP_HOME': str(rust_tools / 'rustup'), 'RUSTC': str(chain / 'rustc'),
                   'CARGO_TARGET_DIR': str(root / 'target'), 'CARGO_NET_OFFLINE': 'true',
                   'TMPDIR': str(root)}
    save(root / 'precompile-source.json', {'source_commit': COMMIT,
                                           'archive_sha256': ARCHIVE_SHA256,
                                           'files': prepared_files})

    def require_source_binding(stage, build_receipt=None):
        actual = _file_manifest(source)
        if actual != prepared_files:
            changed = sorted(name for name in prepared_files.keys() | actual.keys()
                             if prepared_files.get(name) != actual.get(name))
            failure = {'status': 'source_changed', 'stage': stage, 'changed_files': changed,
                       'expected_files': prepared_files, 'actual_files': actual,
                       'build_receipt': build_receipt}
            with (root / 'source-binding-failure.json').open('x') as stream:
                stream.write(json.dumps(failure, indent=2, allow_nan=False) + '\n')
            raise ValueError('Prepared source changed ' + stage)

    # All campaign adapters share this advisory compiler lock.
    lock_path = root.parent / 'native-compiler.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        while True:
            commands = subprocess.check_output(['/bin/ps', '-A', '-o', 'comm='], text=True).splitlines()
            if not any(Path(name.strip()).name in ('cargo', 'rustc') for name in commands):
                break
            time.sleep(1)
        require_source_binding('before compilation')
        receipt = run_process([str(chain / 'cargo'), 'build', '--release', '--locked', '--offline',
                               '--jobs', '1', '--bin', 'build_circuit'], source, root / 'compilation', environment)
        require_source_binding('during compilation', receipt)
    if receipt['returncode']:
        raise RuntimeError('Offline builder compilation failed; inspect compilation/receipt.json')
    binary = root / 'build_circuit.bin'
    shutil.copy2(root / 'target/release/build_circuit', binary)
    manifest = {'source_commit': COMMIT, 'archive_sha256': ARCHIVE_SHA256,
                'source_manifest_sha256': digest(manifest_file), 'binary_sha256': digest(binary),
                'driver_original_sha256': hashlib.sha256(original).hexdigest(),
                'driver_modified_sha256': prepared_files['src/bin/build_circuit.rs'],
                'prepared_files': prepared_files, 'precompile_source_sha256': digest(root / 'precompile-source.json'),
                'compiler_sha256': digest(chain / 'rustc'), 'build_receipt': 'compilation/receipt.json',
                'active_product_minimum_room': 90, 'rounds_fixed': 404,
                'arithmetic_changes': [] if not rail_profile else ['Global rail width margin in skycof/walk.rs'],
                'rail_profile': rail_profile, 'rail_profile_patch': profile_patch,
                'cleanup': 'Checked 48 final unconditional X/X pairs in the emitter driver'}
    save(root / 'builder.json', manifest)
    return manifest


@contextmanager
def emission_slot(root: Path):
    """Serialize full native emissions across the campaign adapters."""
    with (root.parent / 'native-emission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def emit(root: Path, recipe_id: str, changes: dict, keep_original: bool = False) -> dict:
    """Emit a recipe once and retain either its operation hash or rejection."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', recipe_id):
        raise ValueError('Recipe id requires lowercase letters, digits, and hyphens')
    root = root.resolve()
    manifest = json.loads((root / 'builder.json').read_text())
    rail_profile = manifest.get('rail_profile') is True
    environment = recipe_environment(changes, rail_profile=rail_profile)
    manifest = verify_builder(root, manifest, require_profile_control=(
        rail_profile and environment.get('SKYCOF_RAIL_MARGIN', '0') != '0'))
    directory = root / 'recipes' / recipe_id
    directory.mkdir(parents=True, exist_ok=False)
    recipe = {'id': recipe_id, 'changes': changes, 'environment': environment,
              'source_commit': COMMIT, 'binary_sha256': manifest['binary_sha256']}
    save(directory / 'recipe.json', recipe)
    environment = {**environment, 'TMPDIR': str(directory)}
    command = [str(root / 'build_circuit.bin')]
    if keep_original:
        command.append('--original-and-clean')
    policy = '(version 1)(allow default)(deny network*)(deny file-write*)'
    policy += f'(allow file-write* (subpath {json.dumps(str(directory))}))'
    policy += '(allow file-write* (subpath "/dev"))'
    (directory / 'sandbox.sb').write_text(policy)
    with emission_slot(root):
        receipt = run_process(['/usr/bin/sandbox-exec', '-f', str(directory / 'sandbox.sb'), *command],
                              directory, directory / 'process', environment)
    result = {'id': recipe_id, 'source_commit': COMMIT, 'recipe': recipe,
              'status': 'constructor_rejected' if receipt['returncode'] else 'emitted',
              'process_receipt': str((directory / 'process/receipt.json').relative_to(root)),
              'constructor_stderr': str((directory / 'process/stderr.txt').relative_to(root)),
              'audits': 0, 'official_calls': 0, 'Q': None, 'T': None}
    if not receipt['returncode']:
        ops = directory / 'ops.bin'
        with ops.open('rb') as stream:
            header = stream.read(16)
        if header[:8] != b'QECCOPSZ':
            raise ValueError('Emitter operation framing differs')
        result.update(ops=str(ops.relative_to(root)), ops_sha256=digest(ops),
                      operations=int.from_bytes(header[8:16], 'little'), bytes=ops.stat().st_size,
                      cleanup_proof_sha256=digest(directory / 'cleanup-proof.json'))
        if keep_original:
            result['original_ops_sha256'] = digest(directory / 'ops-original.bin')
        if validate_recipe(changes, rail_profile=rail_profile) == DEFAULTS:
            result['clean_control_matches'] = result['ops_sha256'] == CLEAN_SHA256
            if keep_original:
                result['original_control_matches'] = result['original_ops_sha256'] == ORIGINAL_SHA256
            if not result['clean_control_matches'] or keep_original and not result['original_control_matches']:
                result['status'] = 'control_identity_mismatch'
            if rail_profile and keep_original and result['status'] == 'emitted':
                save(root / 'profile-control.json', {
                    'binary_sha256': manifest['binary_sha256'],
                    'builder_manifest_sha256': digest(root / 'builder.json'),
                    'ops': result['ops'], 'ops_sha256': result['ops_sha256'],
                    'original_ops': str((directory / 'ops-original.bin').relative_to(root)),
                    'original_ops_sha256': result['original_ops_sha256'],
                    'recipe_id': recipe_id})
    save(directory / 'result.json', result)
    return result


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == 'joint':
        from .targeted_joint import main as joint_main
        return joint_main(arguments[1:])
    parser = argparse.ArgumentParser(description=__doc__,
                                     epilog='Joint-array route: targeted-frontier joint --root ROOT ...')
    parser.add_argument('--root', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    preparation = sub.add_parser('prepare')
    preparation.add_argument('--snapshot', type=Path, required=True)
    preparation.add_argument('--rust-tools', type=Path, required=True)
    preparation.add_argument('--rail-profile', action='store_true', help='Build the guarded global rail-margin source variant')
    sub.add_parser('recipes')
    emission = sub.add_parser('emit')
    emission.add_argument('--id', required=True)
    emission.add_argument('--recipe', type=Path)
    emission.add_argument('--keep-original', action='store_true')
    sub.add_parser('batch')
    args = parser.parse_args(arguments)
    if args.action == 'prepare':
        result = prepare(args.root, args.snapshot, args.rust_tools, rail_profile=args.rail_profile)
        print(json.dumps({key: value for key, value in result.items() if key != 'prepared_files'}))
    elif args.action == 'recipes':
        print(json.dumps(first_recipes(), indent=2))
    elif args.action == 'emit':
        changes = json.loads(args.recipe.read_text()) if args.recipe else {}
        result = emit(args.root, args.id, changes, args.keep_original)
        print(json.dumps(result), flush=True)
        return 0 if result['status'] == 'emitted' else 1
    else:
        rows = []
        for recipe in first_recipes():
            existing = args.root / 'recipes' / recipe['id'] / 'result.json'
            if existing.exists():
                result = json.loads(existing.read_text())
                binding = verify_builder(args.root)
                environment = recipe_environment(recipe['changes'], rail_profile=binding.get('rail_profile') is True)
                if environment.get('SKYCOF_RAIL_MARGIN', '0') != '0':
                    verify_builder(args.root, binding, require_profile_control=True)
                if result['recipe']['environment'] != environment:
                    raise ValueError('Existing recipe differs from this batch')
                if result['recipe'].get('binary_sha256') != binding['binary_sha256']:
                    raise ValueError('Cached recipe binary differs from the current verified builder')
                if result.get('source_commit') != COMMIT or result['recipe'].get('source_commit') != COMMIT:
                    raise ValueError('Cached recipe source binding differs')
                if result.get('ops_sha256'):
                    path = (args.root / result['ops']).resolve()
                    if not path.is_relative_to(args.root.resolve()):
                        raise ValueError('Cached operation path escapes its root')
                    verify_hash(path, result['ops_sha256'])
            else:
                result = emit(args.root, recipe['id'], recipe['changes'], recipe['id'] == 'control')
            rows.append(result)
            save(args.root / 'batch.json', rows)
            print(json.dumps({key: result.get(key) for key in ['id', 'status', 'ops_sha256', 'operations']}), flush=True)
            if result['status'] == 'control_identity_mismatch':
                return 1
    return 0
