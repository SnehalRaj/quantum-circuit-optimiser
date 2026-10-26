"""Continue a frozen native swarm under an explicit, prospective budget amendment."""
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import time

from . import swarm_native as base

native, integrity = base.native, base.integrity
ALLOWED_CHANGES = {'search_cutoff_utc', 'deadline_utc', 'proposal_slots', 'maximum_depth', 'seed', 'extension'}


def replace_sealed(path, value):
    """Install a new prospective contract after the old contract is archived."""
    path = Path(path)
    pending = path.with_name(path.name + '.amendment-next')
    native.save(pending, value)
    integrity.seal(pending)
    os.replace(pending, path)
    os.replace(pending.with_name(pending.name + '.sha256'), path.with_name(path.name + '.sha256'))


def assert_parent_prefix(parent_bytes, records):
    """Check original record bytes despite the closing JSON-array delimiter."""
    parent = json.loads(parent_bytes)
    prefix = json.dumps(records[:len(parent)], indent=2, allow_nan=False).encode() + b'\n'
    if prefix != parent_bytes:
        raise ValueError('The original trial records changed')
    return len(parent)


def assert_literal_journal_prefix(journal, parent_bytes):
    with Path(journal).open('rb') as stream:
        if stream.read(len(parent_bytes)) != parent_bytes:
            raise ValueError('The append-only journal lost its original byte prefix')


def amended_config(original, *, cutoff, deadline, proposals, depth, seed, parent):
    value = copy.deepcopy(original)
    value.update(search_cutoff_utc=cutoff, deadline_utc=deadline,
                 proposal_slots=proposals, maximum_depth=depth, seed=seed)
    value['extension'] = {'kind': 'warm-start budget amendment', 'parent_state': str(parent),
                          'parent_proposal_slots': original['proposal_slots'],
                          'original_proposal_seed': original['seed'], 'proposal_rng_seed': seed,
                          'authorised_total_hours': 3, 'original_start_utc': '2026-10-23T07:42:17Z',
                          'sample_policy': 'Reuse all original development labels. Keep the independent audit protocol unchanged.'}
    changed = {key for key in set(original) | set(value) if original.get(key) != value.get(key)}
    if not changed <= ALLOWED_CHANGES:
        raise ValueError('A protected contract field changed')
    return value


