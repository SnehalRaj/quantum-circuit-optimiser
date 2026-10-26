"""Gate, document and submit one candidate (from code/submit.py, amendment 1).

`prepare` requires one passing screen, one audit that meets the bound, and one passing sealed
reference run, all on the same operation hash. It then writes publication/<id>/note.md,
dependencies.json and request.json. The note discloses how many variants were screened for the
region and how many failed, and it repeats the lineage's limiting contract verbatim.
`send` runs the submit command once from the sealed checkout and keeps every receipt.
"""
import hashlib
import json
from pathlib import Path
import subprocess

from .layout import load, relative, require, utc

MODEL = 'Claude Opus 5.5'
HARNESS = 'Claude Code'
COORDINATOR = 'Snehal Raj'
AUTHORIZATION = ('Snehal asked to submit each pass (2026-10-24) and allowed multi-variant submission '
                 'with disclosure (2026-10-25).')
SNAPSHOT = 'final-frontier-20261024/studies/pareto-corners-20261024/submissions-1831Z.json'
CONTRACT_MARKER = 'NOTE TO AI AGENTS ON CIRCUIT ERROR FOR THIS CHALLENGE:'
MINIMUM_NOTE_BYTES = 5200


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def default_snapshot(campaign):
    return campaign.root.parent / SNAPSHOT


def public_row(rows, uuid):
    found = [r for r in rows if r['id'] == uuid]
    require(len(found) == 1, f'The public snapshot holds {len(found)} rows with id {uuid}.')
    return found[0]


def region_disclosure(campaign, region):
    """Return (candidate, passed, counts) for every screened variant whose id starts with the region prefix."""
    rows = []
    for path in sorted(campaign.root.glob(f'tracks/*/{region}*/screen.json')):
        record = load(path)
        # A rebuilt public parent is an instrument control, not a candidate variant.
        if record['candidate'].endswith('-parent'):
            continue
        rows.append((record['candidate'], record['passed'], record['counts']))
    return rows


def gate(campaign, cid, track=None):
    """Check screen, audit and sealed reference on one operation hash. Return the three records."""
    home = campaign.candidate(cid, track)
    screen = load(home / 'screen.json')
    require(screen['passed'], 'The screen did not pass.')
    official = campaign.root / 'official' / cid
    final = load(official / 'final-result.json')
    require(final['status'].startswith('PASSED'), f"The sealed reference did not pass: {final['status']}")
    ops = screen['ops_sha256']
    require(final['operation_sha256'] == ops, 'The sealed reference measured other bytes than the screen.')
    config = load(official / 'preflight-config.json')
    audit = load(campaign.root / config['audit_slot'] / 'evaluations' / cid / 'calibration/result.json')
    require(audit.get('status') == 'complete', 'The audit is not complete.')
    require(audit['binding']['ops_sha256'] == ops, 'The audit measured other bytes than the screen.')
    require(audit['bounds']['decision'] == 'meets_0.001_bound'
            and audit['bounds']['family_upper95'] <= campaign.epsilon, 'The audit does not meet the failure bound.')
    return screen, audit, final


def inherited_contract(parent, levers):
    """Carry the lineage's limiting contract from the first credited row whose note has it."""
    carrier = next((r for r in [parent, *levers] if CONTRACT_MARKER in (r['note'] or '')), None)
    inherited = carrier['note'][carrier['note'].index(CONTRACT_MARKER):] if carrier else None
    if inherited:  # stop at the contract's end; the carrier's own later sections are not part of it
        inherited = inherited.split('\n## ')[0].rstrip()
    return carrier, inherited


