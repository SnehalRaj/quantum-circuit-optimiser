"""Run and retain a complete native construction and recovery experiment."""
import argparse
import json
from pathlib import Path
import secrets
import time

from . import full_native as native, full_path, full_search, full_integrity, providers


def init(project, root):
    project, root = Path(project).resolve(), Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    config = {'schema': 1, 'bits': 256, 'interface': 'complete_generic_domain_secp256k1_point_addition',
              'construction': full_path.construction_path(), 'rules': full_path.RULES,
              'development_pairs': 512, 'long_pairs': 16384, 'final_pairs': 1000000,
              'streams': 2, 'threads': 4, 'epsilon': 1e-5, 'confidence': .95,
              'seeds': [11, 29, 47], 'strategies': ['random', 'evolution', 'llm'],
              'proposals': 100, 'wall_seconds': 900, 'review_rounds': [5, 25],
              'model': 'claude-opus-5-5', 'provider': 'claude',
              'development_label': 'full-construction-development-' + secrets.token_hex(12),
              'created_unix': time.time(),
              'target_interpretation': 'Recover the supplied reference recipe; also record cost and Google-reference hits.',
              'cost_interpretation': 'Shared cold construction measurements; search uses cached development results.',
              'scope': 'Finite native construction recovery. No independent new algorithm discovery claim.'}
    native.save(root / 'config.json', config)
    full_integrity.seal(root / 'config.json')
    native.freeze_tools(project, root)
    full_integrity.seal(root / 'tools.json')
    native.save(root / 'predictions.json', [
        {'rule_id': 'PA01', 'prediction': 'Lower Q and S through a joint architecture replacement. Inherited error mechanisms change.'},
        {'rule_id': 'PA02', 'prediction': 'Lower T. The cleanup sites retain the same qubit allocation.'},
        {'rule_id': 'PA03', 'prediction': 'Lower correction T. Measure global Q rather than assuming a saving.'},
        {'rule_id': 'PA04', 'prediction': 'Lower peak Q through shorter live carry ladders. Extra carry repair can raise T.'}])
    control = root / 'controls'
    control.mkdir()
    proc, _ = native.command(root, [root / 'tools/study-check', '--self-test', '--out', control / 'result.json'], directory=control)
    if proc.returncode or not json.loads((control / 'result.json').read_text())['passed']:
        raise RuntimeError('The fixed evaluator failed its diagnostic controls')
    return config


def measure(root):
    root = Path(root).resolve()
    config = full_integrity.read_sealed(root / 'config.json')
    oracle_path = root / 'development.json'
    oracle = json.loads(oracle_path.read_text()) if oracle_path.exists() else {}
    path = [x['recipe'] for x in config['construction']]
    ordered = path + [r for r in full_path.candidate_pool() if r not in path]
    for index, recipe in enumerate(ordered):
        cid = full_path.recipe_id(recipe)
        if cid in oracle:
            continue
        artifact_file = root / 'circuits' / cid / 'artifact.json'
        artifact = json.loads(artifact_file.read_text()) if artifact_file.exists() else native.emit(root, recipe)
        print(f'measure {index + 1}/{len(ordered)} {recipe}', flush=True)
        result = native.evaluate(root, artifact, label=config['development_label'],
                                 pairs=config['development_pairs'], name='development-' + cid,
                                 threads=config['threads'])
        oracle[cid] = result
        native.save(oracle_path, oracle)
        print(f'Q={result["Q"]} T={result["T"]} S={result["S"]} failed={result["failed_inputs"]}/{result["pairs"]}', flush=True)
    rows = []
    for item in config['construction']:
        result = oracle[full_path.recipe_id(item['recipe'])]
        rows.append({**item, **result})
    native.save(root / 'construction.json', rows)
    return oracle


