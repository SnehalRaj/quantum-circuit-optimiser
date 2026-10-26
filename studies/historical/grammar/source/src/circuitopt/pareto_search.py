"""Retain provisional Q/T tradeoffs before independent qualification.

Rows use top-level Q (positive integer qubit count), T (positive finite mean
executed Toffolis), and ops_sha256 (operation-file identity). No rounding enters
dominance. Equal coordinates can have different operation streams.

Selection uses family, with source and backend as fallbacks. q_region can name
an explicit region. Its default is Q // 64. Each group retains its Q/T frontier
and prioritizes one circuit with the lowest observed development failure rate.
Equal rates use Q, T, then hash. Counts can use failed_inputs/pairs,
development_failed_inputs/development_pairs, or a nested development row.
Only complete valid integer counts supply a rate. Unknown rates keep the old
Q/T ordering. Selection covers each group before adding another group member.
A small limit can truncate coverage. Every selection remains provisional.

Duplicate streams retain available development evidence without adding counts.
Known failure counts and pair counts must agree exactly. Cohort metadata uses
label, packet_id, packet_sha256, inputs_sha256, streams, protocol, or cohort.
Admission fields use the development_ prefix; nested development rows use plain
names. Conflicting known cohort or grouping metadata rejects the duplicates.
"""
from copy import deepcopy
from fractions import Fraction
import math


def _point(q, t):
    if type(q) is not int or q <= 0:
        raise ValueError('Q must be a positive integer')
    if type(t) not in (int, float) or not math.isfinite(t) or t <= 0:
        raise ValueError('T must be positive and finite')
    return q, t


def _dominates(left, right):
    return (left['Q'] <= right['Q'] and left['T'] <= right['T']
            and (left['Q'] < right['Q'] or left['T'] < right['T']))


def _unique(rows):
    result = {}
    for row in rows:
        _point(row['Q'], row['T'])
        identity = row['ops_sha256']
        if not isinstance(identity, str) or not identity:
            raise ValueError('Each search row needs an operation hash')
        previous = result.get(identity)
        if previous is not None and (previous['Q'], previous['T']) != (row['Q'], row['T']):
            raise ValueError('One operation hash has conflicting resource counts')
        current = deepcopy(row)
        evidence = _development_evidence(current)
        if previous is not None:
            for key in ('family', 'source', 'backend', 'q_region'):
                if key in previous and key in current and previous[key] != current[key]:
                    raise ValueError('One operation hash has conflicting group metadata: ' + key)
                if key in current:
                    previous[key] = current[key]
            evidence = _merge_evidence(_development_evidence(previous), evidence)
            current = previous
        if evidence is not None:
            nested = current.get('development')
            current['development'] = {**(nested if isinstance(nested, dict) else {}), **evidence}
        result[identity] = current
    return list(result.values())


def pareto_rows(rows):
    """Return each distinct undominated stream, ordered by Q, T, then hash."""
    unique = _unique(rows)
    return sorted((row for row in unique
                   if not any(_dominates(other, row) for other in unique)),
                  key=lambda row: (row['Q'], row['T'], row['ops_sha256']))


def classify_point(q, t, public_rows):
    """Compare unrounded counts with public coordinates; assign no qualification."""
    _point(q, t)
    public = list(public_rows)
    for row in public:
        _point(row['Q'], row['T'])
    point = {'Q': q, 'T': t}
    dominators = [row for row in public if _dominates(row, point)]
    equal = any((q, t) == (row['Q'], row['T']) for row in public)
    status = 'dominated' if dominators else 'equal' if equal else 'extends'
    return {'pareto': status, 'qualified': False,
            'dominating_coordinates': [{'Q': row['Q'], 'T': row['T']} for row in dominators]}


def _merge_evidence(left, right):
    if left is None:
        return right
    if right is None:
        return left
    if ('failed_inputs' in left and 'failed_inputs' in right
            and (left['failed_inputs'], left['pairs']) != (right['failed_inputs'], right['pairs'])):
        raise ValueError('One operation hash has conflicting development counts')
    for key in left.keys() & right.keys() - {'failed_inputs', 'pairs'}:
        if left[key] != right[key]:
            raise ValueError('One operation hash has conflicting development cohort: ' + key)
    return {**left, **right}


def _development_evidence(row):
    nested = row.get('development')
    nested = nested if isinstance(nested, dict) else {}
    evidence = None
    for values, prefix in ((row, ''), (row, 'development_'), (nested, '')):
        observed = {key: values[prefix + key]
                    for key in ('label', 'packet_id', 'packet_sha256', 'inputs_sha256',
                                'streams', 'protocol', 'cohort')
                    if values.get(prefix + key) is not None}
        failures, pairs = values.get(prefix + 'failed_inputs'), values.get(prefix + 'pairs')
        if type(failures) is int and type(pairs) is int and 0 <= failures <= pairs and pairs > 0:
            observed.update(failed_inputs=failures, pairs=pairs)
        if observed:
            evidence = _merge_evidence(evidence, observed)
    return evidence


def _development_rate(row):
    evidence = _development_evidence(row)
    if evidence is None or 'failed_inputs' not in evidence:
        return None
    return Fraction(evidence['failed_inputs'], evidence['pairs'])


def select_finalists(rows, limit):
    """Retain each group's Q/T frontier and its development reliability champion."""
    if type(limit) is not int or limit < 0:
        raise ValueError('The finalist limit must be a nonnegative integer')
    groups = {}
    for row in _unique(rows):
        family = row.get('family', row.get('source', row.get('backend', 'unspecified')))
        key = (str(family), str(row.get('q_region', row['Q'] // 64)))
        groups.setdefault(key, []).append(row)
    queues = []
    for _, group in sorted(groups.items()):
        queue = pareto_rows(group)
        known = [row for row in group if _development_rate(row) is not None]
        if known:
            champion = min(known, key=lambda row: (_development_rate(row), row['Q'], row['T'], row['ops_sha256']))
            queue = [champion] + [row for row in queue if row['ops_sha256'] != champion['ops_sha256']]
        queues.append(queue)
    queues.sort(key=lambda queue: (queue[0]['Q'], queue[0]['T'], queue[0]['ops_sha256']))
    selected = []
    while queues and len(selected) < limit:
        for queue in queues:
            if len(selected) == limit:
                break
            row = queue.pop(0)
            selected.append({**row, 'qualification_status': 'provisional'})
        queues = [queue for queue in queues if queue]
    return selected
