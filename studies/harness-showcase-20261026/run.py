#!/usr/bin/env python3
"""Two bounded showcases: proposal format and reusable compiler extensions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from llm_circuit_optimiser.common import digest, load, save, sha256
from llm_circuit_optimiser.proposal_calls import request, SCHEMA
from llm_circuit_optimiser.rewrite_rules import cost, nonlinear_rank
from llm_circuit_optimiser.showcase import assess, control, freeze_rules, task_prompt
from llm_circuit_optimiser.runner import check

SEEDS = [401, 809, 1601]
CONTROL_SEEDS = SEEDS + list(range(2001, 2018))
SOURCES = ['rewrite_rules.py', 'rewrite_stream.py', 'proposal_calls.py', 'showcase.py', 'models.py', 'common.py', 'runner.py']
SOURCE_PATHS = ['src/llm_circuit_optimiser/' + p for p in SOURCES] + [
    'native/src/bin/rewrite-mine.rs', 'native/src/bin/rewrite-apply.rs',
    'native/src/lib.rs', 'native/src/bin/circuit-check.rs', 'native/Cargo.lock',
    'src/llm_circuit_optimiser/frontier/paired.py', 'src/llm_circuit_optimiser/frontier/registry.py',
    'studies/harness-showcase-20261026/run.py']


def frozen(path, value):
    if path.exists():
        if load(path) != value:
            raise ValueError('Frozen evidence differs: ' + str(path))
    else:
        save(path, value)
    return value


def command(args, out):
    if out.exists():
        raise ValueError('A process already consumed this path: ' + str(out))
    save(out, {'command': list(map(str, args)), 'complete': False})
    start = time.monotonic()
    p = subprocess.run(list(map(str, args)), capture_output=True, text=True)
    out.with_suffix('.stdout.txt').write_text(p.stdout)
    out.with_suffix('.stderr.txt').write_text(p.stderr)
    save(out, {'command': list(map(str, args)), 'complete': True, 'returncode': p.returncode,
               'wall_seconds': time.monotonic() - start})
    if p.returncode:
        raise ValueError('Command failed; inspect ' + str(out))


def prepare(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    corpus_path = out / 'corpus.json'
    if not corpus_path.exists():
        command([args.native_dir / 'rewrite-mine', '--width', '256', '--ops', args.train,
                 '--out', corpus_path, '--max-wires', '4'], out / 'mining-process.json')
    corpus = load(corpus_path)
    if corpus['input_ops_sha256'] != sha256(args.train):
        raise ValueError('Training artifact changed')
    tasks, excluded = [], []
    for row in corpus['tasks']:
        t = {**row, 'id': digest(row['before'])[:16],
             'nonlinear_lower_bound': nonlinear_rank(row['before'], row['wires'])}
        (tasks if t['nonlinear_lower_bound'] < cost(t['before']) else excluded).append(t)
    if not 1 <= len(tasks) <= 3:
        raise ValueError('This bounded pilot requires one to three eligible tasks; freeze a new protocol for another corpus')
    holdout = dict(item.split('=', 1) for item in args.holdout)
    if not holdout or len(holdout) > 2:
        raise ValueError('Supply one or two held-out circuits')
    if any(sha256(p) == corpus['input_ops_sha256'] for p in holdout.values()):
        raise ValueError('Training circuit cannot be a held-out circuit')
    config = {**load(ROOT / 'configs/claude.json'), 'max_calls': 40, 'timeout_seconds': 240}
    bins = {name: sha256(args.native_dir / name) for name in ['rewrite-mine', 'rewrite-apply', 'circuit-transform', 'circuit-check']}
    protocol = {'schema': 'lco-harness-showcase-v1', 'tasks': tasks, 'excluded_tasks': excluded,
        'task_selection': 'All <=4-wire, 3..7-gate windows with >=2 CCX/CCZ; remove those whose nonlinear output rank equals cost. Frequency order. No model outcome used.',
        'training_ops_sha256': corpus['input_ops_sha256'], 'corpus_sha256': sha256(corpus_path),
        'holdout': {k: sha256(v) for k, v in holdout.items()}, 'forms': ['grammar', 'source'],
        'seeds': SEEDS, 'control_seeds': CONTROL_SEEDS, 'rounds': 2, 'batch_size': 8,
        'max_model_calls': 4 * len(tasks) * len(SEEDS), 'model_config': config,
        'controls': {'random': 4096, 'evolution': 4096, 'enumeration': 20000, 'matched_checkpoint': 16},
        'transfer_selection': 'Union of the three declared seed runs per interface or control; choose the cheapest proved replacement per pattern. No held-out feedback.',
        'audit': {'pairs': 8192, 'streams': 2, 'alpha': 0.05 / 32, 'family_reserve': 32, 'epsilon': 0.001},
        'sources': {p: sha256(ROOT / p) for p in SOURCE_PATHS}, 'binaries': bins,
        'limits': ['Three source contexts of one factoring mechanism; each occurs 894 times in the training circuit', 'Seed labels identify repetitions; provider sampling seeds are uncontrolled',
            'Source and grammar forms share a bounded program language; this tests proposal serialization',
            'The built-in exact catalog is a strong deterministic comparator; matching it establishes rule recovery and reuse, not improvement beyond the full deterministic compiler', 'Transfer alone does not establish a new public frontier point']}
    frozen(out / 'protocol.json', protocol)
    frozen(out / 'setup.json', {'train': str(args.train.resolve()), 'holdout': {k: str(Path(v).resolve()) for k, v in holdout.items()},
                              'native_dir': str(args.native_dir.resolve())})
    print(json.dumps({'eligible_tasks': len(tasks), 'excluded_tasks': len(excluded), 'calls': protocol['max_model_calls']}), flush=True)


def validate(out):
    p, setup = load(out / 'protocol.json'), load(out / 'setup.json')
    for path, expected in p['sources'].items():
        if sha256(ROOT / path) != expected:
            raise ValueError('Frozen implementation changed: ' + path)
    for name, expected in p['binaries'].items():
        if sha256(Path(setup['native_dir']) / name) != expected:
            raise ValueError('Frozen binary changed: ' + name)
    if sha256(out / 'corpus.json') != p['corpus_sha256']:
        raise ValueError('Frozen corpus changed')
    return p, setup


def traces(path, data=None):
    if data is None:
        with gzip.open(path, 'rt') as stream:
            return json.load(stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'xt') as stream:
        json.dump(data, stream, separators=(',', ':'))


def control_path(out, arm, seed, task):
    return out / 'controls' / f'{arm}-{seed}-{task["id"]}.json.gz'


def read_control(out, p, arm, seed, task, prove=False):
    path = control_path(out, arm, seed, task)
    index = load(out / 'control-hashes.json')
    if index.get(path.name) != sha256(path):
        raise ValueError('Control trace hash differs: ' + path.name)
    row = traces(path)
    budget = p['controls'][arm]
    expected = {'arm': arm, 'seed': seed, 'task': task['id'], 'budget': budget}
    if any(row.get(k) != v for k, v in expected.items()) or len(row.get('attempts', [])) != budget:
        raise ValueError('Control trace identity or budget differs')
    if prove:
        for attempt in row['attempts']:
            if assess(task, attempt['program'], 'grammar') != attempt:
                raise ValueError('Saved control proof differs')
    return row


def validate_controls(out, p, prove=False):
    for arm in ['random', 'evolution', 'enumeration']:
        for seed in ([401] if arm == 'enumeration' else CONTROL_SEEDS):
            for task in p['tasks']:
                read_control(out, p, arm, seed, task, prove)


def offline(out):
    p, _ = validate(out)
    index_path = out / 'control-hashes.json'
    if not index_path.exists():
        save(index_path, {})
    for arm in ['random', 'evolution', 'enumeration']:
        for seed in ([401] if arm == 'enumeration' else CONTROL_SEEDS):
            for task in p['tasks']:
                path = control_path(out, arm, seed, task)
                if path.exists():
                    read_control(out, p, arm, seed, task)
                    continue
                data = control(task, arm, seed, p['controls'][arm])
                traces(path, data)
                index = load(index_path)
                index[path.name] = sha256(path)
                save(index_path, index)
            print(f'control {arm} seed {seed} complete', flush=True)


def saved_call(out, p, form, seed, task, task_index, round_index, history):
    index = (((SEEDS.index(seed) * len(p['tasks']) + task_index) * 2 + ['grammar', 'source'].index(form)) * 2 + round_index)
    folder = out / 'calls' / f'{form}-{seed}-{task["id"]}-{round_index}'
    row = load(folder / 'evaluated.json')
    expected_id = {'form': form, 'seed': seed, 'task': task['id'], 'round': round_index, 'call_index': index}
    if any(row.get(k) != v for k, v in expected_id.items()):
        raise ValueError('Saved call identity differs')
    prompt = task_prompt(task, form, history)
    receipt_path = folder / 'receipt.json'
    if receipt_path.exists():
        receipt = load(receipt_path)
        expected_hash = digest({'prompt': prompt, 'config': p['model_config'], 'schema': SCHEMA})
        if receipt.get('request_hash') != expected_hash or load(folder / 'prompt.json') != prompt:
            raise ValueError('Saved prompt differs')
        copied = {k: v for k, v in row['receipt'].items() if k != 'reused'}
        if copied != receipt:
            raise ValueError('Saved receipt or usage differs')
        if receipt.get('success'):
            answer = load(folder / 'answer.json')
            if digest(answer) != receipt.get('answer_hash'):
                raise ValueError('Saved answer differs')
            expected = [assess(task, program, form) for program in answer['proposals']]
        else:
            expected = []
    else:
        if not row.get('error') or row.get('receipt') != {'usage': None, 'success': False}:
            raise ValueError('Missing model receipt')
        expected = []
    while len(expected) < p['batch_size']:
        expected.append({'accepted': False, 'error': 'unused or failed proposal slot', 'form': form})
    if row['attempts'] != expected:
        raise ValueError('Saved attempts do not match the model answer')
    return row


def validate_calls(out, p):
    count = 0
    for seed in SEEDS:
        for i, task in enumerate(p['tasks']):
            for form in ['grammar', 'source']:
                history = []
                for r in range(p['rounds']):
                    row = saved_call(out, p, form, seed, task, i, r, history)
                    history.extend(row['attempts'])
                    count += len(row['attempts'])
    return count


def live(out):
    p, _ = validate(out)
    def one(form, seed, task, task_index):
        history = []
        for round_index in range(p['rounds']):
            index = (((SEEDS.index(seed) * len(p['tasks']) + task_index) * 2 + ['grammar', 'source'].index(form)) * 2 + round_index)
            folder = out / 'calls' / f'{form}-{seed}-{task["id"]}-{round_index}'
            result_path = folder / 'evaluated.json'
            if result_path.exists():
                row = saved_call(out, p, form, seed, task, task_index, round_index, history)
            else:
                print(f'call {index + 1}/{p["max_model_calls"]} {form} seed {seed} task {task["id"]} round {round_index}', flush=True)
                row = {'form': form, 'seed': seed, 'task': task['id'], 'round': round_index, 'call_index': index}
                try:
                    answer, receipt = request(task_prompt(task, form, history), p['model_config'], folder, index)
                    attempts = [assess(task, s, form) for s in answer['proposals']]
                    row['receipt'] = receipt
                except ValueError as error:
                    attempts = []
                    row['error'] = str(error)
                    row['receipt'] = load(folder / 'receipt.json') if (folder / 'receipt.json').exists() else {'usage': None, 'success': False}
                while len(attempts) < p['batch_size']:
                    attempts.append({'accepted': False, 'error': 'unused or failed proposal slot', 'form': form})
                row['attempts'] = attempts
                save(result_path, row)
                print(f'finished call {index + 1}: {sum(a["accepted"] for a in attempts)} accepted slots', flush=True)
            history.extend(row['attempts'])
    jobs = [(form, seed, task, i) for seed in SEEDS for i, task in enumerate(p['tasks']) for form in ['grammar', 'source']]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda job: one(*job), jobs))


def freeze(out):
    p, _ = validate(out)
    validate_calls(out, p)
    validate_controls(out, p)
    frozen(out / 'selection-inputs.json', {'controls_sha256': sha256(out / 'control-hashes.json'),
        'calls': {str(path.relative_to(out)): sha256(path) for path in sorted((out / 'calls').glob('*/evaluated.json'))}})
    packages = {'base': []}
    for form in ['grammar', 'source']:
        attempts = {}
        for seed in SEEDS:
            for task in p['tasks']:
                for round_index in range(p['rounds']):
                    path = out / 'calls' / f'{form}-{seed}-{task["id"]}-{round_index}' / 'evaluated.json'
                    attempts.setdefault(task['id'], []).extend(load(path)['attempts'])
        packages[form] = freeze_rules(p['tasks'], attempts)
    for arm in ['random', 'evolution', 'enumeration']:
        for budget in ([20000] if arm == 'enumeration' else [16, 4096]):
            attempts = {}
            for seed in ([401] if arm == 'enumeration' else SEEDS):
                for task in p['tasks']:
                    row = read_control(out, p, arm, seed, task)
                    attempts.setdefault(task['id'], []).extend(row['attempts'][:budget])
            packages[f'{arm}-{budget}'] = freeze_rules(p['tasks'], attempts)
    for name, rules in packages.items():
        frozen(out / 'rules' / f'{name}.json', rules)
    frozen(out / 'selected-rules.json', {k: digest(v) for k, v in packages.items()})
    print('froze all rule packages before held-out evaluation', flush=True)


def transfer(out):
    freeze(out)
    p, setup = validate(out)
    selected = load(out / 'selected-rules.json')
    native = Path(setup['native_dir'])
    endpoints = {}
    for source_id, source in setup['holdout'].items():
        if sha256(source) != p['holdout'][source_id]:
            raise ValueError('Held-out source changed')
        rows = {}
        for name in [*selected, 'catalog']:
            rules = None if name == 'catalog' else load(out / 'rules' / f'{name}.json')
            if rules is not None and digest(rules) != selected[name]:
                raise ValueError('Frozen rule package changed')
            if rules == []:
                ops, receipt = Path(source), None
            else:
                key = 'catalog' if name == 'catalog' else selected[name]
                folder = out / 'circuits' / source_id
                folder.mkdir(parents=True, exist_ok=True)
                ops = folder / f'{key}.qops.zst'
                receipt = Path(str(ops) + '.json') if name == 'catalog' else ops.with_suffix('.json')
                if not receipt.exists():
                    if name == 'catalog':
                        args = [native / 'circuit-transform', '--width', '256', '--ops', source,
                                '--out', ops, '--passes', 'local-catalog']
                    else:
                        args = [native / 'rewrite-apply', '--width', '256', '--ops', source,
                                '--out', ops, '--rules', out / 'rules' / f'{name}.json']
                    print(f'transform {source_id} {name}', flush=True)
                    command(args, folder / f'{key}-process.json')
                r = load(receipt)
                if r['input_ops_sha256'] != p['holdout'][source_id] or r['ops_sha256'] != sha256(ops):
                    raise ValueError('Transformed artifact changed')
                if name != 'catalog' and r['rules_sha256'] != sha256(out / 'rules' / f'{name}.json'):
                    raise ValueError('Transformed rule certificate changed')
            rows[name] = {'ops': str(ops), 'ops_sha256': sha256(ops),
                          'transform': None if receipt is None else load(receipt)}
        endpoints[source_id] = rows
    frozen(out / 'endpoints.json', endpoints)
    distinct = {r['ops_sha256'] for rows in endpoints.values() for r in rows.values()}
    if len(distinct) > p['audit']['family_reserve']:
        raise ValueError('Audit family reserve exhausted')
    if not (out / 'audit-label.json').exists():
        save(out / 'audit-label.json', {'label': 'harness-showcase/final/' + secrets.token_hex(20),
                                      'endpoints_sha256': sha256(out / 'endpoints.json')})
    label = load(out / 'audit-label.json')
    if label['endpoints_sha256'] != sha256(out / 'endpoints.json'):
        raise ValueError('Endpoints changed after final inputs were reserved')
    results = {}
    for source_id, rows in endpoints.items():
        audited = {}
        for name, row in rows.items():
            key = row['ops_sha256']
            if key not in audited:
                print(f'audit {source_id} {name}: {p["audit"]["pairs"]} fresh pairs', flush=True)
                audited[key] = check({'circuit_check': str(native / 'circuit-check')}, row['ops'], 256,
                                     p['audit']['pairs'], label['label'] + '/' + source_id,
                                     out / 'audits' / f'{source_id}-{key}.json', p['audit']['alpha'])
            audit = audited[key]
            results.setdefault(source_id, {})[name] = {
                'Q': audit['qubits'], 'T': audit['mean_toffoli'], 'QT': audit['qubits'] * audit['mean_toffoli'],
                'failed_inputs': audit['failed_inputs'], 'qualified': audit['meets_bound'],
                'upper_bound': audit['binomial_upper'], 'ops_sha256': key,
                'static_toffoli_saved': (row['transform'] or {}).get('static_toffoli_saved')}
        base = results[source_id]['base']['QT']
        for row in results[source_id].values():
            row['measured_gain_percent'] = 100 * (1 - row['QT'] / base)
            row['qualified_gain_percent'] = row['measured_gain_percent'] if row['qualified'] and results[source_id]['base']['qualified'] else None
    save(out / 'transfer.json', results)


def report(out):
    p, _ = validate(out)
    validate_calls(out, p)
    cells = []
    for form in ['grammar', 'source']:
        for seed in SEEDS:
            for task in p['tasks']:
                rows = [load(out / 'calls' / f'{form}-{seed}-{task["id"]}-{r}' / 'evaluated.json') for r in range(2)]
                attempts = [a for r in rows for a in r['attempts']]
                costs = [cost(a['gates']) for a in attempts if a['accepted']]
                usage = [r['receipt'].get('usage') for r in rows]
                fields = ['input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens']
                totals = {f: sum(u[f] for u in usage) if all(isinstance(u, dict) and type(u.get(f)) is int for u in usage) else None for f in fields}
                cells.append({'form': form, 'seed': seed, 'task': task['id'], 'best_cost': min(costs + [cost(task['before'])]),
                              'lower_bound': task['nonlinear_lower_bound'], 'accepted': len(costs), 'slots': len(attempts),
                              'successful_calls': sum(bool(r['receipt'].get('success')) for r in rows), 'usage': totals,
                              'missing_usage_calls': sum(u is None for u in usage)})
    paired = []
    for seed in SEEDS:
        for task in p['tasks']:
            pair = {r['form']: r for r in cells if r['seed'] == seed and r['task'] == task['id']}
            paired.append({'seed': seed, 'task': task['id'], 'same_best_cost': pair['grammar']['best_cost'] == pair['source']['best_cost'],
                           'both_reach_lower_bound': all(r['best_cost'] == r['lower_bound'] for r in pair.values())})
    controls = []
    for arm in ['random', 'evolution', 'enumeration']:
        for seed in ([401] if arm == 'enumeration' else CONTROL_SEEDS):
            for task in p['tasks']:
                row = read_control(out, p, arm, seed, task)
                for budget in ([20000] if arm == 'enumeration' else [16, 4096]):
                    attempts = row['attempts'][:budget]
                    controls.append({'arm': arm, 'seed': seed, 'task': task['id'], 'budget': budget,
                        'best_cost': min([cost(a['gates']) for a in attempts if a['accepted']] + [cost(task['before'])]),
                        'full_run_verifier_calls': row['verifier_calls'], 'full_run_wall_seconds': row['wall_seconds']})
    summary = {'protocol_sha256': sha256(out / 'protocol.json'), 'cells': cells, 'paired': paired, 'controls': controls,
               'transfer': load(out / 'transfer.json') if (out / 'transfer.json').exists() else None,
               'limits': p['limits'], 'status': 'transfer-complete' if (out / 'transfer.json').exists() else 'local-proposals-only'}
    save(out / 'results.json', summary)
    print(json.dumps({'status': summary['status'], 'paired_same': sum(r['same_best_cost'] for r in paired),
                      'paired_lower_bound': sum(r['both_reach_lower_bound'] for r in paired), 'pairs': len(paired)}), flush=True)


def verify(out):
    p, _ = validate(out)
    count = validate_calls(out, p)
    validate_controls(out, p, prove=True)
    freeze(out)
    for path in sorted((out / 'rules').glob('*.json')):
        from llm_circuit_optimiser.rewrite_rules import certify
        for rule in load(path):
            if not certify(rule['before'], rule['after'], rule['wires'])['accepted']:
                raise ValueError('Invalid saved compiler rule')
    save(out / 'verification.json', {'protocol_sha256': sha256(out / 'protocol.json'),
                                    'checked_model_slots': count, 'exact_rule_proofs': 'passed'})
    print(f'verified {count} model slots and all saved exact rules', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'offline', 'live', 'freeze', 'transfer', 'report', 'verify'])
    parser.add_argument('--out', type=Path, default=ROOT / 'work/harness-showcase-20261026')
    parser.add_argument('--train', type=Path)
    parser.add_argument('--holdout', action='append', default=[])
    parser.add_argument('--native-dir', type=Path, default=ROOT / 'work/native-target/release')
    args = parser.parse_args()
    if args.command == 'prepare':
        if args.train is None:
            parser.error('prepare requires --train')
        prepare(args)
    else:
        globals()[args.command](args.out.resolve())

if __name__ == '__main__':
    main()