def adopt(project, root, *, cutoff='2026-10-23T09:35:00Z', deadline='2026-10-23T10:42:17Z',
          proposals=1536, depth=128, seed=11731):
    project, root = Path(project).resolve(), Path(root).resolve()
    if not (root/'search-result.json').exists():
        raise ValueError('The original search must finish before the amendment')
    if any((root/name).exists() for name in ('shortlist.json', 'finalist.json', 'audit-contract.json', 'extension-started.json')):
        raise ValueError('The prospective amendment must precede selection and independent draws')
    old_config = integrity.read_sealed(root/'config.json')
    if 'extension' in old_config:
        raise ValueError('The extension contract was already adopted')
    old_execution = integrity.read_sealed(root/'execution-source.json')
    tools = integrity.read_sealed(root/'tools.json')
    parent = root/'budget-amendments/three-hours/parent-state'
    parent.mkdir(parents=True, exist_ok=False)
    files = ['config.json', 'config.json.sha256', 'execution-source.json', 'execution-source.json.sha256',
             'tools.json', 'tools.json.sha256', 'archive.json', 'trials.json', 'trace.json', 'rule-counts.json',
             'eligible-sites.json', 'search-started.json', 'search-result.json', 'controller-tests.json']
    for name in files:
        shutil.copy2(root/name, parent/name)
    for name in ('execution-source', 'source', 'tools'):
        shutil.copytree(root/name, parent/name)
    parent_bytes = (parent/'trials.json').read_bytes()
    original_trials = json.loads(parent_bytes)
    assert_parent_prefix(parent_bytes, original_trials)
    # The sequence keeps the complete original array as a literal byte prefix.
    with (root/'trials.jsonseq').open('xb') as stream:
        stream.write(parent_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    new_config = amended_config(old_config, cutoff=cutoff, deadline=deadline, proposals=proposals,
                                depth=depth, seed=seed, parent=parent.relative_to(root))
    version = root/'execution-source-v3-extension'
    new_execution = copy.deepcopy(old_execution)
    for name in ('src/circuitopt/swarm_extension.py', 'tools/run_swarm_extension.py', 'tests/test_swarm_extension.py'):
        source, target = project/name, version/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        new_execution['files'][name] = {'file': str(target.relative_to(root)), 'sha256': native.file_hash(target)}
    new_execution['extension_provenance'] = {
        'adopted_utc': base.utc(), 'prior_manifest': str((parent/'execution-source.json').relative_to(root)),
        'original_controller_unchanged': True, 'before_independent_draws': True}
    replace_sealed(root/'config.json', new_config)
    replace_sealed(root/'execution-source.json', new_execution)
    protected = [key for key in old_config if key not in ALLOWED_CHANGES]
    if any(old_config[key] != new_config[key] for key in protected):
        raise ValueError('Protected samples, evaluator settings or acceptance criteria changed')
    if integrity.read_sealed(root/'tools.json') != tools:
        raise ValueError('The original executable or native-source manifest changed')
    receipt = {'adopted_utc': base.utc(), 'parent_trials': len(original_trials),
               'parent_trials_sha256': hashlib.sha256(parent_bytes).hexdigest(),
               'parent_config_sha256': native.file_hash(parent/'config.json'),
               'active_config_sha256': native.file_hash(root/'config.json'),
               'parent_execution_sha256': native.file_hash(parent/'execution-source.json'),
               'active_execution_sha256': native.file_hash(root/'execution-source.json'),
               'unchanged_tools_sha256': native.file_hash(root/'tools.json'),
               'protected_config_fields': protected,
               'changed_config_fields': sorted(key for key in set(old_config) | set(new_config)
                                                if old_config.get(key) != new_config.get(key)),
               'history_policy': 'The original JSON array stays byte-exact in parent-state and at the start of trials.jsonseq. Active trials.json retains its exact original record serialization.',
               'copied_evidence': 'All parent receipts are reused development evidence, with fresh=False. They are never counted as new draws.'}
    native.save(root/'budget-amendments/three-hours/adoption.json', receipt)
    integrity.seal(root/'budget-amendments/three-hours/adoption.json')
    base.verify_execution_source(root)
    return receipt


def load_state(root):
    root = Path(root)
    parent = root/'budget-amendments/three-hours/parent-state'
    parent_bytes = (parent/'trials.json').read_bytes()
    trials = json.loads((root/'trials.json').read_text())
    assert_parent_prefix(parent_bytes, trials)
    assert_literal_journal_prefix(root/'trials.jsonseq', parent_bytes)
    archive = json.loads((root/'archive.json').read_text())
    original_archive = json.loads((parent/'archive.json').read_text())
    if any(archive.get(cid) != row for cid, row in original_archive.items()):
        raise ValueError('A reused parent archive entry changed')
    seen = {row['id'] for row in trials if row.get('id')} | set(archive)
    return {'parent': parent, 'parent_bytes': parent_bytes, 'parent_count': len(json.loads(parent_bytes)),
            'trials': trials, 'archive': archive, 'seen': seen, 'parent_archive_ids': set(original_archive),
            'trace': json.loads((root/'trace.json').read_text()),
            'counts': Counter(json.loads((root/'rule-counts.json').read_text())),
            'eligible': {key: set(value) for key, value in json.loads((root/'eligible-sites.json').read_text()).items()}}


def continue_search(root):
    root = Path(root).resolve()
    base.verify_execution_source(root)
    config = integrity.read_sealed(root/'config.json')
    if config.get('extension', {}).get('proposal_rng_seed') != 11731:
        raise ValueError('The declared warm-start proposal seed changed')
    state = load_state(root)
    with (root/'extension-started.json').open('x') as reservation:
        json.dump({'started_utc': base.utc(), 'cumulative_slot_start': len(state['trials']),
                   'cumulative_slot_limit': config['proposal_slots'], 'seed': config['seed'],
                   'execution_source_sha256': native.file_hash(root/'execution-source.json'),
                   'config_sha256': native.file_hash(root/'config.json'),
                   'parent_trials_sha256': hashlib.sha256(state['parent_bytes']).hexdigest(),
                   'reused_evidence': True}, reservation, indent=2)
        reservation.write('\n')
        reservation.flush()
        os.fsync(reservation.fileno())
    archive, trials, trace = state['archive'], state['trials'], state['trace']
    counts, eligible, seen = state['counts'], state['eligible'], state['seen']
    rng = random.Random(config['seed'])
    passing = [row for row in archive.values() if not row['cost_only_reference'] and not row['screen']['failed_inputs']]
    best = min(passing, key=lambda row: row['screen']['S'])['id']
    original_best = best
    started = time.monotonic()
    first_start = base.timestamp(json.loads((root/'search-started.json').read_text())['started_utc'])
    cutoff, deadline = base.timestamp(config['search_cutoff_utc']), base.timestamp(config['deadline_utc'])
    start_slot = len(trials)
    with base.deadline_calls(min(cutoff, deadline)):
        for slot in range(start_slot, config['proposal_slots']):
            if time.time() >= cutoff-5:
                break
            record = {'slot': slot, 'segment': 'three-hours-warm-start', 'started_utc': base.utc()}
            parent_ids = []
            try:
                parent = base.pick_parent(archive, rng, config['maximum_depth'], slot)
                parent_ids = [parent['id']]
                profile = json.loads((root/parent['artifact']['profile_file']).read_text())
                actions = base.legal_actions(parent['recipe'], profile, eligible)
                for action in actions:
                    counts[action['rule']+'/opportunity'] += 1
                if rng.random() < .16:
                    other = base.pick_parent(archive, rng, config['maximum_depth'], slot+1)
                    parent_ids.append(other['id'])
                    value = base.combine(parent['recipe'], other['recipe'], rng)
                    action = {'rule': 'combine', 'parents': parent_ids}
                else:
                    groups = {}
                    for option in actions:
                        groups.setdefault(option['rule'], []).append(option)
                    family = rng.choices(list(groups), weights=[4 if key == 'fold_site' else 2 if key in
                        ('scalar', 'inlet_sites', 'inline_sites') else 1 for key in groups])[0]
                    action = rng.choice(groups[family])
                    value = base.apply(parent['recipe'], action)
                cid = base.identity(value)
                record.update(id=cid, parents=parent_ids, action=action,
                              reused_parent_evidence=any(pid in state['parent_archive_ids'] for pid in parent_ids))
                counts[action['rule']+'/attempted'] += 1
                if cid in seen:
                    record['status'] = 'duplicate'
                    counts[action['rule']+'/duplicate'] += 1
                else:
                    seen.add(cid)
                    depth = max(archive[pid]['depth'] for pid in parent_ids)+1
                    if depth > config['maximum_depth']:
                        raise ValueError('Maximum warm-start ancestry exceeded')
                    artifact = base.emit(root, value)
                    profile = json.loads((root/artifact['profile_file']).read_text())
                    for row in profile:
                        if row.get('retired'):
                            eligible['inlet_sites'].add(row['id'])
                        if row.get('inline'):
                            eligible['inline_sites'].add(row['id'])
                    active = base.action_activity(action, profile)
                    if active is False:
                        counts[action['rule']+'/inactive'] += 1
                    counts[action['rule']+'/applied'] += 1
                    reused = (root/'evaluations'/('screen-'+cid)/'result.json').exists()
                    screen = base.checked_evaluate(root, artifact, label=config['labels']['screen'],
                        pairs=config['screen_pairs'], name='screen-'+cid, threads=config['threads'])
                    entry = {'id': cid, 'recipe': value, 'parents': parent_ids, 'depth': depth,
                             'action': action, 'artifact': artifact, 'screen': screen,
                             'active_scoped_edit': active, 'cost_only_reference': False,
                             'segment': 'three-hours-warm-start'}
                    archive[cid] = entry
                    record.update(reused_screen_evidence=reused, status='failed' if screen['failed_inputs'] else 'pass',
                                  depth=depth, S=screen['S'], Q=screen['Q'], T=screen['T'],
                                  failed_inputs=screen['failed_inputs'], active_scoped_edit=active,
                                  changed_gate_stream=any(artifact['sha256'] != archive[pid]['artifact']['sha256'] for pid in parent_ids))
                    if not record['changed_gate_stream']:
                        counts[action['rule']+'/same_gate_stream'] += 1
                    if not screen['failed_inputs'] and screen['S'] < archive[best]['screen']['S']:
                        best = cid
                        record['status'] = 'improvement'
                        if time.time() < cutoff-20:
                            entry['development512'] = base.checked_evaluate(root, artifact,
                                label=config['labels']['development512'], pairs=config['promising_pairs'],
                                name='development512-'+cid, threads=config['threads'])
                    if screen['failed_inputs']:
                        counts[action['rule']+'/rejected'] += 1
                    print(json.dumps({key: record[key] for key in ('slot', 'status', 'S', 'Q', 'T', 'depth')}), flush=True)
            except Exception as exc:
                record.update(status='invalid' if isinstance(exc, ValueError) else 'instrument_error',
                              error=type(exc).__name__, message=str(exc)[-1500:])
                counts[record.get('action', {}).get('rule', 'unknown')+'/error'] += 1
                print(json.dumps(record), flush=True)
            record['completed_utc'] = base.utc()
            trials.append(record)
            with (root/'trials.jsonseq').open('ab') as journal:
                journal.write((json.dumps(record, separators=(',', ':'))+'\n').encode())
                journal.flush()
            assert_parent_prefix(state['parent_bytes'], trials)
            assert_literal_journal_prefix(root/'trials.jsonseq', state['parent_bytes'])
            trace.append({'slot': slot, 'segment': 'three-hours-warm-start', 'best_id': best,
                          'best_S': archive[best]['screen']['S'], 'status': record['status'],
                          'elapsed_seconds': time.time()-first_start,
                          'segment_elapsed_seconds': time.monotonic()-started})
            native.save(root/'archive.json', archive)
            native.save(root/'trials.json', trials)
            native.save(root/'trace.json', trace)
            native.save(root/'rule-counts.json', dict(counts))
            native.save(root/'eligible-sites.json', {key: sorted(value) for key, value in eligible.items()})
    prior = json.loads((state['parent']/'search-result.json').read_text())
    result = {'completed_utc': base.utc(), 'slots': len(trials), 'new_slots': len(trials)-start_slot,
              'emitted': len(archive), 'best_development': best, 'warm_start_best': original_best,
              'maximum_depth': max(row['depth'] for row in archive.values()), 'counts': dict(counts),
              'wall_seconds': prior['wall_seconds']+time.monotonic()-started,
              'extension_wall_seconds': time.monotonic()-started,
              'segments': [{'name': 'original-two-hour-budget', 'slots': start_slot,
                            'result_file': 'budget-amendments/three-hours/parent-state/search-result.json'},
                           {'name': 'three-hours-warm-start', 'slots': len(trials)-start_slot,
                            'seed': config['seed'], 'maximum_depth': config['maximum_depth']}],
              'parent_record_prefix_verified': True, 'literal_journal_prefix_verified': True,
              'scope': 'Cumulative development search. Independent qualification follows.'}
    native.save(root/'extension-result.json', result)
    integrity.seal(root/'extension-result.json')
    native.save(root/'search-result.json', result)
    native.save(root/'budget-amendments/three-hours/history-prefix-verification.json', {
        'checked_utc': base.utc(), 'parent_records': state['parent_count'], 'total_records': len(trials),
        'parent_bytes_sha256': hashlib.sha256(state['parent_bytes']).hexdigest(),
        'literal_journal_prefix': True, 'exact_parent_record_serialization': True})
    return result
