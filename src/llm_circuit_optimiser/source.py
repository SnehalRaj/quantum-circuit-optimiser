"""Build an editable user family through the fixed benchmark interface."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

from .common import ROOT, digest, load, save, sha256
from .families import load_family
from . import large


def init_family(target_id, destination, *, family_id):
    """Create a source-backed derivative with its parent's notices and lineage."""
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,63}', family_id):
        raise ValueError('Use lowercase letters, digits, and hyphens for the family ID')
    target = large._target(target_id)
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Choose a new directory for the editable family')
    parent = ROOT / target['vendor_source']
    manifest = large._verify_vendor(parent)
    fixed = large._verify_vendor(ROOT / 'vendor/benchmark')
    destination.mkdir(parents=True)
    files = {}
    for name in manifest['files_sha256']:
        if name.startswith('src/point_add/'):
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(parent / name, path)
    notices = []
    for directory, record in [(ROOT / 'vendor/benchmark', fixed), (parent, manifest)]:
        for name in record['files_sha256']:
            if Path(name).name in {'NOTICE', 'LICENSE', 'LICENSE.md', 'LICENSE.txt'}:
                notices.append(f'Source: {directory.name}/{name}\n\n' + (directory / name).read_text())
    (destination / 'NOTICE').write_text('\n\n'.join(notices) + '\n')
    if target.get('historical_receipts'):
        parent_note = ROOT / target['historical_receipts'] / 'note.md'
        if parent_note.is_file():
            with (destination / 'NOTICE').open('a') as stream:
                stream.write('\n\nInherited research note and attribution\n\n')
                stream.write(parent_note.read_text())
    # Disable the inherited conditional setting. The wrapper supplies one chosen pass.
    module = destination / 'src/point_add/mod.rs'
    text = module.read_text()
    pattern = r'cond_pass::run_with\(ops, "[mfsur]*"\)'
    sites = list(re.finditer(pattern, text))
    if len(sites) > 1:
        raise ValueError('The template has ambiguous conditional settings')
    if sites:
        text = re.sub(pattern, 'cond_pass::run_with(ops, "")', text)
        module.write_text(text)
    dependency_ids = []
    if target.get('historical_receipts'):
        dependency_path = ROOT / target['historical_receipts'] / 'dependencies.json'
        if dependency_path.is_file():
            dependency_ids = [entry['submissionId'] for entry in load(dependency_path)]
    files = {str(p.relative_to(destination)): sha256(p) for p in sorted(destination.rglob('*')) if p.is_file()}
    family = {'schema_version': 1, 'id': family_id, 'name': family_id,
              'backend': 'source-conditional', 'source_dir': '.',
              'targets': [family_id], 'files_sha256': files,
              'provenance': {'description': f'Editable derivative of the pinned {target_id} constructor.',
                  'authors': ['Retain the parent credits in NOTICE and the source files'],
                  'upstream_family': target['family'], 'parent_target': target['id'],
                  'parent_commit': target['parent_commit'],
                  'dependency_ids': dependency_ids}}
    save(destination / 'family.json', family)
    load_family(destination / 'family.json')
    return destination / 'family.json'


def refresh_family(definition):
    """Freeze deliberate source edits before registration or search."""
    definition = Path(definition).resolve()
    raw = load(definition)
    if raw.get('backend') != 'source-conditional':
        raise ValueError('Only an editable source family has source pins to refresh')
    source_dir = (definition.parent / raw['source_dir']).resolve()
    files = [source_dir / 'NOTICE'] + sorted((source_dir / 'src/point_add').rglob('*'))
    pins = {}
    for path in files:
        if path.is_symlink():
            raise ValueError('Source families cannot contain symbolic links')
        if path.is_file():
            pins[str(path.relative_to(source_dir))] = sha256(path)
    raw['files_sha256'] = pins
    save(definition, raw)
    return load_family(definition)


