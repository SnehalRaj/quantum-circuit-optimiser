"""Export checked editable source with attribution and audit evidence."""
from pathlib import Path
import json
import shutil
import tarfile
import subprocess

from .common import ROOT, digest, load, save, sha256
from .runner import audit_qualified


def export_result(run_dir, slot, destination):
    run_dir = Path(run_dir).resolve()
    destination = Path(destination).resolve()
    results = load(run_dir / 'results.json')
    matches = [r for r in results['rows'] if r['slot'] == slot]
    if len(matches) != 1:
        raise ValueError('The completed run has no unique result for this slot')
    row = matches[0]
    if results['contract'].get('audit_pairs', 0) <= 0 or 'final_audit' not in row:
        raise ValueError('Export requires a fresh final audit; screening-only results cannot be submitted')
    if not row.get('qualified') or not audit_qualified(row['final_audit']):
        raise ValueError('Export requires a circuit that meets the fixed probability limit')
    selected = load(run_dir / 'selected.json')
    matches = [i for i, item in enumerate(selected)
               if item['candidate'] == row['candidate'] and item['ops_sha256'] == row['ops_sha256']]
    if len(matches) != 1:
        raise ValueError('The circuit has no unique frozen final audit selection')
    contract = results['contract']
    expected = {'pairs': contract['audit_pairs'], 'streams': 2, 'field_width': 256,
                'alpha': 0.05 / len(selected), 'protocol_valid': True,
                'label': f'{digest(contract)}/fresh-final/{matches[0]}',
                'checker_binary_sha256': contract['tools_sha256']['circuit_check']}
    if any(row['final_audit'].get(key) != value for key, value in expected.items()):
        raise ValueError('The receipt does not match the frozen final audit protocol')
    if sha256(row['ops_path']) != row['ops_sha256']:
        raise ValueError('The checked circuit changed before export')
    audit = row['final_audit']
    if audit.get('ops_sha256') != row['ops_sha256'] or audit.get('streams') != 2:
        raise ValueError('The export audit does not identify this circuit and two-stream protocol')
    if destination.exists():
        raise ValueError('Choose a new export directory')
    emitted = row['large']
    source_dir = Path(emitted['source_dir'])
    for name, expected in emitted['source_hashes'].items():
        if sha256(source_dir / name) != expected:
            raise ValueError(f'The checked source changed: {name}')
    destination.mkdir(parents=True)
    source_out = destination / 'source'
    for name in emitted['source_hashes']:
        path = source_out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_dir / name, path)
    from .large import targets
    backend = results['contract']['family']['backend']
    target = next((t for t in targets() if t['id'] == row['candidate']['target'] and t['family'] == backend), None)
    if target:
        history = ROOT / target['historical_receipts']
        dependency_path = history / 'dependencies.json'
        dependencies = load(dependency_path) if dependency_path.is_file() else []
        inherited_note = (history / 'note.md').read_text() if (history / 'note.md').is_file() else ''
    else:
        provenance = emitted.get('provenance', {})
        dependencies = [{'submissionId': value, 'rationale': 'Inherited source dependency.',
                         'evidence': 'The pinned source family retains the parent commit and source hashes.'}
                        for value in provenance.get('dependency_ids', [])]
        inherited_note = ''
    save(destination / 'dependencies.json', dependencies)
    save(destination / 'audit.json', audit)
    save(destination / 'grammar.json', load(run_dir / 'grammar.json'))
    save(destination / 'source-manifest.json', emitted['source_hashes'])
    model = row.get('model', {})
    note = [
        '# Checked circuit export', '',
        'This export records one source-backed circuit from the staged harness.',
        'The independent full-width audit uses failure probability0.001 and two measurement streams per input.',
        'An input fails when either stream fails.', '',
        '## Recipe and evidence', '',
        '```json', json.dumps({
            'candidate': row['candidate'], 'Q': audit['qubits'], 'mean_T_on_fresh_inputs': audit['mean_toffoli'],
            'ops_sha256': row['ops_sha256'], 'grammar_sha256': row['grammar_sha256'],
            'proposal_provider': model.get('provider'), 'requested_model': model.get('requested_model'),
            'reported_models': model.get('resolved_models'), 'provenance': emitted.get('provenance'),
            'audit': audit}, indent=2), '```', '',
        'The fresh mean T belongs to the stated audit packet.',
        'The submission command obtains the public score from the unchanged benchmark before submission.',
        'Known target reconstruction supplies no model rediscovery claim.', '',
        '## Reproduction', '',
        'Build the exported Cargo project with its pinned lockfile.',
        'Run build_circuit in an empty directory to emit ops.bin.',
        'Run the unchanged evaluator beside that artifact.',
        'The source manifest pins every file used for construction.',
        'The audit and grammar files preserve the checks and admitted actions.', '',
        '## Source hashes', '',
        '```json', json.dumps(emitted['source_hashes'], indent=2, sort_keys=True), '```', '',
        '## Attribution and inherited record', '',
        'Keep the source NOTICE and all file-level credits with this export.',
        'The dependencies file declares reused submissions.',
        'The inherited note below describes the parent research record.',
        'The export evidence above identifies the current circuit.', '', inherited_note,
    ]
    text = '\n'.join(note) + '\n'
    if not 5120 <= len(text.encode()) <= 102400:
        # Small custom examples need substantive context before public submission.
        raise ValueError('The export note needs5–100KiB of method, evidence, and attribution')
    (destination / 'submission-note.md').write_text(text)
    archive = destination / 'source.tar.gz'
    with tarfile.open(archive, 'w:gz') as output:
        for path in sorted(source_out.rglob('*')):
            if path.is_file():
                output.add(path, arcname=str(path.relative_to(source_out)), recursive=False)
    if archive.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('The source archive exceeds the challenge25MiB limit')
    record = {'schema_version': 1, 'source_archive_sha256': sha256(archive),
              'ops_sha256': row['ops_sha256'], 'candidate': row['candidate'],
              'source_hashes': emitted['source_hashes'],
              'audit_sha256': sha256(destination / 'audit.json'),
              'grammar_sha256': row['grammar_sha256'],
              'note_sha256': sha256(destination / 'submission-note.md'),
              'dependencies_sha256': sha256(destination / 'dependencies.json'),
              'model': model.get('requested_model') or 'Model-free source reconstruction',
              'harness': 'llm-circuit-optimiser', 'submitted': False}
    save(destination / 'export.json', record)
    return record


