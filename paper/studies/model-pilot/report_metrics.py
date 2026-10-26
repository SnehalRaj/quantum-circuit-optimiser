"""Summarize the archived pilot without another provider or circuit call."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import statistics
import run_pilot as pilot


result_path = pilot.PAPER / 'data/model-pilot.json'
result = pilot.load(result_path)
result['finished_utc'] = datetime.now(timezone.utc).isoformat()
result['elapsed_wall_seconds'] = (datetime.now(timezone.utc) - datetime.fromisoformat(
    pilot.load(pilot.HERE / 'contract.json')['created_utc'])).total_seconds()
result['environment'] = pilot.load(pilot.HERE / 'runs/guarded-seeds-v1/offline/environment.json')
audit_receipts = pilot.load(pilot.HERE / 'audit/results.json')
result['audit_resources'] = {
    'distinct_recipes': len(audit_receipts),
    'distinct_operation_hashes': len({r['operation_sha256'] for r in audit_receipts.values()}),
    'input_packet_hashes': sorted({r['input_sha256'] for r in audit_receipts.values()}),
    'cpu_seconds': sum(r['cpu_seconds'] for r in audit_receipts.values()),
    'wall_seconds': sum(r['wall_seconds'] for r in audit_receipts.values()),
    'scope': 'One common fresh audit after every available arm freezes; costs are separate from search costs'}
actual = [r for r in result['summary'] if r['arm'] != 'codex']
result['executable_summary'] = actual
result['status'] = ('complete_with_identifier_amendment'
                    if all(r['status'] == 'complete' for r in actual) else 'partial')
calls = []
for path in sorted((pilot.HERE / 'preflight').glob('*.json')):
    record = pilot.load(path)
    receipt = record.get('receipt', record)
    calls.append({'kind': 'preflight', 'arm': path.stem, 'path': str(path.relative_to(pilot.HERE)),
                  'status': receipt.get('status'), 'usage': receipt.get('usage'),
                  'requested_model': receipt.get('model'),
                  'reported_models': receipt.get('resolved_models'),
                  'wall_seconds': receipt.get('wall_seconds')})
for path in sorted((pilot.HERE / 'runs').glob('*/*/runs/*/model/*-receipt.json')):
    receipt = pilot.load(path)
    calls.append({'kind': 'feedback', 'arm': path.parts[-5],
                  'path': str(path.relative_to(pilot.HERE)),
                  'status': receipt.get('status'), 'usage': receipt.get('usage'),
                  'requested_model': receipt.get('model'),
                  'reported_models': receipt.get('resolved_models'),
                  'model_identity_status': receipt.get('model_identity_status'),
                  'wall_seconds': receipt.get('wall_seconds')})
result['all_provider_calls'] = calls
result['all_provider_call_statuses'] = dict(Counter(r['status'] for r in calls))
result['all_provider_call_usage'] = {
    name: {'reported_sum': sum(r['usage'].get(name, 0) or 0 for r in calls if r.get('usage')),
           'missing_calls': sum(not r.get('usage') or r['usage'].get(name) is None for r in calls)}
    for name in ['input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_tokens',
                 'provider_cost_estimate_usd']}
result['paired_contrasts'] = []
for condition in pilot.CONDITIONS:
    control = next(r for r in actual if r['condition'] == condition and r['arm'] == 'offline')
    control_seeds = {r['seed']: r for r in control['seed_scores']}
    for arm in ['claude', 'codex61sol']:
        row = next(r for r in actual if r['condition'] == condition and r['arm'] == arm)
        pairs = [{'seed': r['seed'],
                  'improvement_difference_percentage_points': r['weighted_improvement_percent'] - control_seeds[r['seed']]['weighted_improvement_percent']}
                 for r in row['seed_scores'] if r['seed'] in control_seeds]
        differences = [r['improvement_difference_percentage_points'] for r in pairs]
        result['paired_contrasts'].append({'condition': condition, 'arm': arm,
            'comparison': 'model-assisted minus offline evolution on the same search seed',
            'pairs': pairs, 'median_difference_percentage_points': statistics.median(differences) if differences else None,
            'scope': 'Descriptive comparison of three fresh seeds; no provider ranking claim'})
for row in actual:
    root = pilot.HERE / 'runs' / row['condition'] / row['arm']
    endpoint_trials = []
    model_statuses = Counter()
    feedback_errors = Counter()
    for path in sorted((root / 'runs').glob('*/run.json')):
        run = pilot.load(path)
        for baseline, state in run['baselines'].items():
            endpoint = state['selected']['result']['S']
            first = next(t['trial'] for t in run['trace'] if t['baseline'] == baseline and t['best_S'] == endpoint)
            endpoint_trials.append({'seed': run['seed'], 'baseline': baseline, 'trial': first})
        model_statuses.update(t['status'] for t in run['trials'] if t['origin'] == 'model')
        feedback_errors.update(r.get('error') for r in run['model_calls'] if r.get('error'))
    row['first_final_cost_trials'] = endpoint_trials
    row['model_proposal_statuses'] = dict(model_statuses)
    row['feedback_errors'] = dict(feedback_errors)
result['arms'] = []
for row in actual:
    arm = row['arm']
    provider, model = {'offline': ('offline', None), 'claude': ('claude', 'claude-opus-5-5'),
                       'codex61sol': ('codex', 'gpt-6.1-sol')}[arm]
    selections = [r for r in result['selections'] if r['condition'] == row['condition'] and r['arm'] == arm]
    seed_scores = [{'seed': r['seed'], 'remaining': r['weighted_remaining_cost'],
                    'improvement_percent': r['weighted_improvement_percent']}
                   for r in row['seed_scores']]
    result['arms'].append({'condition': row['condition'], 'provider': provider,
        'arm': arm, 'requested_model': model,
        'resolved_models': row['reported_models'],
        'resolved_status': ('not_applicable' if provider == 'offline' else
                            'verified' if row['reported_models'] == [model] else
                            'requested_accepted_resolved_id_unreported'),
        'status': row['status'], 'seed_scores': seed_scores,
        'median_remaining': statistics.median(r['remaining'] for r in seed_scores) if seed_scores else None,
        'median_improvement_percent': row['median_weighted_improvement_percent'],
        'qualified_endpoints': sum(r['qualified'] for r in selections),
        'total_endpoints': len(selections),
        'feedback_calls': {'attempted': row['feedback_calls'],
                           'completed': row['feedback_statuses'].get('completed', 0),
                           'failed': row['feedback_statuses'].get('failed', 0),
                           'timeouts': row['feedback_errors'].get('TimeoutExpired', 0)},
        'feedback_statuses': row['feedback_statuses'],
        'tokens': {key: value['reported_sum'] for key, value in row['usage'].items()
                   if key != 'provider_cost_estimate_usd'},
        'usage_missing_calls': {key: value['missing_calls'] for key, value in row['usage'].items()},
        'wall': {'search_seconds': row['search_wall_seconds'], 'model_seconds': row['model_wall_seconds'],
                 'evaluator_cpu_seconds': row['evaluator_cpu_seconds'],
                 'evaluator_wall_seconds': row['evaluator_wall_seconds']},
        'proposal_slots': row['proposal_slots_observed'],
        'proposal_statuses': row['proposal_statuses'], 'proposal_origins': row['proposal_origins'],
        'model_proposal_statuses': row['model_proposal_statuses'],
        'first_final_cost_trials': row['first_final_cost_trials']})
result['analysis_source_sha256'] = pilot.sha(__file__)
result['failed_feedback_policy'] = (
    'A failed call adds no proposals and consumes its scheduled call. Existing pending proposals remain. '
    'The shared seed path has priority in its condition. Otherwise the engine consumes pending proposals; '
    'when none remain, it uses the normal grammar mutation with 30 percent random viable-parent selection '
    'and 70 percent cheapest viable-parent selection. No feedback call receives a retry.')
pilot.save(result_path, result)
print('Wrote', result_path)
for row in actual:
    print(row['condition'], row['arm'], row['median_weighted_improvement_percent'],
          row['feedback_calls'], row['proposal_statuses'])
