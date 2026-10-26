"""Extract the timing table from fixed protocols and completed measurements."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from scipy.stats import beta

HERE = Path(__file__).resolve().parent
WORKSHOP = HERE.parents[1]
protocol = json.loads((HERE / 'protocol.json').read_text())
rows = []
for request in protocol['requests']:
    path = HERE / request['id'] / 'result.json'
    if not path.exists():
        rows.append({'id': request['id'], 'name': request['name'], 'status': 'pending',
                     'pairs_requested': request['pairs'], 'streams': request['streams']})
        continue
    value = json.loads(path.read_text())
    if request['id'] == 'point_addition':
        raw = value['raw']
        k, n = raw['failed_inputs'], raw['pairs']
        times = raw['timings']
        row = {'Q': raw['qubits'], 'T': raw['mean_toffoli'], 'failed_inputs': k,
               'failed_shots': raw['failed_shots'], 'pairs': n,
               'construction_wall_seconds': value['construction']['wall_seconds'],
               'construction_cpu_seconds': value['construction']['cpu_seconds'],
               'evaluation_wall_seconds': value['evaluation']['wall_seconds'],
               'evaluation_cpu_seconds': value['evaluation']['cpu_seconds'],
               'simulation_wall_seconds': times['simulation_seconds'],
               'load_wall_seconds': times['load_seconds'],
               'preparation_wall_seconds': times['preparation_seconds'],
               'scope': 'Frozen native generator and native checker. Simulation time excludes load and input preparation.',
               'input_sha256': raw['packet_sha256']}
    else:
        k, n = value['failures']['failed_inputs'], value['pairs']
        row = {'Q': value['Q'], 'T': value['T'], 'failed_inputs': k,
               'failed_shots': value['failures']['failed_shots'], 'pairs': n,
               'construction_wall_seconds': value['construction']['wall_seconds'],
               'construction_cpu_seconds': value['construction']['cpu_seconds'],
               'evaluation_wall_seconds': value['evaluation']['wall_seconds'],
               'evaluation_cpu_seconds': value['evaluation']['cpu_seconds'],
               'simulation_wall_seconds': None,
               'input_preparation_wall_seconds': value['input_preparation_wall_seconds'],
               'outcome_archival_wall_seconds': value['outcome_archival_wall_seconds'],
               'evaluation_pipeline_wall_seconds': value['evaluation_pipeline_wall_seconds'],
               'scope': value['evaluation']['scope'], 'input_sha256': value['input_sha256']}
    bound = 1. if k == n else float(beta.isf(protocol['alpha_per_circuit'], k + 1, n - k))
    row.update({'id': request['id'], 'name': request['name'], 'status': value['status'],
                'streams': request['streams'], 'batch_size': request['batch_size'], 'threads': request['threads'],
                'operation_sha256': value['operation_sha256'], 'label': request['label'],
                'alpha': protocol['alpha_per_circuit'], 'upper_bound': bound,
                'epsilon': protocol['epsilon'], 'qualified': bound <= protocol['epsilon'],
                'result_file': str(path.relative_to(WORKSHOP)),
                'result_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    start_record = json.loads((path.parent / 'started.json').read_text())
    row['environment_at_start'] = start_record['environment']
    row['environment_after'] = value['environment_after']
    row['batch_size_unit'] = ('input cases in one native stream task' if request['id'] == 'point_addition'
                              else 'input pairs in one two-stream Python call')
    assert row['operation_sha256'] == request['expected_operation_sha256']
    assert n == 100000
    rows.append(row)
out = {'study': '100,000-input timing study of recorded circuits',
       'updated_utc': datetime.now(timezone.utc).isoformat(),
       'protocol_sha256': hashlib.sha256((HERE/'protocol.json').read_bytes()).hexdigest(),
       'status': 'complete' if all(r['status'] == 'complete' for r in rows) else 'partial',
       'environment_at_freeze': protocol['environment_at_freeze'],
       'concurrency': protocol['concurrency'], 'rows': rows,
       'limitations': ['The two Python evaluation times include repeated construction and hashing. They are not simulation-only times.',
                       'The runs share a workstation with other applications and experiments. Wall times do not describe an isolated machine.',
                       'These are fixed representative circuits, rather than width-only scaling of one identical algorithm.']}
hardware = HERE / 'hardware.json'
if hardware.exists():
    out['hardware'] = json.loads(hardware.read_text())
verification = HERE / 'verification.json'
if verification.exists():
    out['verification'] = {'file': str(verification.relative_to(WORKSHOP)),
                           'sha256': hashlib.sha256(verification.read_bytes()).hexdigest(),
                           'scope': 'Recounted all 300000 inputs and 600000 shot verdicts; checked 71 frozen source files and all circuit bindings.'}
destination = WORKSHOP / 'data/timing-study.json'
destination.parent.mkdir(parents=True, exist_ok=True)
tmp = destination.with_suffix('.json.tmp')
tmp.write_text(json.dumps(out, indent=2) + '\n')
tmp.replace(destination)
print(json.dumps({'status': out['status'], 'rows': [{k: r.get(k) for k in ['id', 'status', 'pairs', 'evaluation_wall_seconds', 'failed_inputs']} for r in rows]}))
