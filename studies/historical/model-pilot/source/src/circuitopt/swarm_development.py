"""Location-aware evolution of saved, measured modular arithmetic cells.

These cells expose arithmetic mechanisms. They do not implement point addition.
The simulator and the arithmetic kernels stay unchanged.
"""
from collections import Counter
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import random
import time

from . import block_program
from .arithmetic import arith as A
from .arithmetic.revsim import Circuit, simulate

PRIMES = {16: 65521, 256: 2**256 - 2**32 - 977}
SHAPES = {
    'payload': ['swap', 'sub', 'half'],
    'stationary_window': ['swap', 'sub', 'half', 'sub', 'half', 'sub', 'half'],
}
SCOPE = ('Real emitted modular arithmetic cells with bounded development inputs. '
         'No full point-addition integration or rare-failure qualification.')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def baseline(shape, lowering='legacy', cleanup='toffoli'):
    return {'ops': [{'op': op, 'lowering': lowering, 'cleanup': cleanup}
                    for op in SHAPES[shape]], 'cache_masks': False}


def validate(program):
    if not isinstance(program, dict) or set(program) != {'ops', 'cache_masks'}:
        raise ValueError('A cell needs ordered ops and the mask-cache flag')
    if type(program['cache_masks']) is not bool or not 1 <= len(program['ops']) <= 16:
        raise ValueError('Invalid cell size or mask-cache flag')
    for item in program['ops']:
        if set(item) != {'op', 'lowering', 'cleanup'}:
            raise ValueError('Invalid location fields')
        block_program.validate({'ops': [{'op': item['op'], 'lowering': item['lowering']}]})
        if item['cleanup'] not in ('toffoli', 'measured'):
            raise ValueError('Invalid location cleanup')
    return copy.deepcopy(program)


def mask_groups(program):
    """Find repeated masked updates before any operation can change U."""
    groups, current = [], []
    for i, item in enumerate(program['ops']):
        if item['op'] in ('swap', 'half_u', 'double_u'):
            if len(current) > 1:
                groups.append(current)
            current = []
        elif item['op'] in ('sub', 'add'):
            current.append(i)
    if len(current) > 1:
        groups.append(current)
    return groups


@contextmanager
def location(circuit, item):
    previous = circuit.mbu
    circuit.mbu = item['cleanup'] == 'measured'
    try:
        with block_program.lowering(circuit, item['lowering']):
            yield
    finally:
        circuit.mbu = previous


def emit_cell(bits, prime, program):
    """Emit the ordered cell and return its public register map."""
    program = validate(program)
    circuit = Circuit()
    u, v = circuit.alloc(bits), circuit.alloc(bits)
    b, s = circuit.alloc1(), circuit.alloc1()
    original_u, original_v = list(u), list(v)
    groups = mask_groups(program) if program['cache_masks'] else []
    start = {g[0]: g for g in groups}
    finish = {g[-1]: g for g in groups}
    cached, cache_controls = None, None
    for i, item in enumerate(program['ops']):
        with circuit.block('location/' + str(i) + '/' + item['op']):
            with location(circuit, item):
                if i in start:
                    cache_controls = list(u)
                    cached = [circuit.and_new(b, q) for q in u]
                op = item['op']
                if op == 'swap':
                    if cached is not None:
                        raise ValueError('A retained mask cannot cross a U write')
                    A.cswap(circuit, s, u, v)
                elif op in ('sub', 'add'):
                    if cached is None:
                        operation = A.ctrl_mod_sub if op == 'sub' else A.ctrl_mod_add
                        operation(circuit, b, u, v, prime)
                    else:
                        operation = A.mod_sub if op == 'sub' else A.mod_add
                        operation(circuit, cached, v, prime)
                elif op in ('half', 'double'):
                    operation = A.mod_half if op == 'half' else A.mod_double
                    v = operation(circuit, v, prime)
                else:
                    if cached is not None:
                        raise ValueError('A retained mask cannot cross a U write')
                    operation = A.mod_half if op == 'half_u' else A.mod_double
                    u = operation(circuit, u, prime)
                if i in finish:
                    # U and b have stayed unchanged throughout this mask group.
                    for q, target in zip(cache_controls, cached):
                        circuit.and_erase(b, q, target)
                    cached, cache_controls = None, None
    if cached is not None:
        raise ValueError('Unclosed mask lifetime')
    block_program._restore_layout(circuit, u, original_u)
    return circuit, {'inputs': [original_u, original_v, [b], [s]],
                     'outputs': [original_u, v, [b], [s]]}


