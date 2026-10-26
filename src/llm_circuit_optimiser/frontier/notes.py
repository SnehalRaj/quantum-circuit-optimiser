"""Render submission notes from recorded edits, measurements, and credits."""
import json
from .registry import checked_hash

ROLES = {'parent', 'lever', 'catalog', 'setting', 'dial_source', 'integrated_base', 'source', 'other'}


def dependencies_for_submission(job):
    """Keep each role in the rationale and preserve the evidence text."""
    result = []
    for source in job.get('dependencies', []):
        role = source.get('role')
        if role not in ROLES:
            raise ValueError('Unknown dependency role: ' + str(role))
        for key in ('submission_id', 'rationale', 'evidence'):
            if not isinstance(source.get(key), str) or not source[key].strip():
                raise ValueError('Each dependency needs ' + key)
        result.append({'submissionId': source['submission_id'],
                       'rationale': role + ': ' + source['rationale'], 'evidence': source['evidence']})
    if not any(d.get('role') == 'parent' for d in job.get('dependencies', [])):
        raise ValueError('The note needs a direct parent dependency')
    return result


def _result(receipts, stage):
    value = receipts[stage]
    return value.get('result', value)


def _json(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)


def render_note(job, receipts, disclosure, *, model, harness, credits,
                inherited_contract='', reproduction=None, limitations=None):
    """Use recorded facts. Never pad a note to meet a size requirement."""
    if not all(isinstance(v, str) and v.strip() for v in (model, harness)):
        raise ValueError('Name the actual model mode and harness')
    if not credits or any(not isinstance(v, str) or not v.strip() for v in credits):
        raise ValueError('The note needs source credits')
    dependencies = dependencies_for_submission(job)
    material = _result(receipts, 'materialize')
    emission = _result(receipts, 'emit')
    screen = _result(receipts, 'screen')
    audit = _result(receipts, 'audit')
    operation = checked_hash(emission['operation_sha256'])
    if screen.get('operation_sha256') != operation or audit.get('operation_sha256') != operation:
        raise ValueError('The note measurements use different operation bytes')
    if screen.get('passed') is not True or audit.get('qualified') is not True:
        raise ValueError('A submission note needs a passing screen and qualified audit')
    q, t = screen['Q'], screen['T']
    if type(q) is not int or type(t) is not int or min(q, t) <= 0:
        raise ValueError('Use positive integer public Q and T counts')
    if screen.get('score', q*t) != q*t:
        raise ValueError('The recorded score differs from Q times T')
    edit_evidence = material.get('edit_receipts', material.get('edits', []))
    copy_evidence = material.get('copy_receipts', material.get('file_copies', []))
    if job.get('edits') and not edit_evidence:
        raise ValueError('The materialization receipt lacks edit evidence')
    if job.get('file_copies') and not copy_evidence:
        raise ValueError('The materialization receipt lacks copied-file evidence')
    completed = [r for r in disclosure if r.get('status') == 'complete']
    failed = [r for r in completed if not r.get('result', {}).get('passed')]
    incomplete = len(disclosure)-len(completed)
    lines = [f"# Q{q} point addition with T {t:,}", '',
             f"**Q:** {q}. **T:** {t:,}. **Q × T:** {q*t:,}.", '',
             f"**Model or proposal mode:** {model}. **Harness:** {harness}.", '',
             '## What changed', '',
             f"The base commit is `{job['base_commit']}`. The job is `{job['id']}`.",
             'The source manifest records each file before and after the edits.', '',
             '### Ordered edits and copied files', '', '```json',
             _json({'edits': edit_evidence, 'file_copies': copy_evidence}), '```', '',
             '### Source identities', '', '```json',
             _json({key: material[key] for key in ['base_commit', 'resolved_commit', 'base_tree_sha256', 'recipe_sha256', 'source_hashes', 'grammar_binding'] if key in material}),
             '```', '', '## Measured results', '',
             '| Measurement | Value |', '|:--|:--|',
             f"| Operation SHA-256 | `{operation}` |",
             f"| Public screen | {screen['shots']:,} shots |",
             f"| Classical mismatches | {screen['counts']['classical_mismatches']} |",
             f"| Phase-garbage batches | {screen['counts']['phase_garbage_batches']} |",
             f"| Ancilla-garbage batches | {screen['counts']['ancilla_garbage_batches']} |",
             f"| Independent audit | {audit['pairs']:,} input pairs × {audit['streams']} streams |",
             f"| Audit failed inputs | {audit['failed_inputs']} of {audit['pairs']:,} |",
             f"| One-sided audit allowance | {audit['alpha']:.10g} |",
             f"| Exact upper bound per input | {audit['upper_bound']:.12g} |",
             '| Failure limit per input | 0.001 |', '',
             'The public screen uses the fixed packet for these operation bytes.',
             'The audit uses the label reserved before execution and counts a failure in either stream as a failed input.', '',
             '## Disclosure: variants screened in this region', '',
             f"The registry records {len(disclosure)} distinct operation hashes: {len(completed)} completed screens, {len(failed)} failed screens, and {incomplete} unresolved attempts.",
             'Each distinct circuit receives at most one public screen. An interrupted attempt remains in the registry.', '',
             '| Job | Operation SHA-256 | Status | Classical | Phase garbage | Scratch garbage |',
             '|:--|:--|:--|--:|--:|--:|']
    for row in disclosure:
        result = row.get('result', {})
        counts = result.get('counts', {})
        status = ('pass' if result.get('passed') else 'fail') if row['status'] == 'complete' else row['status']
        values = [str(counts.get(k, 'unresolved')) for k in ('classical_mismatches', 'phase_garbage_batches', 'ancilla_garbage_batches')]
        lines.append(f"| `{row['job_id']}` | `{row['operation_sha256']}` | {status} | " + ' | '.join(values) + ' |')
    lines += ['', '## Reproduce', '']
    steps = reproduction or [f"Check out base commit `{job['base_commit']}`.",
                             'Apply the ordered edits and copy the files whose hashes appear above.',
                             'Build with the recorded toolchain and locked dependencies.',
                             'Emit the operation stream and compare its SHA-256 with the recorded value.',
                             'Use a separate reproduction record when rerunning any evaluation.']
    lines += [f'{i}. {step}' for i, step in enumerate(steps, 1)]
    lines += ['', '## Prior work and credit', ''] + ['- ' + c for c in credits]
    lines += ['', '### Dependencies by role', '', '```json', _json(job['dependencies']), '```', '',
              '## Limitations', '',
              'The audit bounds failure under its declared input distribution and measurement protocol.',
              'A paired development check gives finite agreement evidence. It does not prove general circuit equivalence.']
    if limitations:
        lines += [''] + ['- ' + item for item in limitations]
    if inherited_contract:
        lines += ['', '## Inherited limiting contract', '', inherited_contract.rstrip()]
    return '\n'.join(lines) + '\n'


def validate_note_size(note, *, minimum_bytes=5120):
    size = len(note.encode('utf-8'))
    if size < minimum_bytes:
        raise ValueError(f'The note has {size} bytes; add missing source or measurement details to reach {minimum_bytes}')
    return size