def recover(root):
    root = Path(root).resolve()
    config = full_integrity.read_sealed(root / 'config.json')
    oracle = json.loads((root / 'development.json').read_text())
    results = []
    for strategy in config['strategies']:
        for seed in config['seeds']:
            directory = root / 'searches' / f'{strategy}-{seed}'
            if (directory / 'result.json').exists():
                results.append(json.loads((directory / 'result.json').read_text()))
                continue
            directory.mkdir(parents=True, exist_ok=False)
            def feedback(packet):
                location = directory / 'model' / str(packet['proposal_number'])
                location.mkdir(parents=True, exist_ok=False)
                native.save(location / 'packet.json', packet)
                prompt = json.dumps(packet, separators=(',', ':'))
                (location / 'prompt.txt').write_text(prompt)
                answer, receipt = providers.generate(config['provider'], config['model'], prompt,
                                                     timeout=180, reasoning_effort='medium', isolated=True,
                                                     receipt_path=location / 'receipt.json')
                native.save(location / 'answer.json', answer)
                if receipt.get('error') in ['AuthenticationRequired', 'RequestedModelMismatch']:
                    raise RuntimeError('The requested model could not run; preserve this failed call')
                return answer, receipt
            def progress(record):
                with (directory / 'events.jsonl').open('a') as stream:
                    stream.write(json.dumps(record, separators=(',', ':')) + '\n')
            print('search', strategy, seed, flush=True)
            result = full_search.search(oracle, strategy=strategy, seed=seed,
                                        proposals=config['proposals'], wall_seconds=config['wall_seconds'],
                                        feedback=feedback if strategy == 'llm' else None, progress=progress)
            native.save(directory / 'result.json', result)
            results.append(result)
            native.save(root / 'search-results.json', results)
            print('first hits', result['first_hits'], 'selected', result['selected_S'], flush=True)
    # Freeze every selected endpoint and the reference before fresh outcomes exist.
    ids = sorted({r['selected'] for r in results} |
                 {full_path.recipe_id(config['construction'][-1]['recipe'])})
    selected, aliases = full_path.canonical_endpoints(ids, oracle)
    manifest = {'circuits': {}, 'recipe_aliases': aliases,
                'selections': [{k: r[k] for k in ['strategy', 'seed', 'selected']} for r in results],
                'epsilon': config['epsilon'], 'final_pairs': config['final_pairs'],
                'confidence': config['confidence'], 'streams': config['streams'],
                'created_unix': time.time()}
    for cid in selected:
        artifact = json.loads((root / 'circuits' / cid / 'artifact.json').read_text())
        native.assert_hash(root / artifact['operation_file'], artifact['sha256'])
        manifest['circuits'][cid] = artifact
    if not (root / 'finalists.json').exists():
        native.save(root / 'finalists.json', manifest)
        (root / 'finalists.sha256').write_text(native.file_hash(root / 'finalists.json') + '\n')
    return results


