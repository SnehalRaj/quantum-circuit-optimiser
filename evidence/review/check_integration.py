"""Run local integrity fixtures without model calls or public submissions."""

import json
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from llm_circuit_optimiser import models, runner, source, submission
from llm_circuit_optimiser import cli
from llm_circuit_optimiser.common import ROOT, load, save, sha256
from llm_circuit_optimiser.families import load_family
from llm_circuit_optimiser.grammar import compose


def fake_cli(path, *, fail):
    path.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\nimport json\n"
        "p=Path(__file__).with_suffix('.calls')\n"
        "n=int(p.read_text())+1 if p.exists() else 1\np.write_text(str(n))\n"
        f"print(json.dumps({{'is_error':{fail!r},'modelUsage':{{'test-fixture':{{}}}},"
        "'structured_output':{'action_index':0,'reason':'Local fixture.'}}))\n"
    )
    path.chmod(0o755)


def model_checks(directory):
    outcomes = {}
    grammar = compose([ROOT / 'grammars/base.json', ROOT / 'grammars/families/froghop.json'])
    failed = directory / 'failed-cli'
    fake_cli(failed, fail=True)
    config = {'provider': 'claude', 'model': 'test-fixture', 'executable': str(failed),
              'max_calls': 1, 'timeout_seconds': 5}
    tools = {'small_emit': sys.executable, 'circuit_check': sys.executable}
    with patch.object(runner, 'native_tools', return_value=tools):
        for attempt in range(2):
            try:
                runner.run_campaign(grammar, load_family('froghop'), config, directory / 'failed-run',
                                    target_ids=['q839'], trials=1)
            except models.ModelError:
                pass
            else:
                raise AssertionError('The failed model fixture should reject the call')
            record = directory / 'failed-run/trials/000/model/receipt.json'
            stdout = record.with_name('stdout.txt')
            current = (sha256(record), sha256(stdout))
            if attempt == 0:
                first = current
    assert int(failed.with_suffix('.calls').read_text()) == 1
    assert current == first
    outcomes['failed_resume'] = {'fake_cli_calls': 1, 'max_calls': 1, 'receipt_and_stdout_preserved': True}
    successful = directory / 'successful-cli'
    fake_cli(successful, fail=False)
    config.update(executable=str(successful))
    output = directory / 'successful-call'
    chosen, _ = models.select([{'id': 'fixture'}], config, output, context={})
    before = sha256(output / 'receipt.json')
    reused, receipt = models.select([{'id': 'fixture'}], config, output, context={})
    assert chosen == reused and receipt['recommendation_reused']
    assert int(successful.with_suffix('.calls').read_text()) == 1
    assert sha256(output / 'receipt.json') == before
    outcomes['successful_resume'] = {'fake_cli_calls': 1, 'recommendation_reused': True, 'receipt_preserved': True}
    return outcomes


def source_checks(directory):
    binary = directory / 'fake-emitter'
    binary.write_text("#!/usr/bin/env python3\nfrom pathlib import Path\nPath('ops.bin').write_bytes(b'complete fixture')\n")
    binary.chmod(0o755)
    work = directory / 'interrupted-artifact'
    (work / 'emission').mkdir(parents=True)
    (work / 'emission/ops.bin').write_bytes(b'partial fixture')
    with patch.dict(os.environ, {'LCO_EMISSION_LOCK': str(directory / 'fixture-emission.lock')}), \
         patch.object(source, 'build', return_value={'build_binary': str(binary), 'build_binary_sha256': sha256(binary)}):
        emitted = source.emit({}, 'fixture', work, policy='none')
    archives = list(work.glob('interrupted-emission-*'))
    assert len(archives) == 1 and (archives[0] / 'ops.bin').read_bytes() == b'partial fixture'
    assert Path(emitted['ops_path']).read_bytes() == b'complete fixture'
    family_dir = directory / 'source-family'
    module = family_dir / 'src/point_add/mod.rs'
    module.parent.mkdir(parents=True)
    module.write_text('pub fn build() -> Vec<crate::circuit::Op> { Vec::new() }\n')
    (family_dir / 'NOTICE').write_text('Local test fixture. No inherited arithmetic.\n')
    definition = {'schema_version': 1, 'id': 'original-fixture', 'name': 'Original fixture',
                  'backend': 'source-conditional', 'targets': ['q839'], 'source_dir': '.',
                  'files_sha256': {name: sha256(family_dir / name) for name in ['NOTICE', 'src/point_add/mod.rs']},
                  'provenance': {'description': 'A fixture that tests a target ID collision.',
                                 'authors': ['Local fixture'], 'upstream_family': None}}
    save(family_dir / 'family.json', definition)
    family = load_family(family_dir / 'family.json')
    tools = {'small_emit': sys.executable, 'circuit_check': sys.executable}
    grammar = compose([ROOT / 'grammars/base.json'])
    with patch.object(runner, 'native_tools', return_value=tools):
        try:
            runner.run_campaign(grammar, family, {'provider': 'offline'}, directory / 'collision-replay',
                                target_ids=['q839'], mode='reproduce')
        except ValueError as error:
            assert 'no frozen target' in str(error)
        else:
            raise AssertionError('A custom source target cannot inherit global replay metadata')
    return {'partial_emission_archived': True, 'fresh_fixture_emitted': True,
            'custom_target_collision_rejected_as_frozen_replay': True}