def submit_export(directory, benchmark, *, execute=False):
    """Prepare a separate benchmark worktree and submit only on an explicit call."""
    directory = Path(directory).resolve()
    benchmark = Path(benchmark).resolve()
    record = load(directory / 'export.json')
    for name, key in [('source.tar.gz', 'source_archive_sha256'), ('audit.json', 'audit_sha256'),
                      ('submission-note.md', 'note_sha256'), ('dependencies.json', 'dependencies_sha256')]:
        if sha256(directory / name) != record[key]:
            raise ValueError(f'The export changed: {name}')
    for name, expected in record['source_hashes'].items():
        if sha256(directory / 'source' / name) != expected:
            raise ValueError(f'The exported source changed: {name}')
    stage = directory / 'benchmark'
    command = ['ecdsafail', 'submit', '--dependencies-file', str(directory / 'dependencies.json'),
               '--note-file', str(directory / 'submission-note.md'), '--model', record['model'],
               '--harness', record['harness'], '--claimed-score', '<score from official local run>']
    plan = {'execute': execute, 'benchmark': str(benchmark), 'staging_worktree': str(stage),
            'command': command, 'submitted': False,
            'steps': ['Create a detached benchmark worktree.', 'Copy the exported editable paths.',
                      'Run ecdsafail setup and run.', 'Submit the official local score.']}
    if not execute:
        return plan
    if record.get('submitted'):
        raise ValueError('This export already records a submission; inspect its receipt')
    if stage.exists():
        raise ValueError('A staging worktree exists; inspect it before another submission attempt')
    def command_run(args, cwd, label):
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
        (directory / (label + '.stdout.txt')).write_text(done.stdout)
        (directory / (label + '.stderr.txt')).write_text(done.stderr)
        if done.returncode:
            raise RuntimeError(f'{label} failed; inspect the saved process output')
        return done
    command_run(['git', 'worktree', 'add', '--detach', str(stage), 'HEAD'], benchmark, 'worktree')
    editable = stage / 'src/point_add'
    if editable.is_dir():
        editable.rename(directory / 'inherited-point-add')
    shutil.copytree(directory / 'source/src/point_add', editable)
    command_run(['ecdsafail', 'setup'], stage, 'setup')
    command_run(['ecdsafail', 'run'], stage, 'official')
    rebuilt_ops = stage / 'ops.bin'
    if not rebuilt_ops.is_file() or sha256(rebuilt_ops) != record['ops_sha256']:
        raise ValueError('The official build emitted a different circuit. Audit that artifact before submission.')
    score_path = stage / 'score.json'
    if not score_path.is_file():
        raise ValueError('The official local run did not produce score.json')
    score = load(score_path).get('score')
    if not isinstance(score, (int, float)) or isinstance(score, bool) or score <= 0:
        raise ValueError('The official local run did not return a positive score')
    command[-1] = str(score)
    plan.update(command=command, claimed_score=score)
    save(directory / 'submission-request.json', plan)
    # An uncertain network result requires inspection before any new request.
    record['submission_attempted'] = True
    save(directory / 'export.json', record)
    command_run(command, stage, 'submit')
    record['submitted'] = True
    save(directory / 'export.json', record)
    plan['submitted'] = True
    return plan
