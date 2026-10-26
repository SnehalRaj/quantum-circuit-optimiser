"""A bounded native swarm with one frozen, independently audited finalist."""
from collections import Counter
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import sys
import time

from . import full_integrity as integrity, full_native as native, full_path

BASE_SETTINGS = {
    'CP_CARRY_MEASURED': 1, 'CP_AND_MEASURED': 1, 'CP_FOLD_FUSED': 1,
    'CP_WALK_SPLIT': 1, 'CP_FOLD_SPLIT': 1, 'CP_FOLD_STREAMED': 0,
    'CP_INLET_EARLY': 0, 'CP_INLINE_PLUS2': 0,
    'PP_WALK_MAX_QUBITS': 1332, 'CP_FOLD_CAP': 1332,
    'PP_REPLAY_FOLD_WINDOW': 64, 'PP_REPLAY_FOLD_WINDOW_MUL': 64,
    'PP_REPLAY_CHUNK_COMPARE': 32, 'PP_REPLAY_FLAG_COMPARE': 34,
    'FOLD_GUARD': 32, 'ERASE_COMPARE': 28,
}
BOOLEANS = {key for key, value in BASE_SETTINGS.items() if key.startswith('CP_') and key != 'CP_FOLD_CAP'}
BOUNDS = {key: ((1200, 1400) if key in ('PP_WALK_MAX_QUBITS', 'CP_FOLD_CAP')
                else (48, 80) if 'WINDOW' in key else (16, 64))
          for key in BASE_SETTINGS if key not in BOOLEANS}


def utc():
    return datetime.now(timezone.utc).isoformat()