def submission_check(directory):
    export = directory / 'export'
    module = export / 'source/src/point_add/mod.rs'
    module.parent.mkdir(parents=True)
    module.write_text('// Local submission fixture\n')
    for name in ['source.tar.gz', 'audit.json', 'submission-note.md', 'dependencies.json']:
        (export / name).write_text('Local integrity fixture\n')
    record = {'ops_sha256': '0' * 64, 'source_hashes': {'src/point_add/mod.rs': sha256(module)},
              'model': 'Local fixture', 'harness': 'Local fixture', 'submitted': False}
    for name, key in [('source.tar.gz', 'source_archive_sha256'), ('audit.json', 'audit_sha256'),
                      ('submission-note.md', 'note_sha256'), ('dependencies.json', 'dependencies_sha256')]:
        record[key] = sha256(export / name)
    save(export / 'export.json', record)
    calls = []
    def command(args, cwd, **kwargs):
        calls.append(args)
        if args[:3] == ['git', 'worktree', 'add']:
            (Path(args[4]) / 'src/point_add').mkdir(parents=True)
        if args[:2] == ['ecdsafail', 'run']:
            save(Path(cwd) / 'score.json', {'score': 123})
            (Path(cwd) / 'ops.bin').write_bytes(b'different fixture circuit')
        return subprocess.CompletedProcess(args, 0, '', '')
    with patch.object(submission.subprocess, 'run', side_effect=command):
        try:
            submission.submit_export(export, directory, execute=True)
        except ValueError as error:
            assert 'different circuit' in str(error)
        else:
            raise AssertionError('A different operation hash must block submission')
    assert not any(args[:2] == ['ecdsafail', 'submit'] for args in calls)
    return {'different_rebuilt_hash_rejected': True, 'external_submit_calls': 0, 'all_commands_mocked': True}


def bundle_check(directory):
    files = [ROOT / 'grammars/base.json', *sorted((ROOT / 'grammars/families').glob('*.json'))]
    bundle = directory / 'combined.json'
    with redirect_stdout(io.StringIO()):
        assert cli.main(['grammar', 'compose', *map(str, files), '--out', str(bundle)]) == 0
        assert cli.main(['plan', '--grammar', str(bundle), '--targets', 'q839',
                         '--out', str(directory / 'bundle-plan')]) == 0
    plan = load(directory / 'bundle-plan/plan.json')
    original = compose(files)
    args = cli.parser().parse_args(['search', '--grammar', str(bundle), '--targets', 'q839',
                                   '--out', str(directory / 'bundle-search')])
    _, loaded, _, targets = cli.resolve(args)
    assert loaded.sha256 == original.sha256 == plan['grammar_sha256']
    assert targets == ['q839'] and len(plan['candidates']) == 64
    return {'compose_output_accepted_by_plan': True, 'compose_output_accepted_by_search_resolver': True,
            'unchanged_hash': original.sha256, 'candidate_combinations_per_target': 64}


def main():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        checks = {'models': model_checks(directory), 'source': source_checks(directory),
                  'submission': submission_check(directory), 'compiled_bundle': bundle_check(directory)}
    cutoff = load(ROOT / 'grammars/base.json')['definitions']['failure_probability_cutoff']
    assert cutoff == runner.EPSILON == 0.001
    assert runner.audit_qualified({'upper_bound': 0.001, 'protocol_valid': True})
    assert not runner.audit_qualified({'upper_bound': 0.001 + 1e-12, 'protocol_valid': True})
    checks['cutoff'] = {'base_grammar': cutoff, 'runner': runner.EPSILON,
                        'equal_bound_accepted': True, 'larger_bound_rejected': True}
    files = ['src/llm_circuit_optimiser/' + name + '.py' for name in ['models', 'runner', 'source', 'submission', 'grammar', 'large', 'cli']]
    report = {'schema_version': 1, 'scope': 'Local integrity fixtures. No model benefit or circuit qualification claim.',
              'real_model_calls': 0, 'public_submissions': 0, 'checks': checks,
              'source_sha256': {name: sha256(ROOT / name) for name in files},
              'reproduce': 'PYTHONPATH=src python3 evidence/review/check_integration.py'}
    save(Path(__file__).with_name('integration-review.json'), report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
