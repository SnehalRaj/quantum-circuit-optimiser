"""Reserve one audit slot and freeze its protocol before any run (from code/audit_slot.py).

The slot takes the next free number in the track's range. Slots in audits/registry.jsonl and
existing audits/slot-NN folders count as used. The label is fresh and random. The protocol,
with the label, is frozen and hashed before the controller runs. A frozen slot is spent,
whatever its outcome.
"""
from pathlib import Path
import json
import secrets
import shutil
import subprocess
import sys

from .layout import load, require, sha, utc

CONTROLLER = 'code/audit_frontier_reliability.py'


def write_hashed(path, value):
    """Write JSON once and its .sha256 companion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    (path.parent / (path.name + '.sha256')).write_text(sha(path) + '\n')


def used_slots(audits):
    used = {int(d.name.split('-')[1]) for d in audits.glob('slot-*') if d.is_dir()}
    registry = audits / 'registry.jsonl'
    if registry.exists():
        for line in registry.read_text().splitlines():
            if line.strip() and isinstance(json.loads(line).get('slot'), int):
                used.add(json.loads(line)['slot'])
    return used


def reserve(campaign, cid, ops, *, checker='tools/study-check.bin', slots=range(1, 21), track_label='A',
            pairs=20480, streams=2, threads=4, family_size=32, epsilon=0.001, timing_pairs=128,
            design_scope='Campaign audit chosen by Snehal (about 20,000 pairs). One slot of 32 at alpha 0.05/32.',
            confidence_scope=None, label_token=lambda: secrets.token_hex(16)):
    """Reserve the next free slot, copy the frozen controller, checker and ops, and freeze the protocol."""
    root = campaign.root
    audits = root / 'audits'
    ops = Path(ops)
    checker_path = Path(checker) if Path(checker).is_absolute() else root / checker
    require(ops.is_file(), f'The operation file is missing: {ops}')
    require(checker_path.is_file(), f'The checker is missing: {checker_path}')
    require((root / CONTROLLER).is_file(), f'The frozen controller is missing: {root / CONTROLLER}')
    if confidence_scope is None:
        confidence_scope = f'{campaign.name} reserves {family_size} audit slots at alpha=0.05/{family_size}.'
    with campaign.lock('audits/registry.lock'):
        used = used_slots(audits)
        free = [n for n in slots if n not in used]
        require(free, 'No free audit slot in the track range.')
        slot = free[0]
        folder = audits / f'slot-{slot:02d}'
        require(not folder.exists(), f'The slot folder already exists: {folder}')
        label = f'{campaign.name}/slot-{slot:02d}/{label_token()}'
        ops_target = folder / 'artifacts' / cid / 'ops.bin'
        ops_target.parent.mkdir(parents=True)
        shutil.copy2(ops, ops_target)
        (folder / 'code').mkdir()
        shutil.copy2(root / CONTROLLER, folder / CONTROLLER)
        (folder / 'tools').mkdir()
        shutil.copy2(checker_path, folder / 'tools/study-check.bin')
        member = {'id': cid, 'status': 'ready', 'ops': f'artifacts/{cid}/ops.bin', 'ops_sha256': sha(ops_target)}
        write_hashed(folder / 'selection.json', {'members': [member], 'track': track_label, 'slot': slot})
        write_hashed(folder / 'design.json', {'pairs': pairs, 'streams': streams, 'threads': threads,
                                              'family_size': family_size, 'scope': design_scope})
        protocol = {'schema': 1, 'frozen_utc': utc(),
                    'selection_file': 'selection.json', 'selection_sha256': sha(folder / 'selection.json'),
                    'sampling_amendment_file': 'design.json', 'sampling_amendment_sha256': sha(folder / 'design.json'),
                    'epsilon': epsilon, 'confidence': 0.95, 'family_size': family_size, 'pairs': pairs,
                    'streams': streams, 'threads': threads,
                    'label': label, 'timing_pairs': timing_pairs, 'timing_label': label + '/unused-timing',
                    'deadline_unix': None, 'deadline_utc': None,
                    'checker': {'path': 'tools/study-check.bin', 'sha256': sha(folder / 'tools/study-check.bin')},
                    'controller_sha256': sha(folder / CONTROLLER),
                    'members': [member], 'confidence_scope': confidence_scope}
        write_hashed(folder / 'protocol.json', protocol)
        entry = {'slot': slot, 'track': track_label, 'candidate': cid, 'ops_sha256': member['ops_sha256'],
                 'protocol_sha256': sha(folder / 'protocol.json'), 'label': label, 'frozen_utc': protocol['frozen_utc']}
        with (audits / 'registry.jsonl').open('a') as stream:
            stream.write(json.dumps(entry) + '\n')
    return {'slot': slot, 'candidate': cid, 'folder': str(folder.relative_to(root)), 'label': label}


def registry_entry(campaign, slot):
    registry = campaign.root / 'audits/registry.jsonl'
    require(registry.exists(), 'The audit registry is missing.')
    entries = [json.loads(line) for line in registry.read_text().splitlines() if line.strip()]
    found = [e for e in entries if e.get('slot') == slot]
    require(len(found) == 1, f'The registry holds {len(found)} entries for slot {slot}.')
    return found[0]


def run_slot(campaign, slot, *, stage='calibration', python=sys.executable, execute=subprocess.run):
    """Run the slot's frozen controller copy after the protocol matches its frozen hash and registry entry."""
    folder = campaign.root / 'audits' / f'slot-{int(slot):02d}'
    protocol_path = folder / 'protocol.json'
    require(protocol_path.is_file(), 'Freeze the protocol before the run.')
    recorded = (folder / 'protocol.json.sha256').read_text().split()[0]
    require(sha(protocol_path) == recorded, 'The protocol differs from its frozen hash.')
    require(registry_entry(campaign, int(slot))['protocol_sha256'] == recorded,
            'The protocol differs from its registry entry.')
    protocol = load(protocol_path)
    require(sha(folder / CONTROLLER) == protocol['controller_sha256'], 'The controller differs from the protocol.')
    argv = [str(python), str(folder / CONTROLLER), str(folder), '--stage', stage]
    done = execute(argv, cwd=folder)
    return {'slot': int(slot), 'argv': argv, 'returncode': done.returncode}