def timestamp(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_execution_source(root):
    """Match the live controller, CLI and imported local modules to saved hashes."""
    root = Path(root).resolve()
    manifest = integrity.read_sealed(root/'execution-source.json')
    project = Path(__file__).resolve().parents[2]
    for relative, record in manifest['files'].items():
        native.assert_hash(root/record['file'], record['sha256'])
        native.assert_hash(project/relative, record['sha256'])
    tools = integrity.read_sealed(root/'tools.json')
    for name, module in list(sys.modules.items()):
        filename = getattr(module, '__file__', None)
        if not name.startswith('circuitopt') or not filename:
            continue
        path = Path(filename).resolve()
        if not path.is_relative_to(project):
            raise ValueError('An imported circuitopt module lies outside the frozen project')
        relative = str(path.relative_to(project))
        expected = manifest['files'].get(relative, {}).get('sha256') or tools['native_sources'].get(relative)
        if expected is None:
            raise ValueError('An imported local module has no frozen source identity: ' + relative)
        native.assert_hash(path, expected)


def recipe(**changes):
    settings = {**BASE_SETTINGS, **changes}
    return {'backend': 'pingpong', 'settings': settings, 'fold_sites': {},
            'inlet_sites': [], 'inline_sites': []}


def validate(value):
    if set(value) != {'backend', 'settings', 'fold_sites', 'inlet_sites', 'inline_sites'}:
        raise ValueError('Unknown native recipe field')
    if value['backend'] not in ('original', 'pingpong'):
        raise ValueError('Unknown native backend')
    if value['backend'] == 'original':
        if value['settings'] or value['fold_sites'] or value['inlet_sites'] or value['inline_sites']:
            raise ValueError('Original control cannot carry ping-pong settings')
        return copy.deepcopy(value)
    if set(value['settings']) != set(BASE_SETTINGS):
        raise ValueError('Every admitted setting must be explicit')
    for name, setting in value['settings'].items():
        if type(setting) is not int:
            raise ValueError('Settings must be integers')
        if name in BOOLEANS:
            if setting not in (0, 1):
                raise ValueError('Boolean setting outside its domain')
        elif not BOUNDS[name][0] <= setting <= BOUNDS[name][1]:
            raise ValueError('Scalar setting outside its domain')
    if any(not isinstance(k, str) or not k.isdigit() or type(v) is not int or v not in (0, 1, 2)
           for k, v in value['fold_sites'].items()):
        raise ValueError('Invalid fold site mode')
    for name in ('inlet_sites', 'inline_sites'):
        sites = value[name]
        if not isinstance(sites, list) or sites != sorted(set(sites)) or any(type(i) is not int or i < 0 for i in sites):
            raise ValueError('Site lists must contain distinct sorted nonnegative integers')
    return copy.deepcopy(value)


def native_arguments(value):
    value = validate(value)
    arguments = ['--backend', value['backend']]
    if value['backend'] == 'original':
        return arguments
    for name, setting in sorted(value['settings'].items()):
        arguments += ['--set', f'{name}={setting}']
    arguments += ['--set', 'CP_PROFILE=1']
    if value['fold_sites']:
        arguments += ['--set', 'CP_FOLD_SITES=' + ','.join(f'{k}:{v}' for k, v in
                       sorted(value['fold_sites'].items(), key=lambda item: int(item[0])))]
    for field, setting in [('inlet_sites', 'CP_INLET_SITES'), ('inline_sites', 'CP_INLINE_SITES')]:
        if value[field]:
            arguments += ['--set', setting + '=' + ','.join(map(str, value[field]))]
    return arguments


def parse_profile(stderr):
    sites = {}
    for line in stderr.splitlines():
        fields = line.split('\t')
        if fields[0] == 'SWARM_SITE' and len(fields) == 6:
            sid, width, active, mode = map(int, fields[1:5])
            prior = sites.get(sid, {})
            sites[sid] = {'id': sid, 'width': width, 'active': active, 'mode': mode,
                          'chunk': None if fields[5] == 'none' else int(fields[5]),
                          'retired': prior.get('retired', False), 'inline': prior.get('inline', False)}
        elif fields[0] in ('SWARM_RETIRE', 'SWARM_INLINE') and len(fields) == 3:
            sid = int(fields[1])
            sites.setdefault(sid, {'id': sid})['retired' if fields[0] == 'SWARM_RETIRE' else 'inline'] = fields[2] == '1'
    return sorted(sites.values(), key=lambda row: row['id'])


@contextmanager
def deadline_calls(deadline):
    """Bound the existing process runner without changing checker semantics."""
    original = native.command
    def bounded(root, argv, **kwargs):
        remaining = deadline - time.time() - 3
        if remaining <= 0:
            raise TimeoutError('The absolute campaign deadline has arrived')
        kwargs['timeout'] = min(kwargs.get('timeout', 1800), remaining)
        return original(root, argv, **kwargs)
    native.command = bounded
    try:
        yield
    finally:
        native.command = original


def freeze(project, root, generator, checker, *, cutoff='2026-10-23T08:45:00Z',
           deadline='2026-10-23T09:42:17Z', threads=4, proposals=512, seed=731):
    project, root = Path(project).resolve(), Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root/'config.json').exists() or (root/'tools').exists():
        raise FileExistsError('The campaign is already initialized')
    (root/'tools').mkdir()
    executables = {}
    for name, source in [('full-point-add-generator', generator), ('study-check', checker)]:
        target = root/'tools'/name
        shutil.copy2(source, target)
        executables[name] = {'file': str(target.relative_to(root)), 'sha256': native.file_hash(target)}
    sources = {}
    paths = [p for p in (project/'native').rglob('*') if p.is_file() and
             not any(part in ('target', 'logs', '__pycache__') for part in p.relative_to(project).parts)]
    paths += list((project/'src/circuitopt').rglob('*.py'))
    paths += [project/'tools/run_swarm_native.py', project/'tests/test_swarm_native.py']
    for source in sorted(paths):
        relative = source.relative_to(project)
        target = root/'source'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        sources[str(relative)] = native.file_hash(source)
    official_sources = {}
    official = root/'official-tool'
    if (official/'official-point-add-check').exists():
        executables['official-point-add-check'] = {'file': 'official-tool/official-point-add-check',
            'sha256': native.file_hash(official/'official-point-add-check')}
        for source in sorted(official.rglob('*')):
            if source.is_file() and (source.suffix == '.rs' or source.name in ('Cargo.toml', 'Cargo.lock', 'NOTICE')):
                official_sources[str(source.relative_to(root))] = native.file_hash(source)
    native.save(root/'tools.json', {'executables': executables, 'native_sources': sources,
                                  'official_sources': official_sources})
    integrity.seal(root/'tools.json')
    labels = {stage: 'grammar-swarm/' + stage + '/' + secrets.token_hex(24)
              for stage in ('screen', 'development512', 'selection32768')}
    config = {'schema': 1, 'created_utc': utc(), 'search_cutoff_utc': cutoff,
              'deadline_utc': deadline, 'threads': threads, 'proposal_slots': proposals,
              'seed': seed, 'maximum_depth': 64, 'screen_pairs': 64, 'promising_pairs': 512,
              'selection_pairs': 32768, 'shortlist_size': 4, 'audit_pairs': 1000000,
              'audit_chunks': 16, 'audit_chunk_max': 65536, 'streams': 2,
              'epsilon': 1e-5, 'confidence': .95, 'labels': labels,
              'selection': 'Lowest 32768-pair S among candidates with zero observed selection failures.',
              'audit_policy': 'Freeze one endpoint before fresh inputs. Complete 1000000 pairs. No replacement or extra draws after the audit.',
              'prior_qualified_control': {'S': 1309376572, 'status': 'Prior campaign evidence; separate from this new audit.'},
              'hypotheses': [
                  {'id': 'N-search-exact', 'question': 'Can scoped storage edits reduce complete point-addition S?',
                   'invariant': 'Keep the full arithmetic map, phase correction and clean workspace; preserve all rounding and input sampling.',
                   'prediction': 'Streamed repair, early inlet erasure and inline selectors can reduce Q; measure global T and Q.',
                   'falsifier': 'A failed fixed-checker channel on a development pair.',
                   'acceptance': 'Lower measured S and one frozen finalist passes the fixed independent 1e-5 audit.'},
                  {'id': 'N-search-scalars', 'question': 'Do caps, windows and compare widths combine with exact site edits?',
                   'invariant': 'Keep round counts and fixed checker unchanged; count all approximation failures.',
                   'prediction': 'Tighter allocations may lower Q or T but can increase failures.',
                   'falsifier': 'One development failure or final audit bound above 1e-5.',
                   'acceptance': 'The same frozen-finalist acceptance rule.'}],
              'scope': 'Actual emitted complete 256-bit generic-domain point-addition circuits.'}
    native.save(root/'config.json', config)
    integrity.seal(root/'config.json')
    with deadline_calls(timestamp(deadline)):
        directory = root/'checker-controls'
        proc, _ = native.command(root, [root/'tools/study-check', '--self-test', '--out', directory/'result.json'], directory=directory)
        if proc.returncode or not json.loads((directory/'result.json').read_text())['passed']:
            raise RuntimeError('Fixed checker diagnostic controls failed')
    return config


