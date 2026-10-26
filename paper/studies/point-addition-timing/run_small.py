"""Measure a fixed full 16-bit point-addition circuit once."""
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import os
import platform
import resource
import subprocess
import time
from datetime import datetime, timezone


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def timed(command, prefix):
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    with prefix.with_suffix('.stdout').open('x') as out, prefix.with_suffix('.stderr').open('x') as err:
        result = subprocess.run(command, stdout=out, stderr=err)
    wall = time.perf_counter() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    receipt = {'wall_seconds': wall,
               'cpu_seconds': after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
               'exit_code': result.returncode}
    write(prefix.with_suffix('.process.json'), receipt)
    if result.returncode not in (0, 1):
        raise RuntimeError(f'Instrument failed: {command[0]}')
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    emitter = args.tools.resolve() / 'small-emit'
    checker = args.tools.resolve() / 'circuit-check'
    build = json.loads(subprocess.check_output([str(emitter), '--build-info']))
    for key in ('small_emit', 'circuit_check', 'circuit_transform'):
        build.pop(key, None)
    label = 'paper-full-point-timing-20261026/' + os.urandom(16).hex()
    expected = '14c0c9fb60ec676458ccbcc5a836820e08ad5cd632acfac12fbf9700205d9c83'
    contract = {'utc': datetime.now(timezone.utc).isoformat(), 'revision': args.revision,
                'pairs': 100000, 'streams': 2, 'threads': 1, 'alpha': .05, 'epsilon': .001,
                'policy': 'mfsur', 'parent_policy': 'mfs', 'field_width': 16,
                'label': label, 'expected_ops_sha256': expected,
                'expected_Q': 214, 'expected_T': 28051, 'build': build,
                'emitter_sha256': digest(emitter), 'checker_sha256': digest(checker),
                'script_sha256': digest(__file__),
                'platform': platform.platform(), 'cpu_count': os.cpu_count(),
                'load_average': list(os.getloadavg()),
                'selection': 'One fixed policy, selected before timing; one run; preserve every failure.',
                'scope': 'Emission includes compiler passes, hashing and compressed output. Evaluation includes loading, inputs, simulation, checks and per-input output. Installation, compilation and post-run compression are excluded.'}
    write(out / 'contract.json', contract)
    ops = out / 'circuit.qops.zst'
    construction = timed([str(emitter), '--out', str(ops), '--policy', 'mfsur', '--parent-policy', 'mfs'], out / 'emission')
    assert construction['exit_code'] == 0 and digest(ops) == expected
    evaluation = timed([str(checker), '--width', '16', '--ops', str(ops), '--pairs', '100000',
                        '--label', label, '--out', str(out / 'audit.json'), '--streams', '2',
                        '--alpha', '.05', '--threads', '1'], out / 'evaluation')
    audit = json.loads((out / 'audit.json').read_text())
    assert audit['Q'] == 214 and audit['T'] == 28051
    assert audit['pairs'] == 100000 and audit['streams'] == 2
    assert audit['ops_sha256'] == expected
    assert digest(emitter) == contract['emitter_sha256']
    assert digest(checker) == contract['checker_sha256']
    per_input = Path(audit['per_input'])
    assert digest(per_input) == audit['per_input_sha256']
    with per_input.open('rb') as source, gzip.open(out / 'outcomes.jsonl.gz', 'wb') as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
    result = {'status': 'complete', 'id': 'point_addition16', 'name': 'Full 16-bit point addition',
              'Q': audit['Q'], 'T': audit['T'], 'failed_inputs': audit['failed_inputs'],
              'pairs': 100000, 'streams': 2, 'threads': 1,
              'construction_wall_seconds': construction['wall_seconds'],
              'construction_cpu_seconds': construction['cpu_seconds'],
              'evaluation_wall_seconds': evaluation['wall_seconds'],
              'evaluation_cpu_seconds': evaluation['cpu_seconds'],
              'alpha': .05, 'upper_bound': audit['upper_bound'], 'epsilon': .001,
              'qualified': audit['upper_bound'] <= .001,
              'operation_sha256': expected, 'input_sha256': audit['packet_sha256'],
              'per_input_sha256': audit['per_input_sha256'],
              'contract_sha256': digest(out / 'contract.json'), 'audit_sha256': digest(out / 'audit.json')}
    write(out / 'result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
