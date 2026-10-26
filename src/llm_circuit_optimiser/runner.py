"""Run the same two stages for offline and model-assisted proposals."""
from pathlib import Path
import os
import random
import shutil
import sys
import time

from .common import ROOT, digest, load, pareto, run, save, sha256
from .models import select

EPSILON = 0.001
DEFAULT_AUDIT_PAIRS = 32768


def qualify_native(output, *, pairs=100000, label='lco-native-qualification-20261026-v1', offline=False):
    """Run the four paired compiler controls with one frozen confidence allowance."""
    output = Path(output).resolve()
    tools = native_tools(output, offline=offline)
    contract = {'pairs': pairs, 'label': label, 'policies': ['mfs', 'mfsu', 'mfsr', 'mfsur'],
                'streams': 2, 'epsilon': EPSILON, 'family_alpha': 0.05, 'alpha_per_test': 0.0125,
                'native_source_sha256': tools['source_sha256'], 'tools': tools['binary_sha256']}
    path = output / 'contract.json'
    if path.exists() and load(path) != contract:
        raise ValueError('The native qualification directory has a different frozen contract')
    save(path, contract)
    rows = []
    for policy in contract['policies']:
        result = small_stage(policy, tools, output / policy, pairs, label, 0.0125)
        result['admission'] = admit_small(policy, result['emission'], inherited_policy='mfs')
        rows.append({'policy': policy, **result})
        print(f'Native {policy}: {result["audit"]["failed_inputs"]}/{pairs} failed inputs', file=sys.stderr, flush=True)
    result = {'contract': contract, 'rows': rows, 'qualified': all(r['qualified'] for r in rows)}
    save(output / 'results.json', result)
    return result


def audit_qualified(audit):
    upper = audit.get('upper_bound', audit.get('binomial_upper'))
    if type(upper) not in (float, int) or not 0 <= upper <= 1:
        raise ValueError('The fixed checker did not return a probability bound')
    return upper <= EPSILON and audit.get('protocol_valid', True)


def admit_small(policy, receipt, *, inherited_policy='none', baseline_receipt=None):
    if receipt.get('guards_active') is not True:
        raise ValueError('The small-stage compiler guards must remain active')
    counts = receipt.get('activation_counts', {})
    enabled = set() if policy == 'none' else set(policy)
    inherited = set() if inherited_policy == 'none' else set(inherited_policy)
    for lever in enabled - inherited:
        if counts.get(lever, 0) <= 0:
            raise ValueError(f'The {lever} rule did not act on the small circuit')
    if baseline_receipt is not None:
        before = baseline_receipt.get('activation_counts', {})
        for lever in inherited - enabled:
            if before.get(lever, 0) <= 0:
                raise ValueError(f'The small circuit supplies no evidence for removing {lever}')
    return {'new_rules': sorted(enabled - inherited), 'removed_rules': sorted(inherited - enabled),
            'inherited_inactive': sorted(c for c in enabled & inherited if not counts.get(c, 0)),
            'scope': 'Only changed, active rules provide transfer evidence. Inherited inactive rules remain source context.'}


def native_tools(work, *, offline=False):
    path = Path(work).resolve() / 'native-build.json'
    environment = os.environ.copy()
    if offline:
        environment['LCO_OFFLINE'] = '1'
    stdout = run(['sh', ROOT / 'native/build.sh'], ROOT, path, env=environment)
    import json
    tools = json.loads(stdout)
    frozen = Path(work).resolve() / 'frozen-bin'
    frozen.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for key in ('small_emit', 'circuit_check'):
        if not Path(tools[key]).is_file():
            raise ValueError(f'The native build omitted {key}')
        destination = frozen / Path(tools[key]).name
        if destination.exists() and sha256(destination) != sha256(tools[key]):
            raise ValueError('The native executable changed after the run froze')
        if not destination.exists():
            shutil.copy2(tools[key], destination)
        tools[key] = str(destination)
        hashes[key] = sha256(destination)
    tools['binary_sha256'] = hashes
    return tools