def emit(root, value):
    root = Path(root).resolve()
    value = validate(value)
    cid = identity(value)
    directory = root/'circuits'/cid
    if (directory/'artifact.json').exists():
        artifact = json.loads((directory/'artifact.json').read_text())
        native.assert_hash(root/artifact['operation_file'], artifact['sha256'])
        return artifact
    directory.mkdir(parents=True, exist_ok=False)
    native.save(directory/'recipe.json', value)
    tools = native.load_tools(root)
    binary = root/tools['full-point-add-generator']['file']
    native.assert_hash(binary, tools['full-point-add-generator']['sha256'])
    proc, timing = native.command(root, [binary, *native_arguments(value), '--out', directory/'ops.bin'],
                                  directory=directory, sandbox=True)
    if proc.returncode:
        raise RuntimeError('Native emission failed: ' + proc.stderr[-1800:])
    profile = parse_profile(proc.stderr)
    native.save(directory/'profile.json', profile)
    artifact = {'id': cid, 'recipe': value, 'sha256': native.file_hash(directory/'ops.bin'),
                'operation_file': str((directory/'ops.bin').relative_to(root)),
                'bytes': (directory/'ops.bin').stat().st_size, 'emission': timing,
                'profile_file': str((directory/'profile.json').relative_to(root))}
    native.save(directory/'artifact.json', artifact)
    return artifact


def checked_evaluate(root, artifact, *, label, pairs, name, threads, fresh=False):
    directory = Path(root)/'evaluations'/name
    if not (directory/'result.json').exists():
        native.evaluate(root, artifact, label=label, pairs=pairs, name=name, threads=threads, fresh=fresh)
    result, proof = integrity.validate_evaluation(root, directory, artifact, label=label, pairs=pairs, streams=2)
    native.save(directory/'integrity.json', proof)
    return result


def global_seeds():
    s3 = recipe(CP_FOLD_SPLIT=0)
    pc001 = recipe()
    frontier = recipe(PP_WALK_MAX_QUBITS=1316, CP_FOLD_CAP=1316)
    rows = [('S3_split_off', s3), ('PC001_cap1332', pc001), ('prior_frontier_cap1316', frontier)]
    for streamed, inlet, inline in [(1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1)]:
        child = copy.deepcopy(frontier)
        child['settings'].update(CP_FOLD_STREAMED=streamed, CP_INLET_EARLY=inlet, CP_INLINE_PLUS2=inline)
        rows.append((f'global_stream{streamed}_inlet{inlet}_inline{inline}', child))
    for cap in (1308, 1300, 1292, 1284, 1268, 1252, 1236, 1220, 1204):
        rows.append((f'all_rules_cap{cap}', recipe(CP_FOLD_STREAMED=1, CP_INLET_EARLY=1,
                     CP_INLINE_PLUS2=1, PP_WALK_MAX_QUBITS=cap, CP_FOLD_CAP=cap)))
    return rows