def materialize(family, target_id, workdir, policy='mfsur', cancel_adjacent=False):
    family = load_family(family)
    if family['backend'] != 'source-conditional' or target_id not in family['targets']:
        raise ValueError('The target does not belong to this editable source family')
    if policy != 'none' and policy not in large.POLICIES:
        raise ValueError('The conditional policy is invalid')
    workdir = Path(workdir).resolve()
    source = workdir / 'source'
    receipt_path = workdir / 'materialization.json'
    recipe_hash = digest({'family': family, 'target': target_id, 'policy': policy,
                          'cancel_adjacent': cancel_adjacent,
                          'source_adapter_sha256': sha256(__file__),
                          'cancellation_wrapper': large.cancellation_body('ops') if cancel_adjacent else None})
    if receipt_path.exists():
        receipt = load(receipt_path)
        if receipt['recipe_sha256'] != recipe_hash or large._hashes(source) != receipt['source_hashes']:
            raise ValueError('The editable source or recipe changed after materialization')
        return receipt
    if source.exists():
        raise ValueError('The source directory lacks a frozen materialization receipt')
    fixed_dir = ROOT / 'vendor/benchmark'
    fixed = large._verify_vendor(fixed_dir)
    source.mkdir(parents=True)
    for name in fixed['files_sha256']:
        out = source / name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixed_dir / name, out)
    for name, expected in family['files_sha256'].items():
        out = source / name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(family['source_dir']) / name, out)
        if sha256(out) != expected:
            raise ValueError(f'The source changed during its copy: {name}')
    module = source / 'src/point_add/mod.rs'
    text = module.read_text()
    pattern = r'pub\s+fn\s+build\s*\(\s*\)\s*->\s*Vec\s*<\s*(?:crate::circuit::)?Op\s*>'
    if len(list(re.finditer(pattern, text))) != 1:
        raise ValueError('The constructor must expose exactly one pub fn build() -> Vec<Op>')
    text = re.sub(pattern, 'fn lco_source_build() -> Vec<crate::circuit::Op>', text)
    flags = '' if policy == 'none' else policy
    text += '\nmod lco_conditional_pass;\n'
    if cancel_adjacent:
        text += 'mod lco_adjacent_cancel;\n'
    text += 'pub fn build() -> Vec<crate::circuit::Op> {\n'
    text += f'    let ops = lco_conditional_pass::run_with(lco_source_build(), "{flags}");\n'
    if cancel_adjacent:
        text += large.cancellation_body('ops') + '\n}\n'
    else:
        text += '    ops\n}\n'
    module.write_text(text)
    conditional = ROOT / 'vendor/conditional/cond_pass.rs'
    if sha256(conditional) != 'a56f374ad86c7dd3ca13cfd08291f5f9a4f40a54e122959c481656a54b9d987e':
        raise ValueError('The shared conditional compiler changed')
    shutil.copyfile(conditional, source / 'src/point_add/lco_conditional_pass.rs')
    if cancel_adjacent:
        cancellation = ROOT / 'vendor/grammar_passes/adjacent_cancel.rs'
        if sha256(cancellation) != '5bd79f20646acd7b8dcf436fdd604d6a6546f32536e8aa2324d8100875ee328f':
            raise ValueError('The shared cancellation pass changed')
        shutil.copyfile(cancellation, source / 'src/point_add/lco_adjacent_cancel.rs')
    result = {'target_id': target_id, 'family': family['id'], 'policy': policy,
              'source_dir': str(source), 'source_hashes': large._hashes(source),
              'recipe_sha256': recipe_hash, 'provenance': family['provenance'],
              'cancel_adjacent': cancel_adjacent,
              'expected_ops_sha256': None, 'expected_Q': None, 'expected_T': None}
    save(receipt_path, result)
    return result