def check(tools, ops, width, pairs, label, out, alpha=0.05):
    from .frontier.paired import validate_verdicts
    from .frontier.registry import binomial_upper

    out = Path(out).resolve()
    before = sha256(ops)
    expected_checker = tools.get('binary_sha256', {}).get('circuit_check', sha256(tools['circuit_check']))
    if sha256(tools['circuit_check']) != expected_checker:
        raise ValueError('The fixed checker changed before evaluation')
    process = out.with_name(out.stem + '-process.json')
    verdicts = out.with_suffix('.jsonl')
    if not out.exists():
        if verdicts.exists() or process.exists():
            raise ValueError('The interrupted check has incomplete evidence; preserve its consumed inputs')
        run([tools['circuit_check'], '--width', str(width), '--ops', str(ops),
             '--pairs', str(pairs), '--label', label, '--out', str(out),
             '--streams', '2', '--alpha', str(alpha)], out.parent,
            process, accepted_returncodes=(0, 1))
    audit = load(out)
    expected = {'pairs': pairs, 'streams': 2, 'field_width': width, 'label': label,
                'alpha': alpha, 'ops_sha256': before, 'checker_binary_sha256': expected_checker}
    if any(audit.get(key) != value for key, value in expected.items()):
        raise ValueError('The check receipt differs from its frozen protocol or artifacts')
    if audit.get('per_input') != str(verdicts):
        raise ValueError('The check receipt names a different verdict file')
    validate_verdicts(audit, verdicts, pairs=pairs, streams=2)
    upper = binomial_upper(audit['failed_inputs'], pairs, alpha)
    for key in ('upper_bound', 'binomial_upper'):
        value = audit.get(key)
        if type(value) not in (int, float) or not 0 <= value <= 1 or abs(value - upper) > 1e-11:
            raise ValueError('The check probability bound differs from its saved outcomes')
    if sha256(ops) != before:
        raise ValueError('The operation file changed during evaluation')
    if sha256(tools['circuit_check']) != expected_checker:
        raise ValueError('The fixed checker changed during evaluation')
    audit.update(checker_sha256=expected_checker, protocol_valid=True)
    save(out, audit)
    return audit


def small_stage(policy, tools, work, pairs, label, alpha, cancel_adjacent=False):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    ops = work / 'ops.bin'
    receipt_path = work / 'emission.json'
    frozen = {'policy': policy, 'pairs': pairs, 'label': label, 'alpha': alpha, 'cancel_adjacent': cancel_adjacent,
              'emitter_sha256': sha256(tools['small_emit']), 'checker_sha256': sha256(tools['circuit_check'])}
    freeze_path = work / 'freeze.json'
    if freeze_path.exists() and load(freeze_path) != frozen:
        raise ValueError('The small-stage cache belongs to a different frozen protocol')
    save(freeze_path, frozen)
    if receipt_path.exists():
        if not ops.is_file() or load(receipt_path).get('ops_sha256') != sha256(ops):
            raise ValueError('The cached small circuit changed')
    else:
        if ops.exists():
            ops.rename(work / ('interrupted-' + sha256(ops) + '.bin'))
        command = [tools['small_emit'], '--policy', policy, '--parent-policy', 'mfs', '--out', str(ops),
                   '--receipt', str(receipt_path)]
        if cancel_adjacent:
            command.append('--cancel-adjacent')
        run(command, work, work / 'emit-process.json')
    receipt = load(receipt_path)
    if receipt['ops_sha256'] != sha256(ops):
        raise ValueError('The small emitter receipt has the wrong operation hash')
    audit_path = work / 'audit.json'
    if audit_path.exists():
        audit = load(audit_path)
        if audit.get('ops_sha256') != sha256(ops) or audit.get('checker_sha256') != frozen['checker_sha256']:
            raise ValueError('The small audit cache has a different artifact or checker')
    else:
        audit = check(tools, ops, 16, pairs, label, audit_path, alpha)
    return {'emission': receipt, 'audit': audit, 'qualified': audit_qualified(audit)}


