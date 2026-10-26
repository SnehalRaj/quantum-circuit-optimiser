#!/usr/bin/env python3
"""Group every frozen submission and compare resources with its stated parent."""
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
from freeze_snapshot import timestamp

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
NOTE_PARENTS = {
    '11613a51': 'f22e87ab-a08a-4812-ba7e-e4768380158d',
    'aefedb12': 'd0abb28f-bdbb-4ceb-be72-cfcc88c64b47',
    'a42ed949': '8a57279a-f075-4b62-af9f-08c9e5ff0aab',
}


def collect():
    public = json.loads(gzip.decompress((DATA / 'public-submissions.json.gz').read_bytes()))['submissions']
    by_id = {r['id']: r for r in public}
    frontier = json.loads((DATA / 'frontier-summary.json').read_text())
    membership = {r['id']: r for r in frontier['submissions']}
    records = []
    for source in public:
        if source['solverUsername'] != 'SnehalRaj':
            continue
        if timestamp(source['createdAt']) > timestamp(frontier['analysis_cutoff_utc']):
            continue
        note = source.get('note') or ''
        member = membership.get(source['id'], {})
        metrics = source.get('officialMetrics') if member else None
        parent_match = re.search(r'The parent is public submission `([^`]+)`', note)
        reference_kind = 'published parent'
        if source['id'].startswith('06b08594'):
            family, method = 'Leapfrog', 'Approximation tuning after reliability repair'
            parent_id = ''
            parent_commit = '48dcefa9f07af349f1561b345558541313cfc825'
            parent_q, parent_t = 1292, 857382
            reference_kind = 'local repaired control; data/lowest-product-lineage.md'
            assert parent_commit in (DATA / 'lowest-product-lineage.md').read_text()
        else:
            parent_id = parent_match[1] if parent_match else NOTE_PARENTS[source['id'][:8]]
            assert parent_id in note
            parent = by_id[parent_id]
            parent_commit = parent['submissionCommitSha']
            parent_q, parent_t = (parent['officialMetrics'][k] for k in ('qubits', 'toffoli'))
            if source['id'][:8] in NOTE_PARENTS:
                assert 'SKY-COF' in parent['note']
                family, method = 'SKY-COF', 'Exact local gate replacement'
            elif parent_id.startswith('f6a88040'):
                family, method = 'SKY-COF', 'Cofactor and comparison widths with workspace cap'
            else:
                assert 'froghop' in note.lower(), source['id']
                family = 'Froghop'
                if 'GPOOL =' in note:
                    method = 'Carry allocation with exact local gate replacement'
                elif 'POOL_1 =' in note:
                    method = 'Scratch allocation with cleanup and exact local gate replacement'
                elif 'POOL_D =' in note:
                    method = 'Scratch allocation'
                elif source['id'].startswith('b9a40957'):
                    method = 'Cleanup and exact local gate replacement'
                else:
                    method = 'Measurement-based cleanup'
        records.append(dict(
            id=source['id'], commit=source.get('submissionCommitSha') or '', family=family,
            method=method, status=source['status'], current_frontier=member.get('current_pareto', False),
            historical_frontier=member.get('historical_new_point', False),
            Q=metrics['qubits'] if metrics else None, T=metrics['toffoli'] if metrics else None,
            parent_id=parent_id, parent_commit=parent_commit, reference_kind=reference_kind,
            parent_Q=parent_q, parent_T=parent_t,
            Q_reduction=parent_q-metrics['qubits'] if metrics else None,
            T_reduction=parent_t-metrics['toffoli'] if metrics else None,
            note_sha256=hashlib.sha256(note.encode()).hexdigest()))
    groups = []
    for family in ['Froghop', 'SKY-COF', 'Leapfrog']:
        entries = [r for r in records if r['family'] == family]
        measured = [r for r in entries if r['Q'] is not None]
        best_t = max(measured, key=lambda r: r['T_reduction'])
        best_q = max(measured, key=lambda r: r['Q_reduction'])
        groups.append(dict(family=family, submissions=len(entries), measured_commits=len(measured),
                           frontier_points=sum(r['current_frontier'] for r in entries),
                           min_Q=min(r['Q'] for r in measured), max_Q=max(r['Q'] for r in measured),
                           max_T_reduction=best_t['T_reduction'], T_example=best_t['id'],
                           Q_change_at_max_T=-best_t['Q_reduction'],
                           max_Q_reduction=best_q['Q_reduction'], Q_example=best_q['id'],
                           T_change_at_max_Q=-best_q['T_reduction']))
    groups.sort(key=lambda r: -r['max_T_reduction'])
    return dict(snapshot_utc=frontier['analysis_cutoff_utc'],
                comparison='Recorded rounded mean T; separate evaluation inputs. Leapfrog uses the local repaired control.',
                maxima='Separate maxima within each family; they can describe different circuits.',
                records=records, groups=groups)


def main():
    result = collect()
    (DATA / 'own-submission-families.json').write_text(json.dumps(result, indent=2)+'\n')
    with (DATA / 'own-submission-families.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(result['records'][0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(result['records'])
    methods = {'Froghop': 'Cleanup, gate replacement, and workspace allocation',
               'Leapfrog': 'Approximation tuning after reliability repair',
               'SKY-COF': 'Gate replacement and cofactor storage'}
    lines = [r'\begin{tabular}{p{.11\textwidth}p{.30\textwidth}rrrr}', r'\toprule',
             r'Family & Edits & Commits / curve & Q range & Max. T saving & Max. Q saving \\', r'\midrule']
    for group in result['groups']:
        qr = str(group['min_Q']) if group['min_Q'] == group['max_Q'] else f"{group['min_Q']}--{group['max_Q']}"
        lines.append(' & '.join([group['family'], methods[group['family']],
                     f"{group['measured_commits']} / {group['frontier_points']}", qr,
                     f"{group['max_T_reduction']:,}", str(group['max_Q_reduction'])])+r' \\')
    lines += [r'\bottomrule', r'\end{tabular}']
    (DATA / 'own-family-table.tex').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
