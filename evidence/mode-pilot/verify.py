#!/usr/bin/env python3
"""Check the saved mode pilot proof without rerunning simulations."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
COMMIT = '49bdddbd3b5486739ab4e0e549bf92285c34fc2c'


def load(path):
    return json.loads(path.read_text())


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_audit(audit, *, width, qualified=True):
    assert audit['field_width'] == width
    assert audit['streams'] == 2
    assert 0 <= audit['failed_inputs'] <= audit['pairs']
    assert audit['packet_sha256'] and audit['per_input_sha256']
    if qualified:
        assert audit['upper_bound'] <= 0.001


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', action='store_true', help='Also check local run files and circuit caches.')
    args = parser.parse_args()
    cases = ['ap-offline-v2', 'ap-claude-v2', 'custom-offline-v2', 'custom-claude-v2']
    calls = 0
    trials = 0
    for case in cases:
        proof = load(EVIDENCE / (case + '.json'))
        assert proof['controller_commit_verified_before_launch'] == COMMIT
        calls += proof['model_calls']
        if case.startswith('ap-'):
            assert proof['qualified'] and proof['epsilon'] == 0.001
            assert proof['official'] == {'Q': 1270, 'T': 793798, 'historical_counts_match': True, 'passed': True}
            for label, width in [('fast', 16), ('screen', 256), ('final', 256)]:
                check_audit(proof[label], width=width)
            assert proof['screen']['packet_sha256'] != proof['final']['packet_sha256']
            trials += 1
        else:
            assert proof['frozen_contract']['audit_pairs'] == 0
            assert proof['frozen_contract']['epsilon'] == 0.001
            assert proof['completed'] == proof['qualified'] == len(proof['rows'])
            for row in proof['rows']:
                assert row['qualified']
                check_audit(row['fast'], width=16)
                check_audit(row['screen'], width=256)
            trials += proof['completed']
        if args.raw:
            run = ROOT / proof['run_directory']
            assert sha(run / 'results.json') == proof['results_sha256']
            assert sha(run / 'contract.json') == proof['contract_sha256']
            data = load(run / 'results.json')
            for row in data['rows']:
                assert sha(Path(row['ops_path'])) == row['ops_sha256']
                emitted = row['large']
                assert all(sha(Path(emitted['source_dir']) / name) == expected
                           for name, expected in emitted['source_hashes'].items())
    assert calls == 3 and trials == 5
    custom = load(EVIDENCE / 'custom-claude-v2.json')
    assert [row['candidate']['policy'] for row in custom['rows']] == ['u', 'none']
    assert [row['operation_artifact_reused'] for row in custom['rows']] == [False, True]
    assert custom['rows'][0]['large_rewrite_counts']['u'] == 1
    assert custom['rows'][0]['small_activation_counts']['u'] == 3658
    family = load(EVIDENCE / 'pilot-ap-family.json')
    assert len(family['files_sha256']) == 124
    assert family['provenance']['upstream_family'] == 'qualified-leapfrog'
    exported = load(EVIDENCE / 'export-dryrun-v2.json')
    assert exported['frozen_contract_audit_pairs'] == 0
    assert exported['qualification'] == 'screening'
    assert not exported['execute'] and not exported['submitted']
    assert not exported['staging_worktree_created']
    assert exported['benchmark_status_unchanged']
    assert exported['ops_sha256'] == custom['rows'][0]['ops_sha256']
    assert sha(ROOT / exported['source_manifest_file']) == exported['source_manifest_sha256']
    dependencies = load(ROOT / exported['dependencies_file'])
    assert [d['submissionId'] for d in dependencies] == family['provenance']['dependency_ids']
    if args.raw:
        source = (EVIDENCE / family['source_dir']).resolve()
        assert all(sha(source / name) == expected for name, expected in family['files_sha256'].items())
        directory = ROOT / exported['export_directory']
        assert sha(directory / 'export.json') == exported['export_record_sha256']
        assert sha(directory / 'source.tar.gz') == exported['source_archive_sha256']
        assert sha(directory / 'audit.json') == exported['audit_sha256']
        assert sha(directory / 'submission-note.md') == exported['note_sha256']
        assert sha(directory / 'dependencies.json') == exported['dependencies_sha256']
        assert not (directory / 'benchmark').exists()
    print(json.dumps({'verified_cases': len(cases), 'qualified_trials': trials,
                      'real_model_calls': calls, 'raw_files_checked': args.raw,
                      'external_submitted': False}, sort_keys=True))


if __name__ == '__main__':
    main()