def preflight(root):
    root = Path(root).resolve()
    config = integrity.read_sealed(root/'config.json')
    original = {'backend': 'original', 'settings': {}, 'fold_sites': {}, 'inlet_sites': [], 'inline_sites': []}
    controls = [('original_cost_reference', original)] + global_seeds()[:3] + [global_seeds()[9]]
    rows = []
    with deadline_calls(timestamp(config['search_cutoff_utc'])):
        for name, value in controls:
            artifact = emit(root, value)
            result = checked_evaluate(root, artifact, label=config['labels']['screen'],
                       pairs=config['screen_pairs'], name='screen-'+artifact['id'], threads=config['threads'])
            row = {'name': name, 'artifact': artifact, 'result': result}
            rows.append(row)
            native.save(root/'preflight.json', rows)
            print(name, result['Q'], result['T'], result['S'], result['failed_inputs'], flush=True)
    return rows


def legal_actions(value, profile, eligible):
    actions = []
    for row in profile:
        if 'width' not in row:
            continue
        # The profile names actual emitted occurrences. Guards inside Rust decide fit.
        if row.get('chunk') is not None or row['active'] + row['width'] - 3 >= value['settings']['CP_FOLD_CAP']:
            for mode in (0, 1, 2):
                if mode != row['mode']:
                    actions.append({'rule': 'fold_site', 'site': row['id'], 'value': mode})
    for field in ('inlet_sites', 'inline_sites'):
        if value['settings']['CP_INLET_EARLY' if field == 'inlet_sites' else 'CP_INLINE_PLUS2']:
            continue
        for sid in sorted(eligible[field]):
            actions.append({'rule': field, 'site': sid, 'value': sid not in value[field]})
    for field in ('CP_FOLD_SPLIT', 'CP_FOLD_STREAMED', 'CP_INLET_EARLY', 'CP_INLINE_PLUS2'):
        actions.append({'rule': 'global', 'field': field, 'value': 1-value['settings'][field]})
    for field, (low, high) in BOUNDS.items():
        steps = (-16, -8, -4, -2, 2, 4, 8, 16) if 'QUBITS' in field or field == 'CP_FOLD_CAP' else (-4, -1, 1, 4)
        for step in steps:
            number = value['settings'][field]+step
            if low <= number <= high:
                actions.append({'rule': 'scalar', 'field': field, 'value': number})
    return actions


def apply(value, action):
    child = copy.deepcopy(value)
    rule = action['rule']
    if rule == 'fold_site':
        child['fold_sites'][str(action['site'])] = action['value']
    elif rule in ('inlet_sites', 'inline_sites'):
        sites = set(child[rule])
        if action['value']:
            sites.add(action['site'])
        else:
            sites.discard(action['site'])
        child[rule] = sorted(sites)
    elif rule in ('scalar', 'global'):
        child['settings'][action['field']] = action['value']
    else:
        raise ValueError('Unknown native mutation')
    return validate(child)


def combine(left, right, rng):
    child = copy.deepcopy(left)
    for field in BASE_SETTINGS:
        if rng.random() < .5:
            child['settings'][field] = right['settings'][field]
    child['fold_sites'].update(right['fold_sites'])
    for field in ('inlet_sites', 'inline_sites'):
        child[field] = sorted(set(left[field]) | set(right[field]))
    return validate(child)


def architecture(value):
    return tuple(value['settings'][field] for field in
                 ('CP_FOLD_SPLIT', 'CP_FOLD_STREAMED', 'CP_INLET_EARLY', 'CP_INLINE_PLUS2'))


def pick_parent(archive, rng, maximum_depth, slot):
    viable = [row for row in archive.values() if row.get('screen', {}).get('failed_inputs') == 0
              and row['depth'] < maximum_depth and row['recipe']['backend'] == 'pingpong']
    if not viable:
        raise ValueError('No valid native parent remains')
    if slot % 3 == 0:
        # Keep a real, potentially neutral edit chain alive through depth 64.
        depth = max(row['depth'] for row in viable)
        return rng.choice([row for row in viable if row['depth'] == depth])
    elite = sorted(viable, key=lambda row: row['screen']['S'])[:12]
    if rng.random() < .5:
        return rng.choice(elite)
    buckets = {}
    for row in sorted(viable, key=lambda row: row['screen']['S']):
        buckets.setdefault(architecture(row['recipe']), row)
    return rng.choice(list(buckets.values()))