def arithmetic_reference(shape, prime, samples):
    out = []
    half = pow(2, -1, prime)
    for u, v, b, s in samples:
        for op in SHAPES[shape]:
            if op == 'swap' and s:
                u, v = v, u
            elif op == 'sub':
                v = (v - b*u) % prime
            elif op == 'half':
                v = v*half % prime
        out.append([u, v, b, s])
    return out


def frozen_samples(bits, count, label):
    """Freeze boundary cases, controls, and random residues before emission."""
    p = PRIMES[bits]
    rng = random.Random(int(digest([label, bits, 'inputs']), 16))
    boundary = [0, 1, 2, p//2, p//2 + 1, p-2, p-1]
    samples = [[u, v, b, s] for u, v in zip(boundary, reversed(boundary))
               for b in (0, 1) for s in (0, 1)]
    while len(samples) < count:
        samples.append([rng.randrange(p), rng.randrange(p), rng.randrange(2), rng.randrange(2)])
    return samples[:count]


def check_circuit(circuit, registers, samples, expected, streams=(0, 29)):
    """Keep output, phase, and freed-scratch checks in every evaluation."""
    inputs = [(reg, [row[i] for row in samples])
              for i, reg in enumerate(registers['inputs'])]
    channels = {k: set() for k in ('output', 'phase', 'scratch')}
    receipts = []
    for seed in streams:
        result = simulate(circuit, inputs, len(samples), seed=seed)
        values = [result.read(reg) for reg in registers['outputs']]
        public = set(q for reg in registers['outputs'] for q in reg)
        scratch = result.dirty_mask
        for q, value in result.state.items():
            if q not in public:
                scratch |= value
        for j, reference in enumerate(expected):
            if any(values[i][j] != reference[i] for i in range(4)):
                channels['output'].add(j)
            if (result.phase >> j) & 1:
                channels['phase'].add(j)
            if (scratch >> j) & 1:
                channels['scratch'].add(j)
        receipts.append({'measurement_seed': seed, 'T': result.toffoli_mean,
                         'phase_mask': hex(result.phase), 'scratch_mask': hex(scratch)})
    failed = set().union(*channels.values())
    t = sum(row['T'] for row in receipts)/len(receipts)
    return {'passed': not failed, 'failed_inputs': len(failed), 'pairs': len(samples),
            'streams': len(streams), 'channel_failed_inputs': {k: len(v) for k, v in channels.items()},
            'stream_receipts': receipts, 'Q': circuit.next_id, 'live_peak_Q': circuit.peak,
            'T': t, 'S': circuit.next_id * int(t + .5)}


def save_circuit(directory, circuit, registers, program):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    operation_hash = hashlib.sha256()
    with (directory/'gates.jsonl.gz').open('xb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
            for operation in circuit.ops:
                stream.write((canonical(operation) + '\n').encode())
                operation_hash.update((canonical(operation[:3]) + '\n').encode())
    meta = {'registers': registers, 'program': program, 'operation_count': len(circuit.ops),
            'operation_sha256': operation_hash.hexdigest(),
            'file_sha256': hashlib.sha256((directory/'gates.jsonl.gz').read_bytes()).hexdigest()}
    save(directory/'circuit.json', meta)
    return meta


def proposals(program):
    """Each proposal names a viable location and a precise ordered edit."""
    program = validate(program)
    out = []
    for i, item in enumerate(program['ops']):
        if item['op'] != 'swap':
            for field, values in [('lowering', ('legacy', 'direct')),
                                  ('cleanup', ('toffoli', 'measured'))]:
                child = copy.deepcopy(program)
                value = values[1] if item[field] == values[0] else values[0]
                child['ops'][i][field] = value
                out.append((child, {'rule': field, 'location': i, 'before': item[field], 'after': value}))
    if mask_groups(program):
        child = copy.deepcopy(program)
        child['cache_masks'] = not program['cache_masks']
        out.append((child, {'rule': 'delayed_mask_cleanup', 'locations': mask_groups(program),
                            'before': program['cache_masks'], 'after': child['cache_masks']}))
    ops = program['ops']
    for i in range(len(ops)-1):
        if [op['op'] for op in ops[i:i+2]] == ['sub', 'half'] and len(ops)+2 <= 16:
            replacement = [dict(ops[i], op=op) for op in ('half_u', 'half', 'sub', 'double_u')]
            child = {'ops': copy.deepcopy(ops[:i]) + replacement + copy.deepcopy(ops[i+2:]),
                     'cache_masks': program['cache_masks']}
            out.append((child, {'rule': 'linear_expansion', 'location': i,
                               'before': copy.deepcopy(ops[i:i+2]), 'after': replacement}))
    if len(ops)+2 <= 16:
        for i in range(1, len(ops)+1):
            replacement = [dict(ops[min(i, len(ops)-1)], op=op) for op in ('half', 'double')]
            child = {'ops': copy.deepcopy(ops[:i]) + replacement + copy.deepcopy(ops[i:]),
                     'cache_masks': program['cache_masks']}
            out.append((child, {'rule': 'inverse_pair', 'location': i, 'after': replacement}))
    return out


def structural_key(program):
    return canonical([[op['op'] for op in program['ops']], program['cache_masks']])


def select_parent(rows, rng, policy='structural'):
    """Retain cheap candidates and the best member of each arithmetic shape."""
    valid = [row for row in rows if row['receipt']['passed']]
    best = sorted(valid, key=lambda row: (row['receipt']['S'], row['id']))[:8]
    structural = {}
    for row in sorted(valid, key=lambda row: (row['receipt']['S'], row['id'])):
        structural.setdefault(structural_key(row['program']), row)
    if policy not in ('structural', 'greedy'):
        raise ValueError('Unknown selection policy')
    pool = best if policy == 'greedy' or rng.random() < .5 else list(structural.values())
    return rng.choice(pool)


def evaluate_program(directory, bits, shape, program, samples, expected):
    started, started_utc = time.monotonic(), now()
    circuit, registers = emit_cell(bits, PRIMES[bits], program)
    meta = save_circuit(Path(directory)/'circuit', circuit, registers, program)
    receipt = check_circuit(circuit, registers, samples, expected)
    receipt.update(meta, started_utc=started_utc, completed_utc=now(),
                   wall_seconds=time.monotonic()-started, scope=SCOPE)
    save(Path(directory)/'receipt.json', receipt)
    return receipt


def run_case(root, bits, shape, trials=64, pairs=256, seed=0, deadline=None, policy='structural'):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    samples = frozen_samples(bits, pairs, 'grammar-swarm-development-20261023-v1')
    streams = [0, 29]
    inputs = {'bits': bits, 'prime': PRIMES[bits], 'samples': samples,
              'measurement_seeds': streams, 'reference_shape': SHAPES[shape]}
    save(root/'inputs.json', inputs)
    expected = arithmetic_reference(shape, PRIMES[bits], samples)
    save(root/'expected.json', expected)
    source_files = [Path(__file__), Path(block_program.__file__), Path(A.__file__),
                    Path(__file__).parent/'arithmetic/revsim.py',
                    Path(__file__).parent/'arithmetic/constant_kernel.py']
    source_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    for source in source_files:
        target = root/'source'/source.relative_to(Path(__file__).parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    plan = {'question': 'Do location edits and retained mask lifetimes beat global arithmetic recipes?',
            'invariant': 'Preserve the reference modular map, all controls, all freed scratch, and phase.',
            'prediction': 'Direct lowerings reduce Q and T. A retained mask avoids repeated mask creation.',
            'predicted_mask_T_saving_measured': (len(SHAPES[shape])//2-1)*bits if shape == 'stationary_window' else 0,
            'cheapest_falsifier': 'Any output, phase, or scratch failure on these frozen development inputs.',
            'acceptance': 'Zero observed failures and lower S than every global recipe control.',
            'bits': bits, 'prime': PRIMES[bits], 'shape': shape, 'trials': trials, 'pairs': pairs,
            'seed': seed, 'scope': SCOPE, 'started_utc': now(), 'input_sha256': digest(inputs),
            'source_sha256': source_hashes,
            'selection_policy': policy,
            'selection': ('Half of parent draws use the cost elite; half use the best member of each operation shape.'
                          if policy == 'structural' else 'Every parent draw uses the eight cheapest valid candidates.'),
            'rules': ['lowering', 'cleanup', 'delayed_mask_cleanup', 'linear_expansion', 'inverse_pair'],
            'deadline_utc': deadline}
    save(root/'plan.json', plan)
    rows, seen, counts, curves = [], set(), Counter(), []
    rng = random.Random(seed)

    def evaluate(program, parent, edit, strategy):
        if parent is not None:
            for _, opportunity in proposals(parent['program']):
                counts[opportunity['rule']+'/opportunity'] += 1
        key = digest(program)
        if key in seen:
            counts[edit['rule']+'/duplicate'] += 1
            return None
        seen.add(key)
        trial_id = 'd' + str(len(rows)).zfill(4)
        receipt = evaluate_program(root/'trials'/trial_id, bits, shape, program, samples, expected)
        row = {'id': trial_id, 'parent': parent['id'] if parent else None,
               'depth': parent['depth'] + 1 if parent else 0, 'edit': edit,
               'strategy': strategy, 'program': program, 'receipt': receipt}
        rows.append(row)
        counts[edit['rule']+'/applied'] += 1
        if not receipt['passed']:
            counts[edit['rule']+'/rejected'] += 1
        best = min((r for r in rows if r['receipt']['passed']), key=lambda r: r['receipt']['S'])
        curves.append({'evaluation': len(rows), 'best_S': best['receipt']['S'], 'best_id': best['id'],
                       'elapsed_seconds': time.monotonic()-started})
        save(root/'history.json', rows)
        save(root/'curve.json', curves)
        return row

    for lowering in ('legacy', 'direct'):
        for cleanup in ('toffoli', 'measured'):
            evaluate(baseline(shape, lowering, cleanup), None,
                     {'rule': 'global_recipe', 'lowering': lowering, 'cleanup': cleanup}, 'global_control')
    control = min(rows, key=lambda row: row['receipt']['S'])
    # A directed first path creates a useful combination before broad evolution.
    current = rows[0]
    directed = []
    for i, item in enumerate(current['program']['ops']):
        if item['op'] != 'swap':
            directed += [('lowering', i), ('cleanup', i)]
    if mask_groups(current['program']):
        directed.append(('delayed_mask_cleanup', None))
    for rule, i in directed:
        children = proposals(current['program'])
        matching = [(p, edit) for p, edit in children if edit['rule'] == rule and edit.get('location') == i]
        for program, edit in matching:
            row = evaluate(program, current, edit, 'targeted_path')
            if row is not None:
                current = row
    # The final global direct/measured recipe can duplicate the path endpoint.
    # Seed the cache edit from that endpoint as well, retaining its real parent.
    if mask_groups(control['program']):
        for program, edit in proposals(control['program']):
            if edit['rule'] == 'delayed_mask_cleanup':
                evaluate(program, control, edit, 'targeted_combination')
    attempts = 0
    while len(rows) < trials and attempts < trials*50:
        attempts += 1
        if deadline and now() >= deadline:
            counts['deadline_stop'] += 1
            break
        parent = select_parent(rows, rng, policy)
        children = proposals(parent['program'])
        unseen = [(p, edit) for p, edit in children if digest(p) not in seen]
        if not unseen:
            continue
        program, edit = rng.choice(unseen)
        evaluate(program, parent, edit, 'evolution_' + policy)
    valid = [row for row in rows if row['receipt']['passed']]
    best = min(valid, key=lambda row: row['receipt']['S'])
    ancestry, node = [], best
    by_id = {row['id']: row for row in rows}
    while node is not None:
        ancestry.append({'id': node['id'], 'parent': node['parent'], 'edit': node['edit'],
                         'S': node['receipt']['S'], 'T': node['receipt']['T'], 'Q': node['receipt']['Q']})
        node = by_id.get(node['parent'])
    summary = {'plan': plan, 'evaluations': len(rows), 'passed': len(valid),
               'controls': [r['id'] for r in rows if r['strategy'] == 'global_control'],
               'best_control': control['id'], 'control_S': control['receipt']['S'],
               'baseline_S': rows[0]['receipt']['S'], 'best': best['id'],
               'best_S': best['receipt']['S'], 'best_T': best['receipt']['T'], 'best_Q': best['receipt']['Q'],
               'gain_over_best_global_percent': 100*(1-best['receipt']['S']/control['receipt']['S']),
               'best_ancestry': list(reversed(ancestry)), 'maximum_depth': max(r['depth'] for r in rows),
               'largest_program_ops': max(len(r['program']['ops']) for r in rows),
               'count_definition': 'Opportunity counts enumerate guarded edits on each evaluated parent. Applied counts count emitted candidates. Rejected counts count candidates with an observed output, phase, or scratch failure.',
               'rule_counts': dict(counts), 'completed_utc': now(),
               'wall_seconds': time.monotonic()-started,
               'decision': 'supported_development_gain' if best['receipt']['S'] < control['receipt']['S']
                           else 'no_gain_over_global_controls', 'scope': SCOPE}
    save(root/'summary.json', summary)
    return summary
