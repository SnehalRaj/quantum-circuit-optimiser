"""Freeze and time the three recorded circuits without model calls.

The division path calls the unchanged division_batch function. Its execution
time includes repeated emission, hashing, simulation, and output checks.
The separate count-only construction time must not be subtracted from it.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
WORKSHOP = HERE.parents[1]
RESUME = WORKSHOP.parents[3]
PILOT = RESUME / 'external-project/external-project/reversible-circuit-optimizer-openevolve-pilot'
PROTOCOL = HERE / 'protocol.json'
EXPECTED_FULL = '9271a9aa2b368b88989e905a932d5469baece5f3af00c0e928e8dcb7b9ce3c01'


def utc():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as out:
        json.dump(value, out, indent=2)
        out.write('\n')


def event(kind, **values):
    row = {'utc': utc(), 'event': kind, **values}
    with (HERE / 'events.jsonl').open('a') as out:
        out.write(json.dumps(row) + '\n')
    print(json.dumps(row), flush=True)


def environment():
    def sysctl(key):
        p = subprocess.run(['/usr/sbin/sysctl', '-n', key], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else {'unavailable': p.stderr.strip()}
    return {'utc': utc(), 'platform': platform.platform(), 'machine': platform.machine(),
            'python': sys.version, 'executable': sys.executable, 'cpu_count': os.cpu_count(),
            'load_average': list(os.getloadavg()), 'hardware_model': sysctl('hw.model'),
            'memory_bytes': sysctl('hw.memsize'), 'cpu_brand': sysctl('machdep.cpu.brand_string')}


def cpu():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def child_cpu():
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def freeze():
    if PROTOCOL.exists():
        raise SystemExit('Protocol already exists. Keep its original circuit and labels.')
    frozen = HERE / 'frozen-src'
    shutil.copytree(PILOT / 'src/circuitopt', frozen / 'circuitopt',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    integration = PILOT / 'runs/experiments/opus55-guarded-blocks-20261023'
    c16 = integration / 'final/circuits/299232bdb05678af786a04d059c3b7e2e3ac6c1601124e563222638c6c1d06e0/circuit.json'
    meta16 = json.loads(c16.read_text())
    fullroot = PILOT / 'runs/frontier-confirmation-20261024/members' / EXPECTED_FULL
    fullmeta = next(fullroot.glob('circuits/*/artifact.json'))
    full = json.loads(fullmeta.read_text())
    checker = fullroot / 'tools/study-check.bin'
    generator = PILOT / 'runs/frontier-exploitation-20261024/tools/full-point-add-generator.bin'
    for src, name in [(checker, 'study-check.bin'), (generator, 'full-point-add-generator.bin')]:
        shutil.copy2(src, HERE / name)
    if digest(HERE / 'full-point-add-generator.bin') != full['generator_sha256']:
        raise ValueError('Original generator binary hash differs from the selected circuit record')
    prefix = 'workshop-paper-20261025/timing-v1/' + os.urandom(16).hex()
    bp = {'ops': [{'op': 'swap', 'lowering': 'legacy'},
                  {'op': 'sub', 'lowering': 'direct'},
                  {'op': 'half', 'lowering': 'direct'}]}
    r256 = {'bits': 256, 'prime': 2**256 - 2**32 - 977, 'family': 'width',
            'steps': 511, 'cleanup': 'measured', 'block_program': bp,
            'widths': [min(256, max(1, 512 - 96 - t)) for t in range(512)]}
    requests = [
        {'id': 'division16', 'name': 'Guarded block division, 16 bits',
         'recipe': meta16['recipe'], 'expected_operation_sha256': meta16['operation_sha256'],
         'expected_Q': 130, 'expected_T': 6851, 'pairs': 100000, 'streams': 2,
         'batch_size': 16384, 'threads': 1, 'label': prefix + '/division16'},
        {'id': 'division256', 'name': 'Offset-96 division, 256 bits',
         'recipe': r256, 'expected_operation_sha256': '461cebb88cd90b0d70efc82b977e0c1d6caae97338c8773fada23b472bc360ca',
         'expected_Q': 2050, 'expected_T': 1633537, 'pairs': 100000, 'streams': 2,
         'batch_size': 16384, 'threads': 1, 'label': prefix + '/division256'},
        {'id': 'point_addition', 'name': 'Qualified settings of full secp256k1 point addition',
         'recipe': full['recipe'], 'expected_operation_sha256': EXPECTED_FULL,
         'expected_Q': 1270, 'expected_T': 793798, 'pairs': 100000, 'streams': 2,
         'batch_size': 64, 'threads': 4, 'label': prefix + '/point-addition',
         'source_artifact': str(fullmeta), 'generator': str(HERE / 'full-point-add-generator.bin'),
         'checker': str(HERE / 'study-check.bin')}
    ]
    hashes = {str(p.relative_to(HERE)): digest(p) for p in frozen.rglob('*') if p.is_file()}
    for p in [HERE / 'run_timing.py', HERE / 'study-check.bin', HERE / 'full-point-add-generator.bin']:
        hashes[str(p.relative_to(HERE))] = digest(p)
    value = {'frozen_utc': utc(), 'frozen_before_any_100k_run': True, 'requests': requests,
             'environment_at_freeze': environment(), 'source_sha256': hashes,
             'concurrency': 'Run the three requests sequentially. Native point addition uses four threads; division uses one.',
             'batch_choice': '16384 pairs per Python batch reduces repeated arithmetic construction relative to the earlier1024-pair audit. Chosen from existing implementation and timing records, before new outcomes.',
             'epsilon': .001, 'alpha_per_circuit': .05 / 3,
             'timing_scope': {'division_construction': 'One count-only emission plus canonical operation hashing; no simulation.',
                              'division_execution': 'Unchanged division_batch. Includes repeated emission, canonical hashing, bit-packed simulation and output checks. Never label simulation-only.',
                              'native_construction': 'Original frozen generator binary emits selected operation bytes.',
                              'native_execution': 'Frozen native checker; reports load, preparation, simulation and total wall time separately.'},
             'selection': 'Fixed recorded circuits. Preserve failures. No model calls, retries after failure, or selection by timing outcome.'}
    write_once(PROTOCOL, value)
    write_once(HERE / 'protocol.json.sha256', {'sha256': digest(PROTOCOL)})
    event('protocol_frozen', protocol_sha256=digest(PROTOCOL))


class CountHash:
    def __init__(self):
        self.h = hashlib.sha256()
        self.count = 0
    def append(self, op):
        self.h.update(canonical(op[:3]) + b'\n')
        self.count += 1


def run_division(request):
    from circuitopt.arithmetic.revsim import Circuit
    from circuitopt.division import build_recipe, division_batch, input_pairs, seed
    out = HERE / request['id']
    out.mkdir()
    write_once(out / 'started.json', {'utc': utc(), 'environment': environment(), 'request': request})
    recipe = request['recipe']
    start, proc = time.perf_counter(), cpu()
    circuit = Circuit(measured_uncompute=recipe['cleanup'] == 'measured')
    sink = CountHash()
    circuit.ops = sink
    xr, yr = circuit.alloc(recipe['bits']), circuit.alloc(recipe['bits'])
    outputs = build_recipe(circuit, xr, yr, recipe)
    construction = {'wall_seconds': time.perf_counter() - start, 'cpu_seconds': cpu() - proc,
                    'operation_count': sink.count, 'operation_sha256': sink.h.hexdigest(),
                    'inputs': [xr, yr], 'outputs': outputs, 'live_peak_Q': circuit.peak,
                    'scope': 'Count-only arithmetic emission and canonical stream hashing; no simulation'}
    write_once(out / 'construction.json', construction)
    if construction['operation_sha256'] != request['expected_operation_sha256']:
        raise ValueError('Count-only emission differs from the frozen target hash')
    event('construction_complete', circuit=request['id'], **{k: construction[k] for k in ['wall_seconds', 'cpu_seconds', 'operation_count']})
    totals = {'failed_inputs': 0, 'failed_shots': 0, 'channel_failed_inputs': {'output': 0, 'phase': 0, 'scratch': 0}}
    input_hash, all_examples = hashlib.sha256(), []
    input_time = output_time = eval_wall = eval_cpu = 0.
    total_t = 0
    overall, proc = time.perf_counter(), cpu()
    with gzip.open(out / 'inputs.jsonl.gz', 'xb') as inp, gzip.open(out / 'outcomes.jsonl.gz', 'xb') as observed:
        for offset in range(0, request['pairs'], request['batch_size']):
            n = min(request['batch_size'], request['pairs'] - offset)
            tic = time.perf_counter()
            xs, ys = input_pairs(int(recipe['prime']), request['label'], offset, n)
            ms = seed(f"{request['label']}/measurement/{offset}")
            row = canonical({'start': offset, 'x': xs, 'y': ys, 'streams': 2, 'measurement_seed': str(ms)}) + b'\n'
            inp.write(row); input_hash.update(row)
            input_time += time.perf_counter() - tic
            tic, t_cpu = time.perf_counter(), cpu()
            b = division_batch(recipe, xs, ys, ms)
            w, c = time.perf_counter() - tic, cpu() - t_cpu
            eval_wall += w; eval_cpu += c
            if b['operation_sha256'] != request['expected_operation_sha256']:
                raise ValueError('Timed division emission differs from the frozen target hash')
            failures = set().union(*b['channels'].values())
            totals['failed_inputs'] += len(failures)
            totals['failed_shots'] += len(b['bad_shots'])
            for k in totals['channel_failed_inputs']:
                totals['channel_failed_inputs'][k] += len(b['channels'][k])
            all_examples.extend({**v, 'index': offset + v['index']} for v in b['examples'])
            total_t += n * b['T']
            tic = time.perf_counter()
            for i in range(n):
                observed.write(canonical({'index': offset + i,
                                          'stream_failed': [2*i in b['bad_shots'], 2*i+1 in b['bad_shots']],
                                          'failed_channels': [k for k, v in b['channels'].items() if i in v]}) + b'\n')
            output_time += time.perf_counter() - tic
            event('batch_complete', circuit=request['id'], completed=offset+n, pairs=request['pairs'],
                  execution_wall_seconds=w, execution_cpu_seconds=c, failed_inputs=len(failures))
    result = {'id': request['id'], 'name': request['name'], 'completed_utc': utc(),
              'status': 'complete', 'pairs': request['pairs'], 'streams': 2, 'batch_size': request['batch_size'],
              'threads': 1, 'Q': b['Q'], 'T': total_t / request['pairs'], 'operation_count': b['operation_count'],
              'operation_sha256': b['operation_sha256'], 'label': request['label'], 'input_sha256': input_hash.hexdigest(),
              'construction': construction, 'evaluation': {'wall_seconds': eval_wall, 'cpu_seconds': eval_cpu,
                 'scope': 'Repeated construction, hashing, simulation and checking combined'},
              'input_preparation_wall_seconds': input_time, 'outcome_archival_wall_seconds': output_time,
              'evaluation_pipeline_wall_seconds': time.perf_counter()-overall,
              'evaluation_pipeline_cpu_seconds': cpu()-proc, 'failures': totals, 'first_failures_by_batch': all_examples,
              'environment_after': environment()}
    write_once(out / 'result.json', result)
    event('circuit_complete', circuit=request['id'], failed_inputs=totals['failed_inputs'], wall_seconds=result['evaluation_pipeline_wall_seconds'])


def run_child(command, directory, name):
    t, c = time.perf_counter(), child_cpu()
    env = {'PATH': '/usr/bin:/bin', 'TMPDIR': str(directory)}
    with (directory / (name+'.stdout.txt')).open('x') as out, (directory / (name+'.stderr.txt')).open('x') as err:
        p = subprocess.run(command, env=env, cwd=directory, stdout=out, stderr=err)
    row = {'command': command, 'environment': env, 'returncode': p.returncode,
           'wall_seconds': time.perf_counter()-t, 'cpu_seconds': child_cpu()-c}
    write_once(directory / (name+'.process.json'), row)
    return row


def run_native(request):
    out = HERE / request['id']
    out.mkdir()
    write_once(out / 'started.json', {'utc': utc(), 'environment': environment(), 'request': request})
    generated = out / 'ops.bin'
    command = [request['generator'], '--backend', request['recipe']['backend']]
    for k, v in sorted(request['recipe']['settings'].items()):
        command += ['--set', f'{k}={v}']
    command += ['--out', str(generated)]
    construction = run_child(command, out, 'construction')
    if construction['returncode'] or digest(generated) != request['expected_operation_sha256']:
        raise ValueError('Original native generator did not reproduce the frozen operation hash')
    event('construction_complete', circuit=request['id'], wall_seconds=construction['wall_seconds'], cpu_seconds=construction['cpu_seconds'])
    command = [request['checker'], '--ops', str(generated), '--pairs', str(request['pairs']), '--streams', '2',
               '--threads', str(request['threads']), '--label', request['label'], '--out', str(out/'raw.json'),
               '--per-input', str(out/'per-input.jsonl'), '--progress', str(out/'native-progress.json')]
    evaluation = run_child(command, out, 'evaluation')
    raw = json.loads((out/'raw.json').read_text())
    with (out/'per-input.jsonl').open('rb') as source, gzip.open(out/'per-input.jsonl.gz', 'xb') as dest:
        shutil.copyfileobj(source, dest)
    result = {'id': request['id'], 'name': request['name'], 'status': 'complete', 'completed_utc': utc(),
              'pairs': request['pairs'], 'streams': 2, 'threads': request['threads'], 'batch_size': request['batch_size'],
              'operation_sha256': digest(generated), 'label': request['label'], 'construction': construction,
              'evaluation': evaluation, 'raw': raw, 'environment_after': environment()}
    write_once(out/'result.json', result)
    event('circuit_complete', circuit=request['id'], wall_seconds=evaluation['wall_seconds'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['freeze', 'run'])
    parser.add_argument('--circuit', choices=['division16', 'division256', 'point_addition'])
    args = parser.parse_args()
    if args.action == 'freeze':
        freeze(); return
    protocol = json.loads(PROTOCOL.read_text())
    for rel, expected in protocol['source_sha256'].items():
        if digest(HERE / rel) != expected:
            raise ValueError(f'Frozen source changed: {rel}')
    if args.circuit is None:
        raise SystemExit('Choose a frozen circuit')
    request = next(r for r in protocol['requests'] if r['id'] == args.circuit)
    sys.path.insert(0, str(HERE / 'frozen-src'))
    try:
        if args.circuit == 'point_addition': run_native(request)
        else: run_division(request)
    except BaseException as error:
        event('circuit_error', circuit=args.circuit, type=type(error).__name__, message=str(error))
        raise


if __name__ == '__main__':
    main()