def build(family, target_id, workdir, policy='mfsur', offline=False, cancel_adjacent=False):
    recipe = materialize(family, target_id, workdir, policy, cancel_adjacent)
    workdir = Path(workdir).resolve()
    receipt_path = workdir / 'build.json'
    if receipt_path.exists():
        receipt = load(receipt_path)
        if receipt.get('returncode') == 0:
            for name in ['build_binary', 'eval_binary']:
                if sha256(receipt[name]) != receipt[name + '_sha256']:
                    raise ValueError('A saved editable-family binary changed')
            return {**recipe, **receipt}
        receipt_path.rename(workdir / ('build-failed-' + str(time.time_ns()) + '.json'))
    cargo, env, target_dir, cached = large._build_environment(workdir)
    command = [cargo, 'build', '--release', '--locked', '--jobs', '1', '--bin', 'build_circuit', '--bin', 'eval_circuit']
    if offline or cached:
        command.append('--offline')
    target_dir.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (target_dir / '.lco-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        start = time.monotonic()
        done = subprocess.run(command, cwd=recipe['source_dir'], env=env, capture_output=True, text=True)
        (workdir / 'build.stdout.txt').write_text(done.stdout)
        (workdir / 'build.stderr.txt').write_text(done.stderr)
        receipt = {'command': command, 'returncode': done.returncode, 'wall_seconds': time.monotonic()-start}
        if done.returncode == 0:
            (workdir / 'binaries').mkdir(exist_ok=True)
            for key, name in [('build_binary', 'build_circuit'), ('eval_binary', 'eval_circuit')]:
                path = workdir / 'binaries' / name
                shutil.copy2(target_dir / 'release' / name, path)
                receipt.update({key: str(path), key + '_sha256': sha256(path)})
        save(receipt_path, receipt)
        if done.returncode:
            raise RuntimeError(f'The editable family did not compile; see {workdir / "build.stderr.txt"}')
    return {**recipe, **receipt}


def emit(family, target_id, workdir, policy='mfsur', offline=False, cancel_adjacent=False):
    built = build(family, target_id, workdir, policy, offline, cancel_adjacent)
    workdir = Path(workdir).resolve()
    saved = workdir / 'emission.json'
    def cached():
        if not saved.exists():
            return None
        receipt = load(saved)
        if receipt.get('returncode') != 0 or not receipt.get('ops_sha256'):
            return None
        if sha256(receipt['ops_path']) != receipt['ops_sha256'] or receipt['emitter_binary_sha256'] != built['build_binary_sha256']:
            raise ValueError('The editable-family artifact changed')
        return {**built, **receipt, 'emission_reused': True}
    hit = cached()
    if hit is not None:
        return hit
    with large.emission_lock() as lock:
        hit = cached()
        if hit is not None:
            return hit
        return _emit_locked(built, workdir, saved, lock)


def _emit_locked(built, workdir, saved, lock):
    if saved.exists():
        saved.rename(workdir / ('failed-emission-' + str(time.time_ns()) + '.json'))
    folder = workdir / 'emission'
    if folder.exists():
        folder.rename(workdir / ('interrupted-emission-' + str(time.time_ns())))
    folder.mkdir()
    start = time.monotonic()
    done = subprocess.run([built['build_binary']], cwd=folder, env={'PATH': '/usr/bin:/bin'},
                          capture_output=True, text=True, pass_fds=(lock.fileno(),))
    (workdir / 'emission.stdout.txt').write_text(done.stdout)
    (workdir / 'emission.stderr.txt').write_text(done.stderr)
    ops = folder / 'ops.bin'
    receipt = {'returncode': done.returncode, 'wall_seconds': time.monotonic()-start,
               'ops_path': str(ops), 'ops_sha256': sha256(ops) if ops.is_file() else None,
               'emitter_binary_sha256': built['build_binary_sha256'],
               'rewrite_counts': large._rewrite_counts(done.stderr),
               'historical_hash_matches': None, 'emission_reused': False}
    save(saved, receipt)
    if done.returncode or not ops.is_file():
        raise RuntimeError(f'The editable-family emitter failed; see {workdir / "emission.stderr.txt"}')
    return {**built, **receipt}