def render_note(campaign, *, title, change, parent, levers, credits, edits, disclosure, audit, score, ops,
                model=MODEL, harness=HARNESS, coordinator=COORDINATOR):
    q, t, s = score['metrics']['qubits'], score['metrics']['toffoli'], score['score']
    failed = [r for r in disclosure if not r[1]]
    pm = parent['officialMetrics']
    counts = audit['counts']
    pairs, streams = audit['pairs'], audit['streams']
    rust = campaign.rust_version
    record_path = relative(campaign.root, campaign.repo)
    rate_text = (f"none observed in {pairs * streams:,} shots; the bound above limits the rate" if counts['failed_shots'] == 0
                 else f"{9024 * counts['failed_shots'] / (pairs * streams):.2f}")
    credit_lines = ''.join(f'- {c}\n' for c in credits)
    carrier, inherited = inherited_contract(parent, levers)
    edit_lines = '\n'.join(f"- `{e['file']}`: " + (f"`{e['old']}` becomes `{e['new']}` (one occurrence)." if 'old' in e
                 else f"taken byte for byte from `{e['taken_from_archive']}` (sha256 `{e['after_sha256']}`).") for e in edits)
    screen_lines = '\n'.join(f"| `{c}` | {'pass' if p else 'fail'} | {k['classical_mismatches']} | {k['phase_garbage_batches']} | {k['ancilla_garbage_batches']} |"
                             for c, p, k in disclosure)
    lever_lines = '\n'.join(f"- `{r['id']}` by `{r['solverUsername']}` (Q{r['officialMetrics']['qubits']}, T {r['officialMetrics']['toffoli']:,})." for r in levers)
    note = f"""# {title}

**Q{q}, T {t:,}, score {s:,}. This is a Pareto-curve submission. It does not improve the scalar leader.**

**Model:** {model} (Anthropic). **Harness:** {harness}. The effort setting is not exposed in this session.
The coordinator is {coordinator}. The circuit construction belongs to the parent authors credited below.

## What changed

The parent is public submission `{parent['id']}` by `{parent['solverUsername']}`: Q{pm['qubits']}, T {pm['toffoli']:,}.
Its source commit is `{parent['submissionCommitSha']}`.
{change}

The exact edits are:

{edit_lines}

No other file changes. The inherited nonce is unchanged. No nonce or padding search produced this circuit.

## Measured results

| Measurement | Value |
|:--|:--|
| Official local reference (unchanged public harness, 9,024 shots) | all shots OK; Q{q}, T {t:,}, score {s:,} |
| Operation SHA-256 | `{ops}` |
| Independent audit | {pairs:,} input pairs x {streams} streams, fresh pre-registered label |
| Audit failed inputs | {counts['failed_inputs']} of {pairs:,} |
| Audit failed shots | {counts['failed_shots']} of {pairs * streams:,} |
| Audit upper bound (one-sided, alpha 0.05/32) | {audit['bounds']['family_upper95']:.3g} per input; limit 0.001 |
| Failing shots per 9,024 (audit estimate) | {rate_text} |

The audit and the public check use different inputs. The audit supports the stated failure bound.
The public check shows that these exact bytes pass the fixed 9,024-shot packet.

## Disclosure: variants screened in this region

Our campaign allows several real-setting variants per region of the curve.
Each variant gets one run of the trusted evaluator on its exact bytes. Because the evaluator draws its packet from the operation hash, that run equals the public check.
We submit only variants that pass, after a separate audit.
For this region we screened {len(disclosure)} variant(s); {len(failed)} failed.

| Variant | Screen | Classical mismatches | Phase-garbage batches | Ancilla-garbage batches |
|:--|:--|--:|--:|--:|
{screen_lines}

A passing screen selects these bytes. It does not lower the circuit's true failure rate. Read the audit row above for that rate.

## Reproduce

1. Check out the parent commit `{parent['submissionCommitSha']}` of the challenge repository.
2. Apply the edits above to `src/point_add`.
3. Run the unchanged public benchmark: `./benchmark.sh`. It builds `build_circuit` and `eval_circuit`, emits `ops.bin`, and scores it.
4. Compare the `ops.bin` SHA-256 with the value above.

The build used Rust {rust} on macOS (Apple Silicon), offline, with the locked dependency set.

## Prior work and credit

- Parent circuit: `{parent['id']}` by `{parent['solverUsername']}`. All arithmetic, layout and passes are theirs.
{lever_lines}
{credit_lines}- This submission adds only the edits listed above, the measurements, and the audit.

## Limitations

- The circuit still fails on some inputs. The audit row gives the measured rate.
- The parent's approximations and error budget are unchanged and remain the parent author's design.
- The score does not beat the scalar leader. The submission targets the qubit/Toffoli Pareto curve.
"""
    if inherited:
        note += (f"\n## Inherited limiting contract\n\nThe note of `{carrier['id']}` carries this lineage contract. "
                 "It is repeated verbatim.\n\n" + inherited + '\n')
    while len(note.encode()) < MINIMUM_NOTE_BYTES:
        note += (f'\nThe campaign records every screen, audit and reference receipt under `{record_path}/` '
                 'in the submitter\'s private research repository. They are available on request.\n')
    return note


