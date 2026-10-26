"""Preserve the failed alias and run the documented GPT 6.1 CLI identifier."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import run_pilot as pilot


def prepare():
    pilot.verify_contract()
    cache = Path.home() / '.codex/models_cache.json'
    metadata = json.loads(cache.read_text())
    matches = [{k: r.get(k) for k in ['slug', 'display_name', 'supported_in_api', 'visibility']}
               for r in metadata.get('models', []) if r.get('slug') == 'gpt-6.1-sol']
    if len(matches) != 1:
        raise ValueError('The corrected identifier is not present in local model metadata')
    pilot.save(pilot.HERE / 'identifier-amendment.json', {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'reason': 'The requested family label gpt-6.1 is not a supported CLI identifier for this ChatGPT subscription',
        'failed_alias_preflight': 'preflight/codex.json',
        'failed_alias_preflight_sha256': pilot.sha(pilot.HERE / 'preflight/codex.json'),
        'new_arm_name': 'codex61sol', 'new_model_identifier': 'gpt-6.1-sol',
        'model_metadata': matches, 'metadata_fetched_at': metadata.get('fetched_at'),
        'parent_contract_sha256': pilot.sha(pilot.HERE / 'contract.json'),
        'amendment_driver_sha256': pilot.sha(__file__),
        'scope': 'Identifier correction before any GPT search; preserve the original failed preflight. Every search setting remains unchanged.',
        'selection': 'No GPT candidate or search outcome existed when the identifier correction was chosen',
        'audit_family': 'The corrected arm replaces no observed search; the three executable arms still have at most111 distinct audit circuits',
    }, True)


def preflight():
    pilot.verify_contract()
    _, providers, _, _, _, _ = pilot.import_source()
    pilot.transport_with_metadata(providers)
    for name in ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY']:
        os.environ.pop(name, None)
    path = pilot.HERE / 'preflight/codex61sol.json'
    if path.exists():
        raise FileExistsError('The corrected identifier already has a preflight')
    answer, receipt = providers.generate('codex', 'gpt-6.1-sol',
        'Connection check only. Return {"proposals":[]}. Use no tools.',
        timeout=180, reasoning_effort='medium', isolated=True, receipt_path=path)
    pilot.save(path, {'answer': answer, 'receipt': receipt})
    print(receipt['status'], receipt.get('resolved_models'), receipt.get('model_identity_status'), flush=True)


def run():
    pilot.ARMS = {'codex61sol': ('codex', 'gpt-6.1-sol', 'llm')}
    pilot.run()


def finish():
    pilot.ARMS['codex61sol'] = ('codex', 'gpt-6.1-sol', 'llm')
    pilot.audit()
    pilot.analyse()
    path = pilot.PAPER / 'data/model-pilot.json'
    result = pilot.load(path)
    result['identifier_amendment'] = pilot.load(pilot.HERE / 'identifier-amendment.json')
    result['availability_failures'] = {'codex-family-alias': pilot.load(pilot.HERE / 'preflight/codex.json')}
    actual = [r for r in result['summary'] if r['arm'] != 'codex']
    result['executable_matrix_status'] = 'complete' if all(r['status'] == 'complete' for r in actual) else 'partial'
    result['summary_status_note'] = 'The unsupported alias remains an explicit blocked row. Compare offline, claude and codex61sol as the amended executable matrix.'
    pilot.save(path, result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'preflight', 'run', 'finish'])
    args = parser.parse_args()
    {'prepare': prepare, 'preflight': preflight, 'run': run, 'finish': finish}[args.stage]()