def action_activity(action, profile):
    if action['rule'] not in ('fold_site', 'inlet_sites', 'inline_sites'):
        return None
    row = next((row for row in profile if row['id'] == action['site']), {})
    if action['rule'] == 'fold_site':
        return row.get('mode') == action['value'] and (action['value'] == 0 or row.get('chunk') is not None)
    return row.get('retired' if action['rule'] == 'inlet_sites' else 'inline', False) == action['value']


def search(root):
    root = Path(root).resolve()
    verify_execution_source(root)
    config = integrity.read_sealed(root/'config.json')
    with (root/'search-started.json').open('x') as reservation:
        json.dump({'started_utc': utc(), 'proposal_slots': config['proposal_slots'],
                   'execution_source_sha256': native.file_hash(root/'execution-source.json'),
                   'policy': 'One search invocation. Preserve every trial; reject reruns.'}, reservation, indent=2)
        reservation.write('\n')
        reservation.flush()
        os.fsync(reservation.fileno())
    cutoff = timestamp(config['search_cutoff_utc'])
    deadline = timestamp(config['deadline_utc'])
    rng, counts, archive, trials, trace = random.Random(config['seed']), Counter(), {}, [], []
    eligible = {'inlet_sites': set(), 'inline_sites': set()}
    seen = set()
    seeds = global_seeds()
    original = {'backend': 'original', 'settings': {}, 'fold_sites': {}, 'inlet_sites': [], 'inline_sites': []}
    queue = [('original_cost_reference', original)] + seeds
    best = None
    started = time.monotonic()
    with deadline_calls(min(deadline, cutoff)):
        for slot in range(config['proposal_slots']):
            if time.time() >= cutoff-5:
                break
            record = {'slot': slot, 'started_utc': utc()}
            parent_ids = []
            try:
                if queue:
                    label, value = queue.pop(0)
                    action = {'rule': 'seed', 'name': label}
                    if label not in ('original_cost_reference', 'S3_split_off', 'PC001_cap1332', 'prior_frontier_cap1316'):
                        parent_ids = [identity(seeds[2][1])]
                else:
                    parent = pick_parent(archive, rng, config['maximum_depth'], slot)
                    parent_ids = [parent['id']]
                    profile = json.loads((root/parent['artifact']['profile_file']).read_text())
                    actions = legal_actions(parent['recipe'], profile, eligible)
                    for opportunity in actions:
                        counts[opportunity['rule']+'/opportunity'] += 1
                    if rng.random() < .16:
                        other = pick_parent(archive, rng, config['maximum_depth'], slot+1)
                        parent_ids.append(other['id'])
                        value = combine(parent['recipe'], other['recipe'], rng)
                        action = {'rule': 'combine', 'parents': parent_ids}
                    else:
                        # First choose a rule family, then a location within it.
                        groups = {}
                        for option in actions:
                            groups.setdefault(option['rule'], []).append(option)
                        family = rng.choices(list(groups), weights=[4 if key == 'fold_site' else 2 if key in ('scalar', 'inlet_sites', 'inline_sites') else 1 for key in groups])[0]
                        action = rng.choice(groups[family])
                        value = apply(parent['recipe'], action)
                cid = identity(value)
                record.update(id=cid, parents=parent_ids, action=action)
                counts[action['rule']+'/attempted'] += 1
                if cid in seen:
                    counts[action['rule']+'/duplicate'] += 1
                    record['status'] = 'duplicate'
                else:
                    seen.add(cid)
                    depth = 0 if not parent_ids else max(archive[p]['depth'] for p in parent_ids)+1
                    if depth > config['maximum_depth']:
                        raise ValueError('Maximum ancestry depth exceeded')
                    artifact = emit(root, value)
                    profile = json.loads((root/artifact['profile_file']).read_text())
                    for row in profile:
                        if row.get('retired'):
                            eligible['inlet_sites'].add(row['id'])
                        if row.get('inline'):
                            eligible['inline_sites'].add(row['id'])
                    active = action_activity(action, profile)
                    if active is False:
                        counts[action['rule']+'/inactive'] += 1
                    counts[action['rule']+'/applied'] += 1
                    record['reused_screen_evidence'] = (root/'evaluations'/('screen-'+cid)/'result.json').exists()
                    screen = checked_evaluate(root, artifact, label=config['labels']['screen'], pairs=config['screen_pairs'],
                              name='screen-'+cid, threads=config['threads'])
                    entry = {'id': cid, 'recipe': value, 'parents': parent_ids, 'depth': depth,
                             'action': action, 'artifact': artifact, 'screen': screen,
                             'active_scoped_edit': active, 'cost_only_reference': value['backend'] == 'original'}
                    archive[cid] = entry
                    record.update(status='failed' if screen['failed_inputs'] else 'pass', depth=depth,
                                  S=screen['S'], Q=screen['Q'], T=screen['T'],
                                  failed_inputs=screen['failed_inputs'], active_scoped_edit=active)
                    record['changed_gate_stream'] = not parent_ids or any(
                        artifact['sha256'] != archive[parent]['artifact']['sha256'] for parent in parent_ids)
                    if not record['changed_gate_stream']:
                        counts[action['rule']+'/same_gate_stream'] += 1
                    if value['backend'] != 'original' and not screen['failed_inputs']:
                        if best is None or screen['S'] < archive[best]['screen']['S']:
                            best = cid
                            record['status'] = 'improvement'
                            # A larger common packet screens every improving endpoint.
                            if time.time() < cutoff-20:
                                entry['development512'] = checked_evaluate(root, artifact,
                                    label=config['labels']['development512'], pairs=config['promising_pairs'],
                                    name='development512-'+cid, threads=config['threads'])
                    if screen['failed_inputs']:
                        counts[action['rule']+'/rejected'] += 1
                    print(json.dumps({k: record[k] for k in ('slot', 'status', 'S', 'Q', 'T', 'depth')}) , flush=True)
            except Exception as exc:
                record.update(status='instrument_error' if not isinstance(exc, ValueError) else 'invalid',
                              error=type(exc).__name__, message=str(exc)[-1500:])
                counts[record.get('action', {}).get('rule', 'unknown')+'/error'] += 1
                print(json.dumps(record), flush=True)
            record['completed_utc'] = utc()
            trials.append(record)
            trace.append({'slot': slot, 'best_id': best, 'best_S': archive[best]['screen']['S'] if best else None,
                          'status': record['status'], 'elapsed_seconds': time.monotonic()-started})
            native.save(root/'archive.json', archive)
            native.save(root/'trials.json', trials)
            native.save(root/'trace.json', trace)
            native.save(root/'rule-counts.json', dict(counts))
            native.save(root/'eligible-sites.json', {key: sorted(value) for key, value in eligible.items()})
    result = {'completed_utc': utc(), 'slots': len(trials), 'emitted': len(archive), 'best_development': best,
              'maximum_depth': max((row['depth'] for row in archive.values()), default=0),
              'counts': dict(counts), 'wall_seconds': time.monotonic()-started,
              'scope': 'Development search. Frozen independent qualification follows.'}
    native.save(root/'search-result.json', result)
    return result


