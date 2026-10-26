"""Read public results and compute the resource frontier without circuit calls."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

from ..common import save


SCORE_REJECTION = 'score did not improve current best'


def positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def eligible(row):
    """Admit positive resource results accepted by the leaderboard evaluator."""
    if not isinstance(row, dict):
        return False
    metrics = row.get('officialMetrics')
    if not isinstance(metrics, dict):
        return False
    status_ok = row.get('status') == 'accepted' or (
        row.get('status') == 'rejected' and row.get('rejectionReason') == SCORE_REJECTION)
    return status_ok and positive_number(row.get('officialScore')) and all(
        positive_number(metrics.get(name)) for name in ('qubits', 'toffoli'))


def select_frontier(rows):
    """Return eligible rows, distinct frontier coordinates, and all tied rows."""
    admitted = [row for row in rows if eligible(row)]
    coordinates = sorted({(r['officialMetrics']['qubits'], r['officialMetrics']['toffoli']) for r in admitted})
    frontier = []
    lowest = math.inf
    for q, t in coordinates:
        if t < lowest:
            frontier.append((q, t))
            lowest = t
    selected = set(frontier)
    return admitted, frontier, [r for r in admitted if (
        r['officialMetrics']['qubits'], r['officialMetrics']['toffoli']) in selected]


def _rows(value):
    rows = value.get('curve') if isinstance(value, dict) else value
    if not isinstance(rows, (list, tuple)):
        raise ValueError('A curve must contain a list of points')
    for row in rows:
        if not isinstance(row, dict) or not all(positive_number(row.get(k)) for k in ('Q', 'T')):
            raise ValueError('Curve points require finite positive Q and T')
    return rows


def best_at_or_below(curve, q):
    """Return the lowest-T eligible curve point with width at most q."""
    if not positive_number(q):
        raise ValueError('Q must be finite and positive')
    rows = [r for r in _rows(curve) if r['Q'] <= q]
    return min(rows, key=lambda r: (r['T'], r['Q'], r.get('id', ''))) if rows else None


def is_dominated(curve, q, t):
    if not positive_number(t):
        raise ValueError('T must be finite and positive')
    best = best_at_or_below(curve, q)
    return best is not None and best['T'] <= t


def gaps(curve, lower=None, upper=None):
    """List integer widths missing from the curve, within inclusive bounds."""
    rows = _rows(curve)
    if not rows and (lower is None or upper is None):
        return []
    lower = min(r['Q'] for r in rows) if lower is None else lower
    upper = max(r['Q'] for r in rows) if upper is None else upper
    if type(lower) is not int or type(upper) is not int or lower <= 0 or upper < lower:
        raise ValueError('Gap bounds must be positive ordered integers')
    held = {r['Q'] for r in rows}
    return [q for q in range(lower, upper + 1) if q not in held]


def build_snapshot(public, *, owner='SnehalRaj'):
    """Build a curve with deterministic representatives for tied coordinates."""
    rows = public.get('submissions') if isinstance(public, dict) else public
    if not isinstance(rows, list):
        raise ValueError('The public response must contain a submissions list')
    admitted, coordinates, _ = select_frontier(rows)
    ordered = sorted(admitted, key=lambda r: (
        r['officialMetrics']['qubits'], r['officialMetrics']['toffoli'],
        r.get('createdAt') or '', r.get('id') or ''))
    representatives = {}
    for row in ordered:
        key = (row['officialMetrics']['qubits'], row['officialMetrics']['toffoli'])
        representatives.setdefault(key, row)
    curve = [{'id': representatives[(q, t)]['id'], 'Q': q, 'T': t,
              'user': representatives[(q, t)].get('solverUsername', ''),
              'created': representatives[(q, t)].get('createdAt', '')}
             for q, t in coordinates]
    selected_ids = {r['id'] for r in curve}
    mine = []
    for row in ordered:
        if row.get('solverUsername') != owner:
            continue
        q, t = row['officialMetrics']['qubits'], row['officialMetrics']['toffoli']
        dominators = [r for r in curve if r['id'] != row['id'] and r['Q'] <= q and r['T'] <= t]
        dom = max(dominators, key=lambda r: (r['Q'], -r['T'])) if dominators else None
        created = row.get('createdAt', '')
        state = 'on curve' if row['id'] in selected_ids else (
            'superseded' if dom and dom['created'] > created else 'never on curve')
        mine.append({'id': row['id'][:8], 'Q': q, 'T': t, 'S': row['officialScore'],
                     'created': created[:16], 'state': state,
                     'by': (f"{dom['id'][:8]} {dom['user']} Q{dom['Q']} T{dom['T']} {dom['created'][5:16]}"
                            if dom else '')})
    return {'curve': curve, 'mine': mine}


def diff_snapshots(previous, current):
    """Compare coordinates and contributor membership between two snapshots."""
    previous = previous or {'curve': [], 'mine': []}
    old = {(r['Q'], r['T']): r for r in _rows(previous)}
    new = {(r['Q'], r['T']): r for r in _rows(current)}
    old_mine = {r['id']: r for r in previous.get('mine', []) if r['state'] == 'on curve'}
    new_mine = {r['id']: r for r in current.get('mine', []) if r['state'] == 'on curve'}
    return {'previous_count': len(old), 'current_count': len(new),
            'added': [new[k] for k in sorted(new.keys() - old.keys())],
            'dropped': [old[k] for k in sorted(old.keys() - new.keys())],
            'gained': [new_mine[k] for k in sorted(new_mine.keys() - old_mine.keys())],
            'lost': [old_mine[k] for k in sorted(old_mine.keys() - new_mine.keys())],
            'per_user': dict(sorted(Counter(r.get('user', '') for r in new.values()).items()))}


def sync_public(output=None, *, runner=subprocess.run, executable='ecdsafail', owner='SnehalRaj', now=None):
    """Fetch public metadata and save its original bytes and hash receipt."""
    command = [str(executable), 'submissions', '--all', '--json']
    result = runner(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f'Public sync failed with exit status {result.returncode}: {result.stderr}')
    text = result.stdout
    public = json.loads(text)
    snapshot = build_snapshot(public, owner=owner)
    receipt = {'schema': 'lco-public-sync-v1', 'command': command,
               'utc': now or datetime.now(timezone.utc).isoformat(),
               'sha256': hashlib.sha256(text.encode()).hexdigest(),
               'public_rows': len(public['submissions']),
               'eligible_rows': sum(eligible(r) for r in public['submissions']),
               'curve_coordinates': len(snapshot['curve'])}
    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x') as stream:
            stream.write(text)
        save(output.with_suffix(output.suffix + '.receipt.json'), receipt)
        save(output.with_suffix(output.suffix + '.curve.json'), snapshot)
    return {'public': public, 'snapshot': snapshot, 'receipt': receipt}
