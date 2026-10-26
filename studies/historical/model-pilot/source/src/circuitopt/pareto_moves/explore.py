"""Measure setting combinations with an exploration-only binary (from code/explore.py).

A grid is a list of {"name": ..., "settings": {NAME: VALUE, ...}}. Each entry overrides the
parent's settings through one environment variable (LEVERS_OVERRIDES), emits once, and runs
the development check. The results are development data for the choice of variants. They
never qualify a circuit. A chosen setting is written into source and rebuilt, and the rebuilt
bytes must match the exploration bytes.
"""
from pathlib import Path
import json
import re
import subprocess
import time

from .layout import load, require, sha, utc
from .levers import development_argv
from ..pass_chance import PUBLIC_SHOTS

OVERRIDE_VARIABLE = 'LEVERS_OVERRIDES'


def defaults(campaign, binary=None, build_receipt=None, out=None, runs=None):
    """Return the recorded exploration paths unless the caller names others."""
    return (Path(binary) if binary else campaign.build_scratch / 'lf-explore/target/release/build_circuit',
            Path(build_receipt) if build_receipt else campaign.root / 'tracks/froghop-levers/lf-explore/build.json',
            Path(out) if out else campaign.root / 'tracks/leapfrog-band/explore',
            Path(runs) if runs else campaign.build_scratch / 'lf-explore/runs')


def overrides_text(settings):
    return ';'.join(f'{k}={v}' for k, v in settings.items())


def measure(campaign, entry, build_sha, *, binary, out, runs, execute=subprocess.run):
    """Emit and check one grid entry. An existing record is returned unchanged."""
    name = entry['name']
    require(re.fullmatch(r'[A-Za-z0-9._-]+', name or '') is not None, f'Use a plain grid entry name: {name!r}')
    target = out / f'{name}.json'
    if target.exists():
        return load(target)
    work = runs / name
    work.mkdir(parents=True)
    with campaign.lock('native-emission.lock'):
        started = time.monotonic()
        done = execute([str(binary)], cwd=work, capture_output=True, text=True,
                       env={'PATH': campaign.child_path, OVERRIDE_VARIABLE: overrides_text(entry['settings'])})
        wall = time.monotonic() - started
    ops = work / 'ops.bin'
    record = {'name': name, 'settings': entry['settings'], 'binary_sha256': build_sha, 'emit_returncode': done.returncode,
              'emit_seconds': wall, 'measured_utc': utc()}
    panic = re.search(r"panicked at ([^\n]+)\n([^\n]+)", done.stderr)
    if done.returncode or not ops.is_file():
        record['status'] = 'construction_failed'
        record['panic'] = panic.group(0)[:600] if panic else done.stderr[-600:]
    else:
        record['ops_sha256'] = sha(ops)
        record['ops_location'] = str(ops)
        dev = work / 'development'
        dev.mkdir()
        check = execute(development_argv(campaign.checker, ops, dev, pairs=campaign.dev_pairs, streams=campaign.streams,
                                         threads=campaign.threads, label=campaign.dev_label),
                        capture_output=True, text=True, env={'PATH': campaign.child_path, 'TMPDIR': str(dev)})
        raw = load(dev / 'raw.json')
        (dev / 'per-input.jsonl').unlink()
        shots = campaign.dev_pairs * campaign.streams
        record.update(status='measured', Q=raw['qubits'], T=raw['mean_toffoli'], failed_inputs=raw['failed_inputs'],
                      failed_shots=raw['failed_shots'], failed_indices=raw['failed_indices'],
                      lambda_estimate=PUBLIC_SHOTS * raw['failed_shots'] / shots, checker_returncode=check.returncode)
    out.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2) + '\n')
    return record


def run_grid(campaign, grid, *, binary=None, build_receipt=None, out=None, runs=None, execute=subprocess.run):
    """Measure every grid entry with the binary named by its build receipt."""
    binary, build_receipt, out, runs = defaults(campaign, binary, build_receipt, out, runs)
    build = load(build_receipt)
    require(sha(binary) == build['binary_sha256'], 'The exploration binary differs from its build receipt.')
    entries = load(grid) if isinstance(grid, (str, Path)) else grid
    results = []
    for entry in entries:
        r = measure(campaign, entry, build['binary_sha256'], binary=binary, out=out, runs=runs, execute=execute)
        results.append({k: r.get(k) for k in ('name', 'status', 'Q', 'T', 'failed_inputs', 'failed_shots', 'lambda_estimate')}
                       | ({'panic': r['panic'][:160]} if r.get('panic') else {}))
    return results