def normalized_cost(audit, emitted):
    q = audit.get('qubits', audit.get('qubit_count', emitted.get('Q')))
    t = audit.get('mean_toffoli', audit.get('toffoli_count', emitted.get('T')))
    if q is None or t is None:
        raise ValueError('The checker omitted Q or T')
    return int(q), float(t)


def evolutionary_order(menu, rows, seeds=None):
    """Prefer one-rule changes from retained parents, then seed untouched targets."""
    parents = pareto(rows)
    seen_targets = {r['candidate']['target'] for r in rows}
    def flags_for(candidate):
        flags = set() if candidate['policy'] == 'none' else set(candidate['policy'])
        if candidate.get('cancel_adjacent'):
            flags.add('c')
        return flags
    def rank(candidate):
        flags = flags_for(candidate)
        distances = [len(flags.symmetric_difference(flags_for(p['candidate']))) for p in parents
                     if p['candidate']['target'] == candidate['target']]
        if distances:
            return (0 if min(distances) == 1 else 2, min(distances), candidate['target'], candidate['policy'])
        seed = (seeds or {}).get(candidate['target'], 'mfs')
        seed_flags = set() if seed == 'none' else set(seed)
        return (1 if candidate['target'] not in seen_targets and candidate['policy'] == seed
                else 3, len(flags.symmetric_difference(seed_flags)), candidate['target'], candidate['policy'])
    menu.sort(key=rank)


def default_paths(family):
    backend = family.get('backend', family.get('id', 'froghop'))
    paths = [ROOT / 'grammars/base.json']
    if backend != 'source-conditional':
        paths.append(ROOT / f'grammars/families/{backend}.json')
    return paths + [Path(p) for p in family.get('grammar_files', [])]