def select(root):
    root = Path(root).resolve()
    verify_execution_source(root)
    if (root/'finalist.json').exists():
        return integrity.read_sealed(root/'finalist.json')
    config = integrity.read_sealed(root/'config.json')
    archive = json.loads((root/'archive.json').read_text())
    candidates = [row for row in archive.values() if not row['cost_only_reference'] and
                  not row['screen']['failed_inputs'] and
                  not row.get('development512', {}).get('failed_inputs', 0)]
    unique = {}
    for row in sorted(candidates, key=lambda row: (row['screen']['S'], row['id'])):
        unique.setdefault(row['artifact']['sha256'], row)
    shortlist = list(unique.values())[:config['shortlist_size']]
    # Measure both controls on the same development cohort before finalist freeze.
    controls = {identity(value) for name, value in global_seeds()[:3]
                if name in ('S3_split_off', 'prior_frontier_cap1316')}
    selected_ids = {row['id'] for row in shortlist}
    shortlist.extend(archive[cid] for cid in sorted(controls) if cid in archive and cid not in selected_ids)
    if (root/'shortlist.json').exists():
        saved = integrity.read_sealed(root/'shortlist.json')
        if saved['ids'] != [row['id'] for row in shortlist]:
            raise ValueError('The frozen shortlist changed')
    else:
        native.save(root/'shortlist.json', {'ids': [row['id'] for row in shortlist], 'frozen_utc': utc(),
                    'pairs': config['selection_pairs'], 'label': config['labels']['selection32768']})
        integrity.seal(root/'shortlist.json')
    results = []
    with deadline_calls(timestamp(config['deadline_utc'])-2400):
        for row in shortlist:
            result = checked_evaluate(root, row['artifact'], label=config['labels']['selection32768'],
                        pairs=config['selection_pairs'], name='selection32768-'+row['id'], threads=config['threads'])
            results.append(result)
            native.save(root/'selection-results.json', results)
            print('selection', row['id'][:12], result['S'], result['failed_inputs'], flush=True)
    passing = [row for row in results if row['failed_inputs'] == 0]
    if not passing:
        native.save(root/'selection-stop.json', {'status': 'no_passing_finalist', 'results': results, 'time': utc()})
        return None
    selected = min(passing, key=lambda row: (row['S'], row['id']))
    artifact = archive[selected['id']]['artifact']
    finalist = {'artifact': artifact, 'selection': selected, 'frozen_utc': utc(),
                'epsilon': config['epsilon'], 'confidence': config['confidence'],
                'audit_pairs': config['audit_pairs'], 'streams': config['streams'],
                'no_replacement': True, 'ancestry_depth': archive[selected['id']]['depth']}
    native.save(root/'finalist.json', finalist)
    integrity.seal(root/'finalist.json')
    return finalist


