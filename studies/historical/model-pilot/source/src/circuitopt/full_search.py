"""Recover native construction recipes through a fixed measured candidate table."""
from collections import Counter
import json
import random
import time

from . import full_path


def search(oracle, *, strategy, seed, proposals=100, wall_seconds=900,
           feedback=None, progress=None):
    if strategy not in ['random', 'evolution', 'llm'] or proposals < 1:
        raise ValueError('Invalid search configuration')
    if strategy == 'llm' and feedback is None:
        raise ValueError('The model strategy requires a live feedback transport')
    initial = full_path.starting_recipe()
    initial_id = full_path.recipe_id(initial)
    target = full_path.construction_path()[-1]['recipe']
    target_id = full_path.recipe_id(target)
    reference_S = oracle[target_id]['S']
    archive = {initial_id: {'id': initial_id, 'recipe': initial, 'parent_id': None,
                            'depth': 0, 'effective_depth': 0, **oracle[initial_id]}}
    seen = {initial_id}
    selected = initial_id
    rng = random.Random(seed)
    trials, calls, pending = [], [], []
    first_hits = {'recipe': None, 'cost': None, 'google': None}
    started = time.monotonic()
    trace = [{'trial': 0, 'best_S': oracle[initial_id]['S'], 'id': initial_id}]
    for trial in range(1, proposals + 1):
        if time.monotonic() - started >= wall_seconds:
            break
        if strategy == 'llm' and trial in [5, 25]:
            parents = sorted(archive.values(), key=lambda p: (p['failed_inputs'] > 0, p['S']))
            packet = full_path.model_packet(
                [{k: p[k] for k in ['id', 'recipe', 'S', 'failed_inputs']} for p in parents],
                trials[-12:], trial)
            answer, receipt = feedback(packet)
            calls.append({'trial': trial, 'receipt': receipt})
            incoming = answer.get('proposals', [])
            if isinstance(incoming, list):
                pending.extend(incoming[:8])
        record = {'trial': trial, 'origin': 'model' if pending else 'mutation'}
        try:
            if pending:
                proposal = pending.pop(0)
                parent = archive[proposal['parent_id']]
                action = json.loads(proposal['changes_json'])
                record['hypothesis'] = proposal.get('hypothesis', '')
            else:
                viable = [p for p in archive.values() if p['failed_inputs'] == 0]
                # The original remains the entry point when it has an inherited fault.
                viable = viable or [archive[initial_id]]
                parent = rng.choice(viable) if strategy == 'random' or rng.random() < .3 else min(viable, key=lambda p: p['S'])
                action = rng.choice(full_path.legal_actions(parent['recipe']))
            child = full_path.apply(parent['recipe'], action)
            cid = full_path.recipe_id(child)
            record.update(id=cid, parent_id=parent['id'], rule_id=action['rule_id'], action=action)
            if cid in seen:
                record['status'] = 'duplicate'
            else:
                seen.add(cid)
                result = oracle[cid]
                changed = parent.get('artifact_sha256', parent['id']) != result.get('artifact_sha256', cid)
                entry = {'id': cid, 'parent_id': parent['id'], 'depth': parent['depth'] + 1,
                         'effective_depth': parent['effective_depth'] + int(changed), **result}
                archive[cid] = entry
                record.update(S=result['S'], failed_inputs=result['failed_inputs'],
                              depth=entry['depth'], effective_depth=entry['effective_depth'],
                              changed_gate_stream=changed,
                              status='failed' if result['failed_inputs'] else 'pass')
                if not result['failed_inputs'] and (archive[selected]['failed_inputs'] or result['S'] < archive[selected]['S']):
                    selected = cid
                    record['status'] = 'improvement'
                hit = full_path.hits(child, result, target, reference_S)
                for key, found in hit.items():
                    if found and first_hits[key] is None:
                        first_hits[key] = trial
            record['cache'] = 'shared measured development table'
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            record.update(status='invalid', error=str(exc))
        record['elapsed_seconds'] = time.monotonic() - started
        trials.append(record)
        trace.append({'trial': trial, 'best_S': archive[selected]['S'], 'id': selected,
                      'candidate_id': record.get('id'), 'status': record['status']})
        if progress:
            progress(record)
    counts = dict(Counter(t['status'] for t in trials))
    return {'strategy': strategy, 'seed': seed, 'declared_proposals': proposals,
            'status': 'complete' if len(trials) == proposals else 'wall_limit',
            'trials': trials, 'trace': trace, 'counts': counts,
            'distinct_tested_recipes': len(seen), 'selected': selected,
            'distinct_tested_gate_streams': len({p.get('artifact_sha256', p['id']) for p in archive.values()}),
            'selected_recipe': archive[selected]['recipe'], 'selected_S': archive[selected]['S'],
            'first_hits': first_hits, 'selected_ancestry_edits': archive[selected]['depth'],
            'reference_ancestry_edits': archive.get(target_id, {}).get('depth'),
            'selected_effective_edits': archive[selected]['effective_depth'],
            'model_calls': calls, 'wall_seconds': time.monotonic() - started,
            'scope': 'Recovery in a supplied finite native grammar using shared measured development results.'}
