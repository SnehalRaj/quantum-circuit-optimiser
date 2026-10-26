"""Build portable experiment records from saved measurements."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path

from . import engine, providers, study


def _load(path, default):
    return study.load(path) if path.is_file() else default


def _known_sum(rows, key):
    values = [(r.get('usage') or {}).get(key) for r in rows]
    known = [v for v in values if isinstance(v, (int, float))]
    return sum(known) if known or not rows else None


def collect(root):
    """Read artifacts without rerunning a model, evaluator, or consumed audit."""
    root = Path(root)
    contract = study.load(root / 'study.json')
    config = _load(root / 'experiment.json', {})
    audit = _load(root / 'final/results.json', {})
    state = _load(root / 'experiment-state.json', {'status': 'recorded_study'})
    costs = [json.loads(line) for line in (root / 'costs.jsonl').read_text().splitlines() if line.strip()] if (root / 'costs.jsonl').is_file() else []
    model_costs = [row for row in costs if row.get('kind') == 'model' and row.get('provider') != 'offline']
    recorded_ids = {row.get('call_id') for row in model_costs if row.get('call_id')}
    pending_paths = list((root / 'runs').glob('*/model/*-receipt.json')) + list((root / 'preflight').glob('*-receipt.json'))
    for path in pending_paths:
        row = study.load(path)
        if row.get('call_id') not in recorded_ids:
            model_costs.append({'kind': 'model', 'stage': 'unfinalized_call', **row})
            if row.get('call_id'): recorded_ids.add(row['call_id'])
    calls = []
    for row in model_costs:
        call = {k: v for k, v in row.items() if k not in ['raw_response', 'stderr_tail']}
        if row.get('provider') == 'claude' and row.get('raw_response'):
            usage = providers.claude_usage(row['raw_response'])
            if usage:
                call['usage'] = usage
                call['usage_source'] = 'Saved raw Claude receipt; include reported thinking and cache-duration fields.'
        calls.append(call)
    runs, trials, candidate_rows = [], [], []
    useful_model_proposals = 0
    for path in sorted((root / 'runs').glob('*/run.json')):
        run = study.load(path)
        bits, rid = run['bits'], run['id']
        weights = contract['calibration'][str(bits)]['weights']
        baselines = {}
        for bid, baseline in run['baselines'].items():
            before, after = baseline['starting'], baseline['selected']
            rows = [r for r in audit.get('rows', []) if r['run'] == rid and r['baseline'] == bid]
            baselines[bid] = {'starting_S': before['result']['S'], 'selected_S': after['result']['S'],
                'selected_Q': after['result']['Q'], 'selected_T': after['result']['T'], 'weight': weights[bid],
                'gain_percent': 100 * max(0, 1 - after['result']['S'] / before['result']['S']),
                'selected_id': after['id'], 'starting_recipe': before['recipe'], 'selected_recipe': after['recipe'],
                'audit': rows[0] if rows else None}
            for entry in baseline['archive'].values():
                result = entry['result']
                candidate_rows.append({'run': rid, 'bits': bits, 'baseline': bid, 'id': entry['id'],
                    'parent_id': entry['parent_id'], 'recipe': entry['recipe'],
                    'rule': (entry.get('rule_history') or {}).get('rule_id'),
                    'Q': result['Q'], 'T': result['T'], 'S': result['S'],
                    'failed_inputs': result['failed_inputs'], 'pairs': result['pairs'], 'stage': result['stage'],
                    'first_failures': result.get('first_failures',[])[:2],
                    'rule_name': _load(root/'rules/cards'/((entry.get('rule_history') or {}).get('rule_id','baseline')+'.json'),{}).get('name'),
                    'audit': {k:v for k,v in audit.get('receipts',{}).get(entry['id'],{}).items()
                              if k in ['Q','T','S','failed_inputs','pairs','epsilon_upper','eligible']},
                    'receipt': result['evaluation_directory'] + '/receipt.json',
                    'circuit_directory': result['circuit_directory'], 'inputs_file': result['inputs_file']})
        counts = {}
        for trial in run['trials']:
            counts[trial['status']] = counts.get(trial['status'], 0) + 1
            trials.append({'run': rid, 'bits': bits, 'strategy': run['strategy'], 'seed': run['seed'], **trial})
            if trial.get('origin') == 'model' and trial['status'] in ['long_pass', 'improvement']:
                archive = run['baselines'][trial['baseline']]['archive']
                child, parent = archive.get(trial.get('id')), archive.get(trial.get('parent_id'))
                if child and parent and (child['result']['S'] < parent['result']['S'] or parent['result']['failed_inputs']):
                    useful_model_proposals += 1
        audited = [b['audit'] for b in baselines.values() if b['audit']]
        denominator = sum(weights.values())
        runs.append({'id': rid, 'bits': bits, 'strategy': run['strategy'], 'seed': run['seed'],
            'format': run['format'], 'status': run['status'], 'baselines': baselines,
            'proposals': len(run['trials']), 'counts': counts, 'trace': run['trace'],
            'wall_seconds': run.get('wall_seconds'), 'model_calls': len(run['model_calls']),
            'call_records': [{k: v for k, v in r.items() if k not in ['raw_response', 'stderr_tail']} for r in run['model_calls']],
            'development_reward': sum(b['weight'] * b['gain_percent'] for b in baselines.values()) / denominator,
            'audit_reward': sum(r['weight'] * r['gain_percent'] for r in audited) / denominator if audited else None,
            'eligible_baselines': sum(r['eligible'] for r in audited) if audited else None,
            'result_file': str(path.relative_to(root))})
    unknown = sum(not r.get('usage') or any((r['usage'].get(k) is None or k in r['usage'].get('incomplete_fields', []))
                  for k in ['input_tokens', 'output_tokens']) for r in calls)
    tokens = {key: _known_sum(calls, key) for key in ['input_tokens', 'output_tokens', 'cached_input_tokens',
        'cache_creation_input_tokens', 'cache_creation_1h_input_tokens', 'cache_creation_5m_input_tokens', 'reasoning_tokens']}
    tokens.update(calls=len(calls), failed_calls=sum(r.get('status') in ['failed','interrupted'] for r in calls),
                  unfinished_calls=sum(r.get('status')=='started' for r in calls),
                  unknown_usage_calls=unknown, model_wall_seconds=sum(r.get('wall_seconds', 0) for r in calls),
                  provider_cost_estimate_usd=_known_sum(calls, 'provider_cost_estimate_usd'),
                  cost_unknown_calls=sum((r.get('usage') or {}).get('provider_cost_estimate_usd') is None for r in calls))
    tokens['useful_model_proposals'] = useful_model_proposals
    tokens['known_tokens_per_useful_proposal'] = (tokens['input_tokens'] + tokens['output_tokens']) / useful_model_proposals if useful_model_proposals and not unknown else None
    evaluations = [r for r in costs if r['kind'] == 'evaluation']
    return {'schema': 1, 'name': root.name, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'presentation_source_sha256': study.source_hashes(),
        'status': state['status'], 'state': state, 'configuration': config, 'contract': contract,
        'scope': contract['claim_limit'], 'runs': runs, 'trials': trials, 'candidates': candidate_rows,
        'calls': calls, 'tokens': tokens, 'audit_complete': bool(audit),
        'audit': {'epsilon': audit.get('epsilon'), 'multiplicity': audit.get('multiplicity'),
                  'eligible_circuits': sum(r.get('eligible', False) for r in audit.get('receipts', {}).values()),
                  'tested_circuits': len(audit.get('receipts', {}))},
        'calibration': contract.get('calibration', {}),
        'verification': _load(root / 'verification/receipt.json', None),
        'transfer': _load(root / 'transfer/results.json', None),
        'phases': [{'name': p.stem, **study.load(p)} for p in sorted((root / 'phases').glob('*.json'))],
        'evaluation_cost': {'calls': len(evaluations), 'wall_seconds': sum(r.get('wall_seconds', 0) for r in evaluations),
                            'known_cpu_seconds': sum(r.get('cpu_seconds', 0) or 0 for r in evaluations)},
        'evidence': {'score': 'S = Q × round-half-up(T). Lower is better.',
            'independence': 'Fresh inputs use the same fixed checker. Local quantum tests cover selected small implementations.',
            'curve': 'Best long-development score after each proposal. Invalid and duplicate proposals consume budget.',
            'tokens': 'Cache counts are included in input. Reasoning counts, when reported, are included in output. Missing usage stays unknown.',
            'sampling': 'Repeated search seeds measure search variability. Input counts measure circuit testing.'}}


def _csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: engine.canonical(v) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def write(root):
    root = Path(root); data = collect(root)
    study.save(root / 'snapshot.json', data)
    _csv(root / 'snapshot/runs.csv', [{k: v for k, v in r.items() if k not in ['trace', 'baselines', 'call_records']} for r in data['runs']])
    _csv(root / 'snapshot/trials.csv', data['trials'])
    _csv(root / 'snapshot/candidates.csv', data['candidates'])
    _csv(root / 'snapshot/model-calls.csv', data['calls'])
    template = (Path(__file__).parent / 'data/dashboard.html').read_text()
    payload = json.dumps(data, ensure_ascii=True, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    html = template.replace('__EXPERIMENT_DATA__', payload)
    (root / 'dashboard.html').write_text(html)
    return data


def render_figures(root):
    """Export the measured search curve in publication-friendly file formats."""
    import os
    import tempfile
    os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'circuitopt-mpl-cache'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    data = collect(root); out = Path(root) / 'figures'; out.mkdir(exist_ok=True)
    colors = {'random': '#ac791e', 'evolution': '#187f73', 'llm': '#2563b9'}
    names = {'random': 'Random mutation', 'evolution': 'Evolution', 'llm': 'Evolution + model'}
    for bits in data['contract']['bits']:
        selected = [r for r in data['runs'] if r['bits'] == bits and r['format'] == 'compact_json']
        if not selected: continue
        fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.8), sharey=True)
        for ax, bid in zip(axes, ['inverse', 'replay', 'width']):
            for strategy in ['random', 'evolution', 'llm']:
                group = [r for r in selected if r['strategy'] == strategy]
                if not group: continue
                traces = [[p for p in r['trace'] if p['baseline'] == bid] for r in group]
                end = min(max(p['trial'] for p in trace) for trace in traces)
                values = []
                for trace in traces:
                    by_trial = {p['trial']: p['best_S'] for p in trace}
                    last = by_trial[0]; curve = []
                    for trial in range(end + 1):
                        last = by_trial.get(trial, last); curve.append(last / by_trial[0])
                    values.append(curve)
                array = np.array(values); x = np.arange(end + 1)
                median = np.median(array, axis=0)
                ax.step(x, median, where='post', color=colors[strategy], lw=2.2, label=names[strategy])
                ax.fill_between(x, array.min(0), array.max(0), step='post', color=colors[strategy], alpha=.10)
                changed = [0] + [i for i in range(1, len(median)) if median[i] < median[i - 1]]
                ax.scatter(changed, median[changed], color=colors[strategy], s=13, zorder=3)
            title={'inverse':'Inverse and multiply','replay':'Record and replay','width':'Replay with width bounds'}[bid]
            ax.set(title=title, xlabel='Proposal number', ylim=(0, 1.05))
            ax.grid(alpha=.16); ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
        axes[0].set_ylabel('Best development S / starting S')
        axes[-1].legend(fontsize=8, loc='upper right')
        fig.suptitle(f'{bits}-bit division: measured search progress', fontsize=14)
        fig.text(.5, -.015, 'Trial 0 is the baseline. Lines show seed medians; bands show seed ranges. Final audit results are separate.', ha='center', fontsize=9)
        fig.tight_layout()
        for suffix in ['png', 'pdf', 'svg']:
            fig.savefig(out / f'autoresearch-{bits}.{suffix}', dpi=180, bbox_inches='tight')
        plt.close(fig)