def audit(root):
    root = Path(root).resolve()
    verify_execution_source(root)
    previous = integrity.read_sealed(root/'audit-result.json') if (root/'audit-result.json').exists() else None
    config = integrity.read_sealed(root/'config.json')
    finalist = integrity.read_sealed(root/'finalist.json')
    artifact = finalist['artifact']
    contract_path = root/'audit-contract.json'
    if contract_path.exists():
        contract = integrity.read_sealed(contract_path)
    else:
        remaining, chunks = config['audit_pairs'], []
        for i in range(config['audit_chunks']):
            size = min(config['audit_chunk_max'], math.ceil(remaining/(config['audit_chunks']-i)))
            chunks.append({'pairs': size, 'label': 'grammar-swarm/independent-final/' + secrets.token_hex(24)})
            remaining -= size
        contract = {'stage': 'final', 'pairs': config['audit_pairs'], 'chunks': chunks,
                    'circuit_ids': [artifact['id']], 'streams': 2, 'epsilon': config['epsilon'],
                    'confidence': config['confidence'], 'created_utc': utc(),
                    'stopping': 'Complete all predeclared chunks. Never replace the finalist or add draws.'}
        native.save(contract_path, contract)
        integrity.seal(contract_path)
    integrity.validate_contract(contract, pairs=config['audit_pairs'], circuit_ids=[artifact['id']], stage='final',
                                streams=2, epsilon=config['epsilon'], confidence=config['confidence'])
    if len(contract['chunks']) != 16 or max(chunk['pairs'] for chunk in contract['chunks']) > 65536:
        raise ValueError('Independent chunk plan changed')
    results = []
    with deadline_calls(timestamp(config['deadline_utc'])-180):
        for i, chunk in enumerate(contract['chunks']):
            if previous and not (root/'evaluations'/f'independent-final-{i:02d}'/'result.json').exists():
                raise ValueError('Completed audit has missing chunk evidence')
            result = checked_evaluate(root, artifact, label=chunk['label'], pairs=chunk['pairs'],
                         name=f'independent-final-{i:02d}', threads=config['threads'], fresh=True)
            results.append(result)
            if previous is None:
                native.save(root/'audit-progress.json', {'completed_pairs': sum(r['pairs'] for r in results),
                            'planned_pairs': config['audit_pairs'], 'chunks': results, 'updated_utc': utc()})
            print('audit', i+1, '/', len(contract['chunks']), 'failed', result['failed_inputs'], flush=True)
    pairs = sum(row['pairs'] for row in results)
    if pairs != config['audit_pairs'] or any(row['Q'] != results[0]['Q'] for row in results):
        raise ValueError('Audit denominator or circuit width changed')
    failures = sum(row['failed_inputs'] for row in results)
    mean = sum(row['T']*row['pairs'] for row in results)/pairs
    bound = full_path.failure_upper(failures, pairs, 1-config['confidence'])
    result = {'id': artifact['id'], 'artifact_sha256': artifact['sha256'], 'pairs': pairs, 'streams': 2,
              'Q': results[0]['Q'], 'T': mean, 'S': results[0]['Q']*int(mean+.5),
              'failed_inputs': failures, 'failed_shots': sum(row['failed_shots'] for row in results),
              'channel_failed_inputs': {key: sum(row['channel_failed_inputs'][key] for row in results)
                                        for key in results[0]['channel_failed_inputs']},
              'epsilon_upper95': bound, 'qualified': bound <= config['epsilon'], 'chunks': results,
              'completed_utc': utc(), 'scope': 'One frozen finalist on exactly one million fresh input pairs under two streams.'}
    if previous is not None:
        for field in ('id', 'artifact_sha256', 'pairs', 'streams', 'Q', 'T', 'S', 'failed_inputs',
                      'failed_shots', 'channel_failed_inputs', 'epsilon_upper95', 'qualified'):
            if result[field] != previous[field]:
                raise ValueError('Completed audit disagrees with its complete saved evidence: ' + field)
        return previous
    native.save(root/'audit-result.json', result)
    integrity.seal(root/'audit-result.json')
    return result


