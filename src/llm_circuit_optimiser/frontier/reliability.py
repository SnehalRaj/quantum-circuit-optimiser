"""Rank parent evidence without predicting qualification for untested children."""
import math
import re

from .registry import binomial_upper, digest


def rank_parent_reliability(records, *, audit_evidence=None, alpha=.05/32, epsilon=.001):
    """Use joint input failures from checked receipts, then note-based screen estimates.

    audit_evidence maps a submission ID to source_commit and result. The result
    is an audit receipt with a frontier-verdict-validation-v1 row_validation.
    Callers must check the receipt's source and artifact hashes before passing it.
    """
    if any(type(x) not in (int, float) or not math.isfinite(x) or not 0 < x < 1 for x in (alpha, epsilon)):
        raise ValueError('The audit allowance and failure limit must lie between zero and one')
    evidence = audit_evidence or {}
    if set(evidence) - {record['submission_id'] for record in records}:
        raise ValueError('Audit evidence names an unknown parent submission')
    ranked = []
    for record in records:
        identifier = record['submission_id']
        entry = {'submission_id': identifier, 'commit': record['commit'],
                 'measured_input_bound': None, 'note_screen_pass_approximation': None,
                 'future_audit_feasibility': 'unknown',
                 'scope': 'Parent evidence only. Every proposed child still needs its own development check and fresh audit.'}
        notes = [rate for rate in record.get('reliability', [])
                 if rate.get('kind') == 'note_reported_rate' and rate.get('packet_shots') == 9024
                 and rate.get('pass_chance_poisson_approximation') is not None]
        if notes:
            chosen = min(notes, key=lambda rate: rate['pass_chance_poisson_approximation'][0])
            entry['note_screen_pass_approximation'] = {
                'range': chosen['pass_chance_poisson_approximation'], 'source': chosen['source'],
                'lambda_range': chosen['lambda_range'], 'packet_shots': 9024,
                'scope': 'Unverified note rate; Poisson approximation exp(-lambda). No joint-input audit rate is inferred.'}
        if identifier in evidence:
            supplied = evidence[identifier]
            if supplied.get('source_commit') != record['commit']:
                raise ValueError('The audit evidence names a different source commit')
            receipt = supplied['result']
            validation = receipt.get('row_validation', {})
            pairs, streams, failed = (receipt.get(key) for key in ('pairs', 'streams', 'failed_inputs'))
            operation = receipt.get('operation_sha256')
            if (validation.get('schema') != 'frontier-verdict-validation-v1' or validation.get('complete') is not True
                    or type(pairs) is not int or pairs <= 0 or type(streams) is not int or streams != 2
                    or validation.get('pairs') != pairs or validation.get('streams') != streams
                    or validation.get('rows') != pairs*streams or validation.get('failed_inputs') != failed
                    or not isinstance(operation, str) or not re.fullmatch(r'[0-9a-f]{64}', operation)
                    or validation.get('ops_sha256') != operation):
                raise ValueError('Audit ranking needs complete joint-input counts and matching operation bytes')
            upper = binomial_upper(failed, pairs, alpha)
            entry['measured_input_bound'] = {
                'failed_inputs': failed, 'pairs': pairs, 'streams': streams,
                'failure_unit': 'input pair with a failure in either measurement stream',
                'alpha': alpha, 'upper_bound': upper, 'epsilon': epsilon, 'meets_limit': upper <= epsilon,
                'operation_sha256': operation, 'evidence_sha256': digest(supplied),
                'scope': 'Exact one-sided binomial bound on the supplied historical input counts. Streams are not independent trials.'}
        ranked.append(entry)
    def order(entry):
        measured = entry['measured_input_bound']
        note = entry['note_screen_pass_approximation']
        if measured is not None:
            return (0 if measured['meets_limit'] else 3, measured['upper_bound'], entry['submission_id'])
        return (1, -note['range'][0], entry['submission_id']) if note is not None else (2, 0, entry['submission_id'])
    ranked.sort(key=order)
    for rank, entry in enumerate(ranked, 1):
        entry['rank'] = rank
        entry['rank_policy'] = 'Historical bounds below the limit, then note-based screen estimates, then unknown evidence, then bounds above the limit.'
    return ranked