def run_campaign(grammar, family, config, output, *, target_ids,
                 mode='search', trials=8, seed=0, small_pairs=4096,
                 large_pairs=16384, audit_pairs=DEFAULT_AUDIT_PAIRS, official=False, offline_build=False,
                 strategy='evolution', artifact_root=None):
    """Freeze a run contract, evaluate proposals, and retain its Q/T front."""
    from . import large
    from . import source as editable
    if type(audit_pairs) is not int or audit_pairs < 0:
        raise ValueError('Final audit pairs must be zero or positive')
    output = Path(output).resolve()
    tools = native_tools(output, offline=True) if offline_build else native_tools(output)
    artifact_root = Path(artifact_root or output / 'artifacts').resolve()
    contract = {'schema_version': 1, 'kind': mode, 'family': family,
                'grammar_sha256': grammar.sha256, 'model': config,
                'targets': target_ids, 'trials': trials, 'seed': seed,
                'small_pairs': small_pairs, 'large_pairs': large_pairs,
                'audit_pairs': audit_pairs, 'official': official, 'strategy': strategy,
                'artifact_root': str(artifact_root),
                'tools_sha256': {k: sha256(tools[k]) for k in ('small_emit', 'circuit_check')},
                'runner_sha256': sha256(__file__),
                'harness_source_sha256': digest({p.name: sha256(p)
                    for p in sorted(Path(__file__).parent.glob('*.py'))}),
                'target_catalog_sha256': sha256(ROOT / 'data/targets/catalog.json'),
                'epsilon': EPSILON, 'measurement_streams': 2,
                'historical_replay_is_rediscovery': False}
    path = output / 'contract.json'
    if path.exists() and load(path) != contract:
        raise ValueError('The output directory contains a different frozen contract')
    save(path, contract)
    save(output / 'grammar.json', grammar.to_dict())
    save(output / 'availability.json', grammar.availability(family))
    from .grammar import POLICIES
    target_rows = {r['id']: r for r in large.targets() if r['family'] == family['backend']}
    historical = {key: r.get('historical_policy') or 'none' for key, r in target_rows.items()}
    menu = []
    for target in target_ids:
        if mode == 'reproduce' and target not in historical:
            raise ValueError('An editable family has no frozen target to reproduce; use search')
        policies = [historical[target]] if mode == 'reproduce' else list(POLICIES)
        for policy in policies:
            for cancellation in ([False] if mode == 'reproduce' else [False, True]):
                try:
                    candidate = grammar.candidate_for_policy(policy, target, family, cancel_adjacent=cancellation)
                    menu.append(grammar.validate_candidate(candidate, family))
                except ValueError:
                    continue
    if not menu:
        raise ValueError('No composed rule has both required stage bindings for this family')
    random.Random(seed).shuffle(menu)
    if mode == 'reproduce':
        menu.sort(key=lambda c: target_ids.index(c['target']))
        trials = len(menu)
    rows = []
    small_cache = {}
    alpha = 0.05 / max(1, 2 * min(trials, len(menu)))
    for slot in range(min(trials, len(menu))):
        slot_dir = output / f'trials/{slot:03d}'
        completed = slot_dir / 'result.json'
        # Resume revalidates exact operations. Changed artifacts require a new output directory.
        if completed.exists():
            old = load(completed)
            index = next((i for i, c in enumerate(menu) if c == old['candidate']), None)
            if index is None:
                raise ValueError('A saved candidate differs from the frozen grammar')
            if old.get('ops_path') and sha256(old['ops_path']) != old['ops_sha256']:
                raise ValueError('A saved operation artifact changed')
            menu.pop(index)
            rows.append(old)
            continue
        if mode == 'search' and strategy == 'evolution':
            evolutionary_order(menu, rows, {t: target_rows.get(t, {}).get('parent_policy') or 'none' for t in target_ids})
        context = {'kind': mode, 'grammar_sha256': grammar.sha256,
                   'mechanisms': grammar.availability(family),
                   'results': [{k: r[k] for k in ('candidate', 'qualified', 'Q', 'T') if k in r} for r in rows],
                   'scope': 'Frozen replay validates known recipes.' if mode == 'reproduce'
                   else 'Promote active, qualified small strategies to full secp256k1.'}
        candidate, model_receipt = select(menu, config, slot_dir / 'model', context=context, call_index=slot)
        candidate = grammar.validate_candidate(candidate, family)
        menu.remove(candidate)
        print(f'Trial {slot + 1}/{trials}: {candidate["target"]}, policy {candidate["policy"]}', file=sys.stderr, flush=True)
        row = {'slot': slot, 'candidate': candidate, 'model': model_receipt,
               'qualified': False, 'grammar_sha256': grammar.sha256}
        save(slot_dir / 'candidate.json', candidate)
        try:
            save(output / 'progress.json', {'completed': len(rows), 'requested': trials,
                'active_slot': slot, 'candidate': candidate, 'stage': 'fast', 'updated_unix': time.time()})
            policy = candidate['policy']
            cancellation = candidate.get('cancel_adjacent', False)
            key = policy + ('-cancel' if cancellation else '')
            if key not in small_cache:
                small_cache[key] = small_stage(policy, tools, output / f'small/{key}', small_pairs,
                                               f'{digest(contract)}/small/{key}', 0.05, cancellation)
            row['small'] = small_cache[key]
            inherited = target_rows.get(candidate['target'], {}).get('parent_policy') or 'none'
            baseline = None
            before_flags = set() if inherited == 'none' else set(inherited)
            after_flags = set() if policy == 'none' else set(policy)
            if mode == 'search' and before_flags - after_flags:
                if inherited not in small_cache:
                    small_cache[inherited] = small_stage(inherited, tools, output / f'small/{inherited}', small_pairs,
                        f'{digest(contract)}/small/{inherited}', 0.05)
                baseline = small_cache[inherited]['emission']
            row['admission'] = admit_small(policy, row['small']['emission'], inherited_policy=inherited,
                                           baseline_receipt=baseline)
            if cancellation and row['small']['emission'].get('adjacent_cancel_pairs', 0) <= 0:
                raise ValueError('The cancellation rule did not act on the small circuit')
            if not row['small']['qualified']:
                raise ValueError('The small circuit did not meet epsilon on its screening inputs')
            save(output / 'progress.json', {'completed': len(rows), 'requested': trials,
                'active_slot': slot, 'candidate': candidate, 'stage': 'large', 'updated_unix': time.time()})
            construction = artifact_root / candidate['target']
            if family['backend'] == 'source-conditional':
                construction = artifact_root / 'source' / family['id'] / candidate['target']
            if policy != 'mfsur':
                construction = construction / policy
            if cancellation:
                construction = construction / 'cancel'
            construction = construction / 'large'
            if family['backend'] == 'source-conditional':
                emitted = editable.emit(family, candidate['target'], construction, policy=policy, offline=offline_build,
                                        cancel_adjacent=cancellation)
            else:
                emitted = large.emit(candidate['target'], construction, policy=policy, offline=offline_build,
                                     cancel_adjacent=cancellation)
            row['large'] = emitted
            ops = Path(emitted['ops_path'])
            row.update(ops_path=str(ops), ops_sha256=sha256(ops))
            if mode == 'reproduce' and emitted.get('historical_hash_matches') is not True:
                raise ValueError('Frozen replay did not reproduce the expected operation hash')
            if official:
                if family['backend'] == 'source-conditional':
                    raise ValueError('Use the fixed full checker for editable source families; official export needs a benchmark checkout')
                row['official'] = large.official_check(candidate['target'], construction, policy=policy,
                                                       offline=offline_build, cancel_adjacent=cancellation)
                if not row['official'].get('passed'):
                    raise ValueError('The unchanged official evaluator rejected this circuit')
                if mode == 'reproduce' and row['official'].get('historical_counts_match') is not True:
                    raise ValueError('The official Q/T counts differ from the frozen target')
            audit = check(tools, ops, 256, large_pairs,
                          f'{digest(contract)}/large/{slot}', slot_dir / 'large-audit.json', alpha)
            q, t = normalized_cost(audit, emitted)
            row.update(Q=q, T=t, QT=q*t, audit=audit, qualified=audit_qualified(audit))
        except (ValueError, RuntimeError, OSError) as error:
            row['error'] = str(error)
        save(completed, row)
        rows.append(row)
        save(output / 'progress.json', {'completed': len(rows), 'requested': trials,
                                        'qualified': sum(r['qualified'] for r in rows)})
    # Selection uses screening evidence, including after a failed final audit.
    screened = [{**r, 'qualified': audit_qualified(r['audit'])}
                for r in rows if 'audit' in r and 'Q' in r and 'T' in r]
    slots = {r['slot']: r for r in rows}
    front = [slots[r['slot']] for r in pareto(screened)]
    selection = [{'candidate': r['candidate'], 'ops_sha256': r['ops_sha256'],
                  'slot': r['slot'], 'pairs': audit_pairs,
                  'label': f'{digest(contract)}/fresh-final/{index}',
                  'alpha': 0.05 / max(1, len(front))}
                 for index, r in enumerate(front)]
    selected_path = output / 'selected.json'
    if selected_path.exists():
        if load(selected_path) != selection:
            raise ValueError('The frozen finalist selection changed; preserve the original audits')
    else:
        if (output / 'final').exists() and any((output / 'final').iterdir()):
            raise ValueError('Existing final audits have no frozen finalist selection')
        from .frontier.campaign import atomic_json
        atomic_json(selected_path, selection)
    if audit_pairs:
        for index, (row, selected) in enumerate(zip(front, selection)):
            audit = check(tools, row['ops_path'], 256, audit_pairs,
                          selected['label'], output / f'final/{index:03d}.json', selected['alpha'])
            row.update(final_audit=audit, qualified=audit_qualified(audit))
            save(output / f'trials/{row["slot"]:03d}/result.json', row)
        front = pareto(front)
    result = {'contract': contract, 'rows': rows, 'front': front,
              'qualification_scope': 'fresh-final-audit' if audit_pairs else 'screening-only',
              'completed': len(rows), 'qualified': sum(r['qualified'] and
                    (not audit_pairs or 'final_audit' in r) for r in rows),
              'menu_exhausted': not menu, 'model_calls': sum(r['model'].get('model_called', False) for r in rows),
              'reproduced_exact': sum(r.get('large', {}).get('historical_hash_matches') is True for r in rows),
              'claim': 'Known-target reconstruction' if mode == 'reproduce' else 'Search over admitted composed rules'}
    save(output / 'results.json', result)
    return result