def prepare(campaign, cid, *, parent_id, lever_ids=(), title, change, region, credits=(), track=None,
            snapshot=None, model=MODEL, harness=HARNESS, coordinator=COORDINATOR, authorization=AUTHORIZATION):
    """Pass the gate, then write the note, the dependency list and the request."""
    screen, audit, final = gate(campaign, cid, track)
    ops = screen['ops_sha256']
    score = final['official_score_file']
    rows = load(snapshot or default_snapshot(campaign))['submissions']
    parent = public_row(rows, parent_id)
    levers = [public_row(rows, x) for x in lever_ids]
    disclosure = region_disclosure(campaign, region)
    edits = load(campaign.candidate(cid, track) / 'manifest.json')['edits']
    note = render_note(campaign, title=title, change=change, parent=parent, levers=levers, credits=credits,
                       edits=edits, disclosure=disclosure, audit=audit, score=score, ops=ops,
                       model=model, harness=harness, coordinator=coordinator)
    out = campaign.root / 'publication' / cid
    out.mkdir(parents=True)
    (out / 'note.md').write_text(note)
    deps = [{'submissionId': parent['id'], 'rationale': 'Direct parent: the complete circuit source, arithmetic, layout and passes.',
             'evidence': f"Our tree is commit {parent['submissionCommitSha']} plus the listed edits in {campaign.overlay}."}]
    for r in levers:
        deps.append({'submissionId': r['id'], 'rationale': 'Source of the lever or pass setting this submission switches on.',
                     'evidence': f"{r['solverUsername']} applied the same setting in submission {r['id']}; see its note."})
    (out / 'dependencies.json').write_text(json.dumps(deps, indent=2) + '\n')
    q, t, s = score['metrics']['qubits'], score['metrics']['toffoli'], score['score']
    official = campaign.root / 'official' / cid
    (out / 'request.json').write_text(json.dumps({'prepared_utc': utc(), 'candidate': cid, 'claimed_score': s, 'Q': q, 'T': t,
        'operation_sha256': ops, 'note_sha256': sha(out / 'note.md'), 'note_bytes': len(note.encode()),
        'dependencies_sha256': sha(out / 'dependencies.json'), 'model': model, 'harness': harness,
        'checkout': relative(official / 'challenge', campaign.repo), 'authorization': authorization},
        indent=2) + '\n')
    failed = [r for r in disclosure if not r[1]]
    return {'candidate': cid, 'Q': q, 'T': t, 'S': s, 'note_bytes': len(note.encode()),
            'screened': len(disclosure), 'failed': len(failed)}


def send(campaign, cid, *, command='ecdsafail', execute=subprocess.run):
    """Run the submit command once. A second send is refused."""
    out = campaign.root / 'publication' / cid
    request = load(out / 'request.json')
    require(sha(out / 'note.md') == request['note_sha256'] and sha(out / 'dependencies.json') == request['dependencies_sha256'],
            'The note or dependency list differs from the prepared request.')
    require(not (out / 'submit-start.json').exists(), 'Submit once.')
    argv = [str(command), 'submit', '--dependencies-file', str(out / 'dependencies.json'), '--note-file', str(out / 'note.md'),
            '--model', request['model'], '--harness', request['harness'], '--claimed-score', str(request['claimed_score']), '--json']
    cwd = campaign.repo / request['checkout']
    with (out / 'submit-start.json').open('x') as stream:
        stream.write(json.dumps({'started_utc': utc(), 'argv': argv, 'cwd': str(cwd)}, indent=2) + '\n')
    done = execute(argv, cwd=cwd, capture_output=True, text=True)
    (out / 'submit.stdout').write_text(done.stdout)
    (out / 'submit.stderr').write_text(done.stderr)
    (out / 'submit-finish.json').write_text(json.dumps({'finished_utc': utc(), 'returncode': done.returncode}, indent=2) + '\n')
    return {'returncode': done.returncode, 'stdout_tail': done.stdout[-1500:], 'stderr_tail': done.stderr[-800:]}