def official(root):
    """Reserve and run one pinned local official check after qualification."""
    root = Path(root).resolve()
    verify_execution_source(root)
    config = integrity.read_sealed(root/'config.json')
    result = integrity.read_sealed(root/'audit-result.json')
    if not result['qualified']:
        native.save(root/'official-skipped.json', {'reason': 'The independent audit did not qualify.', 'time': utc()})
        return None
    finalist = integrity.read_sealed(root/'finalist.json')
    artifact = finalist['artifact']
    manifest = integrity.read_sealed(root/'tools.json')
    tool = manifest['executables']['official-point-add-check']
    directory = root/'official-tool'
    native.assert_hash(root/tool['file'], tool['sha256'])
    for path, expected in manifest['official_sources'].items():
        native.assert_hash(root/path, expected)
    protected_source = directory/'src/main.rs'
    expected_source = 'b35314bc47a5f8eecbf60459e6c76c9b43aa034eeb9b7ec3421655d74f9e890b'
    native.assert_hash(protected_source, expected_source)
    native.assert_hash(root/artifact['operation_file'], artifact['sha256'])
    if (root/'official-result.json').exists():
        previous = integrity.read_sealed(root/'official-result.json')
        native.assert_hash(directory/'ops.bin', artifact['sha256'])
        for filename, output in previous['outputs'].items():
            path = directory/filename
            if path.exists() != output['present']:
                raise ValueError('Official output presence changed: ' + filename)
            if output['present']:
                native.assert_hash(path, output['sha256'])
        if previous['score'] is not None and json.loads((directory/'score.json').read_text()) != previous['score']:
            raise ValueError('Official score summary disagrees with its saved output')
        reservation = json.loads((directory/'attempt-reservation.json').read_text())
        if reservation['ops_sha256'] != artifact['sha256'] or reservation['binary_sha256'] != tool['sha256']:
            raise ValueError('Official attempt reservation changed')
        return previous
    for filename in ('score.json', 'results.tsv', 'ops.bin', 'attempt-reservation.json'):
        if (directory/filename).exists():
            raise FileExistsError('The one-attempt official directory already has ' + filename)
    reservation = {'attempt': 1, 'maximum_attempts': 1, 'reserved_utc': utc(),
                   'id': artifact['id'], 'ops_sha256': artifact['sha256'],
                   'binary_sha256': tool['sha256'], 'source_sha256': expected_source,
                   'no_rerun': 'Preserve pass, fail, timeout or instrument error. Never retry.'}
    with (directory/'attempt-reservation.json').open('x') as stream:
        json.dump(reservation, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.link(root/artifact['operation_file'], directory/'ops.bin')
    with deadline_calls(timestamp(config['deadline_utc'])-15):
        proc, timing = native.command(root, [root/tool['file'], '--note',
            'one local check of the frozen swarm finalist; fixed nonce; no remote submission'],
            directory=directory, sandbox=True)
    observed = {}
    for key, label in [('shots', 'tested shots'), ('classical_mismatches', 'classical mismatches'),
                       ('phase_garbage_batches', 'phase-garbage batches'),
                       ('ancilla_garbage_batches', 'ancilla-garbage batches')]:
        matched = re.search(re.escape(label) + r'\s*:\s*(\d+)', proc.stdout)
        observed[key] = int(matched.group(1)) if matched else None
    outputs = {}
    for filename in ('score.json', 'results.tsv'):
        path = directory/filename
        outputs[filename] = {'present': path.exists(), 'sha256': native.file_hash(path) if path.exists() else None}
    score = json.loads((directory/'score.json').read_text()) if outputs['score.json']['present'] else None
    lines = (directory/'results.tsv').read_text().splitlines() if outputs['results.tsv']['present'] else []
    correctness = lines[0].split('\t')[6] if len(lines) == 1 and len(lines[0].split('\t')) > 6 else None
    native.assert_hash(directory/'ops.bin', artifact['sha256'])
    native.assert_hash(protected_source, expected_source)
    passed = (proc.returncode == 0 and observed['shots'] == 9024 and
              all(observed[key] == 0 for key in ('classical_mismatches', 'phase_garbage_batches', 'ancilla_garbage_batches'))
              and correctness == 'OK' and score is not None)
    receipt = {'attempt': 1, 'official_check_passed': passed, 'returncode': proc.returncode,
               'observed': observed, 'score': score, 'results_correctness': correctness,
               'outputs': outputs, 'process': timing, 'completed_utc': utc(),
               'scope': 'One pinned local 9024-shot official check. No remote submission.'}
    native.save(root/'official-result.json', receipt)
    integrity.seal(root/'official-result.json')
    return receipt

