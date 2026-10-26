"""Run a source-frozen, matched model pilot without changing production code."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
PAPER = HERE.parent.parent
REPO = PAPER.parents[1] / 'reversible-circuit-optimizer-strategies'
SOURCE = HERE / 'source'
CONDITIONS = ['guarded-seeds-v1', 'guarded-cleanup-only-v1']
ARMS = {'offline': ('offline', None, 'evolution'),
        'claude': ('claude', 'claude-opus-5-5', 'llm'),
        'codex': ('codex', 'gpt-6.1', 'llm')}
SEEDS = [17071, 28081, 39091]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w') as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + '\n')


def load(path):
    return json.loads(Path(path).read_text())


def prepare():
    if (HERE / 'contract.json').exists():
        raise FileExistsError('The immutable contract already exists')
    shutil.copytree(REPO / 'src/circuitopt', SOURCE / 'src/circuitopt',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ['pyproject.toml', 'requirements-study.lock']:
        shutil.copy2(REPO / name, SOURCE / name)
    calibration_source = REPO / 'runs/experiments/opus55-guarded-blocks-20261023/study.json'
    shutil.copy2(calibration_source, HERE / 'calibration-source.json')
    files = {str(p.relative_to(HERE)): sha(p) for p in sorted(SOURCE.rglob('*')) if p.is_file()}
    contract = {
        'schema': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'frozen_before_new_search_or_provider_outcomes',
        'study': 'Retrospective component study designed after the frontier results',
        'source_repo': str(REPO), 'source_commit': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'source_files': files, 'driver_sha256': sha(__file__),
        'calibration_source': str(calibration_source),
        'calibration_source_sha256': sha(calibration_source),
        'conditions': CONDITIONS, 'seeds': SEEDS, 'bits': 16,
        'baselines': ['inverse', 'replay', 'width'],
        'grammar': 'blocks-v1',
        'condition_definition': {
            'guarded-seeds-v1': 'Unchanged production guards and shared construction path',
            'guarded-cleanup-only-v1': 'Same guards; trial 1 seeds measured cleanup only; all later actions use the chosen proposer'},
        'arm_definition': ARMS,
        'proposal_slots_per_baseline': 24, 'total_proposal_slots': 1296,
        'feedback_rounds': [2, 5], 'feedback_calls_per_search': 2,
        'planned_feedback_calls_per_model': 12,
        'preflight_calls_per_model': 1,
        'reasoning_effort': 'medium', 'provider_timeout_seconds': 180,
        'search_wall_cap_seconds': 1200, 'evaluation_concurrency': 1,
        'weights': {'inverse': 1, 'replay': 32.72575137951653, 'width': 100},
        'development_protocol': 'Reuse the recorded calibration, long_pairs and quick_pairs for every arm',
        'audit_pairs': 16384, 'measurement_streams_per_input': 2,
        'audit_label': 'Draw once after every available arm freezes its finalists',
        'qualification_epsilon': 0.001, 'global_alpha': 0.05,
        'reserved_distinct_audit_circuits': 128,
        'family_size_argument': '54 selected endpoints + 54 cleanup controls + 3 shared starts <= 111; reserve 128',
        'failure_policy': 'Failed calls and invalid proposals consume their declared opportunity. Do not replace a failed arm.',
        'model_identity_policy': 'Retain requested and CLI-reported identities separately; unreported identity remains unreported',
        'selection_metric': 'Median seed-level weighted remaining cost; all three baselines; failed qualification retains starting cost',
        'known_interaction': 'The production shared path takes priority over pending model proposals during its seed actions',
        'environment': {'python': sys.version, 'platform': platform.platform(),
                        'processor': platform.processor(), 'logical_cpus': os.cpu_count()},
    }
    save(HERE / 'contract.json', contract, True)
    (HERE / 'contract.sha256').write_text(sha(HERE / 'contract.json') + '\n')
    print('Frozen', sha(HERE / 'contract.json'), flush=True)


def import_source():
    sys.path.insert(0, str(SOURCE / 'src'))
    from circuitopt import engine, providers, rules, search_space, study, study_policy
    return engine, providers, rules, search_space, study, study_policy


def verify_contract():
    contract = load(HERE / 'contract.json')
    if sha(HERE / 'contract.json') != (HERE / 'contract.sha256').read_text().strip():
        raise ValueError('The model pilot contract changed')
    if sha(__file__) != contract['driver_sha256']:
        raise ValueError('The model pilot driver changed')
    for name, digest in contract['source_files'].items():
        if sha(HERE / name) != digest:
            raise ValueError('Frozen source changed: ' + name)
    return contract


def transport_with_metadata(providers):
    original = providers.generate
    original_run = subprocess.run

    def generate(*args, **kwargs):
        captured = []

        def capture(command, **options):
            result = original_run(command, **options)
            captured.append({'command': command, 'stderr': result.stderr,
                             'returncode': result.returncode})
            return result

        subprocess.run = capture
        try:
            answer, receipt = original(*args, **kwargs)
        finally:
            subprocess.run = original_run
        receipt['transport_metadata'] = captured
        if args[0] == 'codex':
            text = '\n'.join(str(item.get('stderr', '')) for item in captured)
            reported = re.findall(r'^model:\s*(\S+)', text, re.MULTILINE)
            for event in providers._codex_events(receipt.get('raw_response', '')):
                if isinstance(event.get('model'), str):
                    reported.append(event['model'])
            receipt['resolved_models'] = sorted(set(reported))
            receipt['model_identity_status'] = 'verified' if reported else 'requested_only_cli_did_not_report_model'
        path = kwargs.get('receipt_path')
        if path:
            save(path, receipt)
        return answer, receipt

    providers.generate = generate


def run():
    contract = verify_contract()
    engine, providers, rules, search_space, study, policy = import_source()
    for name in ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY']:
        os.environ.pop(name, None)
    transport_with_metadata(providers)
    preflights = {}
    for arm, (provider, model, _) in ARMS.items():
        if provider == 'offline':
            continue
        path = HERE / 'preflight' / (arm + '.json')
        if path.exists():
            preflights[arm] = load(path)
            continue
        answer, receipt = providers.generate(provider, model,
            'Connection check only. Return {"proposals":[]}. Use no tools.',
            timeout=180, reasoning_effort='medium', isolated=True, receipt_path=path)
        save(path, {'answer': answer, 'receipt': receipt})
        preflights[arm] = {'answer': answer, 'receipt': receipt}
        print('Preflight', arm, receipt['status'], receipt.get('resolved_models'), flush=True)
    baseline_calibration = load(HERE / 'calibration-source.json')['calibration']
    baseline_calibration['16']['weights'] = contract['weights']
    original_seed = policy.seed_change

    def cleanup_only(library, state, trial, selected_policy='historical-v1', active_rules=None):
        if trial != 1:
            return None
        parent = state['starting']
        applied = rules.apply(library, 'R02', parent['recipe'], {})
        return parent, applied

    for condition in CONDITIONS:
        policy.seed_change = original_seed if condition == 'guarded-seeds-v1' else cleanup_only
        for arm, (provider, model, strategy) in ARMS.items():
            root = HERE / 'runs' / condition / arm
            if provider != 'offline' and preflights[arm]['receipt']['status'] != 'completed':
                save(root / 'blocked.json', preflights[arm])
                continue
            if not (root / 'study.json').exists():
                study.initialise(root, bits=[16], trials=24, seeds=SEEDS,
                    provider=provider, model=model, calibration_size=64,
                    audit_pairs=16384, epsilon=0.001, strategies=[strategy],
                    format_arms=[], review_rounds=[2, 5], max_model_calls=2,
                    wall_seconds=1200, reasoning_effort='medium', model_timeout=180,
                    grammar='blocks-v1', proposal_policy='guarded-seeds-v1')
                c = study.load(root / 'study.json')
                c['calibration'] = baseline_calibration
                c['pilot_condition'] = condition
                c['pilot_contract_sha256'] = sha(HERE / 'contract.json')
                study.save(root / 'study.json', c)
                (root / 'contract.sha256').write_text(engine.digest(c) + '\n')
            for seed in SEEDS:
                rid = f'16-{strategy}-compact_json-{seed}'
                if (root / 'runs' / rid / 'run.json').exists():
                    continue
                print('Search', condition, arm, seed, flush=True)
                start = time.monotonic()
                result = study.search(root, 16, strategy, seed)
                print('Search complete', condition, arm, seed, result['status'],
                      round(time.monotonic() - start, 2), flush=True)
            if not (root / 'final/manifest.json').exists():
                study.freeze(root)
    policy.seed_change = original_seed


def audit():
    contract = verify_contract()
    engine, _, _, _, study, _ = import_source()
    circuits = {}
    selections = []
    for condition in CONDITIONS:
        for arm in ARMS:
            root = HERE / 'runs' / condition / arm
            path = root / 'final/manifest.json'
            if not path.exists():
                continue
            m = load(path)
            for cid, item in m['circuits'].items():
                if cid not in circuits:
                    circuits[cid] = {'root': str(root), 'recipe': item['recipe'],
                                     'operation_sha256': item['operation_sha256']}
            for row in m['selections']:
                selections.append({'condition': condition, 'arm': arm, **row})
    if len(circuits) > 128:
        raise ValueError('The reserved audit family is too small')
    frozen = HERE / 'audit/frozen.json'
    if not frozen.exists():
        save(frozen, {'circuits': circuits, 'selections': selections,
                      'label': secrets.token_hex(32), 'alpha_per_circuit': 0.05 / 128,
                      'pairs': 16384, 'frozen_utc': datetime.now(timezone.utc).isoformat()}, True)
    m = load(frozen)
    results = {}
    for cid, item in m['circuits'].items():
        target = HERE / 'audit/results' / (cid + '.json')
        if target.exists():
            results[cid] = load(target)
            continue
        root = Path(item['root'])
        result = study.evaluate(root, item['recipe'], 16384, m['label'],
            'joint-model-pilot-audit-' + cid, 'audit', timeout=1800,
            circuit_directory=root / 'final/circuits' / cid)
        if result['operation_sha256'] != item['operation_sha256']:
            raise ValueError('The audited operation identity differs')
        result['epsilon_upper'] = engine.upper(result['failed_inputs'], result['pairs'], 0.05 / 128)
        result['eligible'] = result['epsilon_upper'] <= 0.001
        save(target, result, True)
        results[cid] = result
        print('Audit', cid[:12], result['failed_inputs'], result['epsilon_upper'], flush=True)
    save(HERE / 'audit/results.json', results)


def analyse():
    verify_contract()
    m = load(HERE / 'audit/frozen.json')
    audits = load(HERE / 'audit/results.json')
    rows = []
    for s in m['selections']:
        before = audits[s['starting']]
        after = audits[s['selected']]
        qualified = before['eligible'] and after['eligible']
        rows.append({**s, 'starting_S': before['S'], 'candidate_S': after['S'],
                     'retained_S': after['S'] if qualified else before['S'],
                     'qualified': qualified, 'audit_failed_inputs': after['failed_inputs'],
                     'audit_pairs': 16384, 'audit_upper': after['epsilon_upper']})
    summaries = []
    for condition in CONDITIONS:
        for arm in ARMS:
            root = HERE / 'runs' / condition / arm
            runs = [load(p) for p in sorted((root / 'runs').glob('*/run.json'))]
            subset = [r for r in rows if r['condition'] == condition and r['arm'] == arm]
            seed_scores = []
            for seed in SEEDS:
                a = [r for r in subset if r['seed'] == seed]
                if len(a) != 3:
                    continue
                remaining = sum(r['weight'] * r['retained_S'] / r['starting_S'] for r in a) / sum(r['weight'] for r in a)
                seed_scores.append({'seed': seed, 'weighted_remaining_cost': remaining,
                                    'weighted_improvement_percent': 100 * (1 - remaining)})
            trial_rows = [r for run in runs for r in run['trials']]
            calls = [r for run in runs for r in run['model_calls']]
            eval_costs = [json.loads(line) for run_dir in (root / 'runs').glob('*')
                          if (run_dir / 'costs.jsonl').exists()
                          for line in (run_dir / 'costs.jsonl').read_text().splitlines()]
            summaries.append({'condition': condition, 'arm': arm,
                'status': 'complete' if len(seed_scores) == 3 else 'blocked_or_incomplete',
                'seed_scores': seed_scores,
                'median_weighted_improvement_percent': statistics.median(r['weighted_improvement_percent'] for r in seed_scores) if seed_scores else None,
                'proposal_slots_observed': len(trial_rows), 'proposal_statuses': dict(Counter(r['status'] for r in trial_rows)),
                'proposal_origins': dict(Counter(r['origin'] for r in trial_rows)),
                'feedback_calls': len(calls), 'feedback_statuses': dict(Counter(r['status'] for r in calls)),
                'requested_models': sorted(set(r['model'] for r in calls)),
                'reported_models': sorted(set(v for r in calls for v in r.get('resolved_models', []))),
                'model_identity_statuses': dict(Counter(r.get('model_identity_status', 'unreported') for r in calls)),
                'usage': {name: {'reported_sum': sum(r.get('usage', {}).get(name, 0) or 0 for r in calls if r.get('usage')),
                                 'missing_calls': sum(not r.get('usage') or r['usage'].get(name) is None for r in calls)}
                          for name in ['input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_tokens', 'provider_cost_estimate_usd']},
                'model_wall_seconds': sum(r['wall_seconds'] for r in calls),
                'search_wall_seconds': sum(r['wall_seconds'] for r in runs),
                'evaluator_cpu_seconds': sum(r.get('evaluator_cpu_seconds', r.get('cpu_seconds', 0)) or 0 for r in eval_costs),
                'evaluator_wall_seconds': sum(r.get('evaluator_wall_seconds', r.get('wall_seconds', 0)) or 0 for r in eval_costs if r['kind'] != 'model'),
            })
    output = {'status': 'complete' if all(s['status'] == 'complete' for s in summaries) else 'partial',
        'contract_path': str(HERE / 'contract.json'), 'contract_sha256': sha(HERE / 'contract.json'),
        'scope': 'Retrospective small16-bit division pilot; same grammar and guards; no full point-addition or provider-superiority claim',
        'summary': summaries, 'audited_distinct_circuits': len(audits), 'audit_reserved_family': 128,
        'audit_alpha_per_circuit': 0.05 / 128, 'selections': rows,
        'limits': ['Three seeds per cell', 'The shared path can reach the endpoint before queued model proposals are applied',
                   'Equal proposal slots do not imply equal elapsed time, token use or expense',
                   'CLI-reported model identity is distinguished from the requested model',
                   'The fresh audit covers the stated input distribution and two measurement streams']}
    save(PAPER / 'data/model-pilot.json', output)
    print(json.dumps([{k: s[k] for k in ['condition', 'arm', 'status', 'median_weighted_improvement_percent', 'proposal_statuses', 'feedback_calls']} for s in summaries], indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'run', 'audit', 'analyse'])
    args = parser.parse_args()
    {'prepare': prepare, 'run': run, 'audit': audit, 'analyse': analyse}[args.stage]()