def audit(root, final=False):
    root = Path(root).resolve()
    config = full_integrity.read_sealed(root / 'config.json')
    native.assert_hash(root / 'finalists.json', (root / 'finalists.sha256').read_text().strip())
    manifest = json.loads((root / 'finalists.json').read_text())
    filename = 'audit-final.json' if final else 'audit-long.json'
    previous = json.loads((root / filename).read_text()) if (root / filename).exists() else {}
    if manifest['epsilon'] != config['epsilon'] or manifest['final_pairs'] != config['final_pairs']:
        raise ValueError('Frozen finalist qualification settings changed')
    for key in ['confidence', 'streams']:
        if key in manifest and manifest[key] != config[key]:
            raise ValueError(f'Frozen finalist {key} changed')
    pairs = config['final_pairs'] if final else config['long_pairs']
    stage = 'final' if final else 'long'
    contract_file = root / ('audit-' + stage + '-contract.json')
    if contract_file.exists():
        contract = full_integrity.read_sealed(contract_file)
    else:
        chunks = []
        remaining = pairs
        while remaining:
            size = min(65536, remaining)
            chunks.append({'pairs': size, 'label': 'full-point-add-' + stage + '-' + secrets.token_hex(24)})
            remaining -= size
        contract = {'stage': stage, 'pairs': pairs, 'chunks': chunks,
                    'circuit_ids': sorted(manifest['circuits']), 'consumed': True,
                    'epsilon': config['epsilon'], 'confidence': config['confidence'], 'streams': config['streams'],
                    'stopping': 'Complete the declared input count. No stopping for a favorable bound.'}
        native.save(contract_file, contract)
        full_integrity.seal(contract_file)
    full_integrity.validate_contract(contract, pairs=pairs, circuit_ids=manifest['circuits'],
                                     stage=stage, streams=config['streams'], epsilon=manifest['epsilon'],
                                     confidence=config['confidence'])
    other_file = root / ('audit-long-contract.json' if final else 'audit-final-contract.json')
    if other_file.exists():
        other_labels = {chunk['label'] for chunk in json.loads(other_file.read_text())['chunks']}
        if any(chunk['label'] in other_labels for chunk in contract['chunks']):
            raise ValueError('Independent audit stages reuse an input label')
    rows = {}
    integrity = {}
    for cid, artifact in manifest['circuits'].items():
        print('fresh test', stage, cid[:12], pairs, flush=True)
        chunks = []
        for index, plan in enumerate(contract['chunks']):
            name = f'{stage}-{cid}-{index}'
            saved = root / 'evaluations' / name / 'result.json'
            if saved.exists():
                result, proof = full_integrity.validate_evaluation(
                    root, saved.parent, artifact, label=plan['label'], pairs=plan['pairs'], streams=config['streams'])
            else:
                result = native.evaluate(root, artifact, label=plan['label'], pairs=plan['pairs'],
                                         name=name, fresh=True, threads=config['threads'])
                result, proof = full_integrity.validate_evaluation(
                    root, saved.parent, artifact, label=plan['label'], pairs=plan['pairs'], streams=config['streams'])
            integrity[name] = proof
            chunks.append(result)
            native.save(root / (f'audit-{stage}-{cid}-progress.json'),
                        {'planned_pairs': pairs, 'completed_pairs': sum(r['pairs'] for r in chunks), 'chunks': chunks})
            print('audit chunk', index + 1, '/', len(contract['chunks']), 'failed', result['failed_inputs'], flush=True)
        observed_pairs = sum(r['pairs'] for r in chunks)
        if observed_pairs != pairs or any(r['Q'] != chunks[0]['Q'] for r in chunks):
            raise ValueError('Evaluated chunks do not match the declared circuit and denominator')
        mean = sum(r['T'] * r['pairs'] for r in chunks) / observed_pairs
        result = {'id': cid, 'recipe': artifact['recipe'], 'artifact_sha256': artifact['sha256'],
                  'pairs': pairs, 'streams': config['streams'], 'Q': chunks[0]['Q'], 'T': mean,
                  'S': chunks[0]['Q'] * int(mean + .5), 'chunks': chunks,
                  'failed_inputs': sum(r['failed_inputs'] for r in chunks),
                  'failed_shots': sum(r['failed_shots'] for r in chunks),
                  'channel_failed_inputs': {key: sum(r['channel_failed_inputs'][key] for r in chunks)
                                            for key in chunks[0]['channel_failed_inputs']},
                  'wall_seconds': sum(r['wall_seconds'] for r in chunks)}
        result['epsilon_upper_adjusted'] = full_path.failure_upper(
            result['failed_inputs'], observed_pairs, (1 - config['confidence']) / len(manifest['circuits']))
        result['qualified'] = result['epsilon_upper_adjusted'] <= manifest['epsilon']
        result['role'] = 'final qualification' if final else 'independent diagnostic'
        rows[cid] = result
        if previous.get('status') != 'complete':
            native.save(root / filename, {'rows': rows, 'status': 'running'})
        print('fresh result', stage, result['failed_inputs'], result['epsilon_upper_adjusted'], flush=True)
    output = {'rows': rows, 'status': 'complete', 'scope': contract, 'epsilon': config['epsilon']}
    native.save(root / ('audit-' + stage + '-verification.json'), integrity)
    if previous.get('status') == 'complete':
        for cid, observed in rows.items():
            for key in ['pairs', 'streams', 'Q', 'T', 'S', 'failed_inputs', 'failed_shots', 'qualified']:
                if previous['rows'][cid][key] != observed[key]:
                    raise ValueError(f'Completed audit disagrees with its supporting evidence: {key}')
        return previous
    native.save(root / filename, output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'measure', 'recover', 'audit-long', 'audit-final', 'report'])
    parser.add_argument('root', type=Path)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    if args.action == 'init':
        init(args.project, args.root)
    elif args.action == 'measure':
        measure(args.root)
    elif args.action == 'recover':
        recover(args.root)
    elif args.action.startswith('audit'):
        audit(args.root, final=args.action == 'audit-final')
    else:
        from . import full_report
        full_report.report(args.root)


if __name__ == '__main__':
    main()
