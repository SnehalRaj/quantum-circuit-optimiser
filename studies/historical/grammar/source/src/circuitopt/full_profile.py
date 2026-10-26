"""Collect measured native work and model packet usage without new model calls."""
import argparse
import json
from pathlib import Path


def profile(root, project):
    root, project = Path(root), Path(project)
    def process_total(paths):
        rows = [json.loads(p.read_text()) for p in paths]
        return {'receipts': len(rows), 'wall_seconds': sum(r.get('wall_seconds', 0) for r in rows),
                'cpu_seconds': sum(r.get('cpu_seconds', 0) for r in rows),
                'failed': sum(r.get('returncode') not in [0, 1] for r in rows)}
    setup = [json.loads(p.read_text()) for p in (project / 'native/logs').glob('*.json')]
    recorded_builds = [r for r in setup if 'elapsed_seconds' in r]
    results = json.loads((root / 'search-results.json').read_text())
    costs = {'emission': process_total(root.glob('circuits/*/process.json')),
             'development': process_total(root.glob('evaluations/development-*/process.json')),
             'independent_long': process_total(root.glob('evaluations/long-*/process.json')),
             'independent_final': process_total(root.glob('evaluations/final-*/process.json')),
             'official_check': process_total(root.glob('official/official-attempt-*.json')),
             'failed_emission_attempts': process_total(root.glob('emission-errors/*/process.json')),
             'search_total_wall_seconds': sum(r['wall_seconds'] for r in results),
             'recorded_setup_wall_seconds': sum(r['elapsed_seconds'] for r in recorded_builds),
             'recorded_setup_receipts': len(recorded_builds),
             'setup_coverage': 'Latest native build and verification receipts. Earlier incremental implementation builds were not all retained.',
             'research_setup': 'Parent-agent and source-review work has no provider usage meter in this snapshot.'}
    packets = []
    model_wall = 0
    for run in results:
        for call in run['model_calls']:
            trial = call['trial']
            directory = root / 'searches' / f'{run["strategy"]}-{run["seed"]}' / 'model' / str(trial)
            packet = json.loads((directory / 'packet.json').read_text())
            compact = (directory / 'prompt.txt').read_bytes()
            pretty = json.dumps(packet, indent=2).encode()
            receipt = json.loads((directory / 'receipt.json').read_text())
            usage = receipt.get('usage') or {}
            model_wall += receipt['wall_seconds']
            packets.append({'seed': run['seed'], 'review_at_proposal': trial,
                            'compact_packet_bytes': len(compact), 'pretty_packet_bytes': len(pretty),
                            'byte_reduction_percent': 100 * (1 - len(compact) / len(pretty)),
                            'reported_input_tokens': usage.get('input_tokens'),
                            'reported_output_tokens': usage.get('output_tokens'),
                            'reported_cached_input_tokens': usage.get('cached_input_tokens'),
                            'reported_reasoning_tokens': usage.get('reasoning_tokens'),
                            'wall_seconds': receipt['wall_seconds'],
                            'after_first_reference_cost_hit': run['first_hits']['cost'] < trial
                                if run['first_hits']['cost'] is not None else False})
    costs['model_wall_seconds_included_in_search'] = model_wall
    costs['search_wall_excluding_model_seconds'] = max(0, costs['search_total_wall_seconds'] - model_wall)
    costs['model_proposals'] = sum(t['origin'] == 'model' for r in results for t in r['trials'])
    costs['model_origin_improvements'] = sum(t['origin'] == 'model' and t['status'] == 'improvement'
                                            for r in results for t in r['trials'])
    costs['calls_after_reference_cost_hit'] = sum(p['after_first_reference_cost_hit'] for p in packets)
    report = {'costs': costs, 'packets': packets,
              'format_scope': 'Byte comparison of the same packets. All actual calls used compact JSON and structured output. No provider-token A/B format claim.',
              'billing_scope': 'Reasoning is included in output. Cache categories are included in input. Provider prices are estimates.'}
    (root / 'profiling.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = profile(args.root, args.project)
    print(json.dumps(result['costs'], indent=2))
