"""Render saved swarm evidence without importing or changing the search controller."""
from collections import Counter
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import tempfile


PRIOR_S = 1309376572
ORIGINAL_S = 10704574395
CONTROL_NAMES = {
    'S3_split_off': 'Compact ping-pong (S3)',
    'PC001_cap1332': 'Known fold split (PC001)',
    'prior_frontier_cap1316': 'Previous qualified local control: cap 1316',
    'original_cost_reference': 'Original cost reference',
}


def read(path, default=None, warnings=None):
    """Tolerate files that the active controller has not written yet."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        if warnings is not None:
            warnings.append(f'{path.name}: snapshot unavailable ({type(error).__name__})')
        return default


def qualification(audit, official, finalist, reservation, config):
    """Keep independent qualification and the linked official check separate."""
    expected_pairs = config.get('audit_pairs', 1000000)
    epsilon = config.get('epsilon', 1e-5)
    artifact = finalist.get('artifact', {})
    complete = bool(audit) and audit.get('pairs') == expected_pairs
    identity_ok = bool(artifact) and audit.get('artifact_sha256') == artifact.get('sha256')
    bound = audit.get('epsilon_upper95')
    independent = (complete and identity_ok and audit.get('qualified') is True
                   and isinstance(bound, (int, float)) and bound <= epsilon
                   and audit.get('streams') == 2)
    observed = official.get('observed', {})
    official_pass = (official.get('official_check_passed') is True
                     and official.get('returncode') == 0 and observed.get('shots') == 9024
                     and all(observed.get(k) == 0 for k in
                             ('classical_mismatches', 'phase_garbage_batches', 'ancilla_garbage_batches')))
    linked = bool(artifact) and reservation.get('ops_sha256') == artifact.get('sha256')
    qualified = bool(independent and official_pass and linked)
    return {'audit_complete': complete, 'independent_pass': bool(independent),
            'official_pass': bool(official_pass), 'official_artifact_link': linked,
            'qualified': qualified, 'qualified_S': audit.get('S') if qualified else None,
            'improves_previous_campaign': qualified and audit['S'] < PRIOR_S,
            'epsilon_target': epsilon, 'required_pairs': expected_pairs}


def snapshot(root):
    root = Path(root).resolve()
    native = root / 'full-point-add'
    warnings = []
    get = lambda name, default: read(native / name, default, warnings)
    config = get('config.json', {})
    budget = read(root / 'budget.json', {})
    initial_search = get('search-result.json', {})
    extension_started = get('extension-started.json', {})
    extension_result = get('extension-result.json', {})
    adoption = get('budget-amendments/three-hours/adoption.json',{})
    segment_boundary = extension_started.get('cumulative_slot_start',adoption.get('parent_trials')) if budget.get('amendment') else None
    archive = get('archive.json', {})
    trials = get('trials.json', [])
    trace = get('trace.json', [])
    preflight = get('preflight.json', [])
    finalist, audit, official = (get(name, {}) for name in
                               ('finalist.json', 'audit-result.json', 'official-result.json'))
    selection_protocol = get('selection-protocol-amendment/protocol.json', {})
    stage1_payload = get('selection-protocol-amendment/stage1-results.json',
                         get('selection-protocol-amendment/stage1-progress.json', {}))
    confirmation_payload = get('selection-protocol-amendment/confirmation-results.json',
                               get('selection-protocol-amendment/confirmation-progress.json', {}))
    stage1_results = list(stage1_payload.get('results', {}).values())
    confirmation_results = list(confirmation_payload.get('results', {}).values())
    confirmation_plan = get('selection-protocol-amendment/confirmation-candidates.json', {})
    no_finalist = get('selection-protocol-amendment/no-finalist.json', {})
    stage1_by_id = {row['id']: row for row in stage1_results}
    confirmed_by_id = {row['id']: row for row in confirmation_results}
    selection_rows = []
    for item in selection_protocol.get('shortlist', []):
        cid = item['id']
        first, second = stage1_by_id.get(cid, {}), confirmed_by_id.get(cid, {})
        name = ('Compact ping-pong (S3)' if cid == selection_protocol.get('controls', {}).get('S3') else
                'Prior local control' if cid == selection_protocol.get('controls', {}).get('prior_frontier') else cid[:12])
        confirmation_status = (f'{second["failed_inputs"]:,} / {second["pairs"]:,}' if second else
                               'Pending' if cid in confirmation_plan.get('ids',[]) else
                               'Not selected' if confirmation_plan else 'Not frozen yet')
        selection_rows.append({'id': cid, 'name': name, 'window': item.get('multiplication_window'),
                               'stage1_S': first.get('S'), 'stage1_failures': first.get('failed_inputs'),
                               'confirmation_S': second.get('S'), 'confirmation': confirmation_status,
                               'confirmation_failures': second.get('failed_inputs'),
                               'confirmed': bool(second), 'stage1_pairs': first.get('pairs')})
    selection_rows.sort(key=lambda row: (row['stage1_S'] is None,row['stage1_S'] or 0))
    unconfirmed = [row for row in selection_rows if row['stage1_failures'] == 0 and not row['confirmed']]
    calibration_root = native / 'diagnostics/rejected-candidate-audit'
    calibration = {'preregistration': read(root/'diagnostics/rejected-candidate-audit/PREREGISTRATION.json',{}),
                   'contract': read(calibration_root/'contract.json',read(calibration_root/'audit-contract.json',{})),
                   'progress': read(calibration_root/'progress.json',read(calibration_root/'audit-progress.json',{})),
                   'result': read(calibration_root/'result.json',read(calibration_root/'audit-result.json',{}))}
    reservation = get('official-tool/attempt-reservation.json', {})
    controls = {}
    for row in preflight:
        if row['name'] in CONTROL_NAMES:
            controls[row['name']] = {'name': CONTROL_NAMES[row['name']], 'id': row['artifact']['id'],
                                     'source': 'preflight', **row['result']}
    for row in archive.values():
        name = row.get('action', {}).get('name')
        if name in CONTROL_NAMES:
            controls[name] = {'name': CONTROL_NAMES[name], 'source': 'search', **row['screen']}
    curve, best512 = [], None
    for trial in trials:
        entry = archive.get(trial.get('id'), {})
        result = entry.get('development512', {})
        if result.get('pairs', 0) >= config.get('promising_pairs', 512) and result.get('failed_inputs') == 0:
            best512 = min(best512, result['S']) if best512 is not None else result['S']
        curve.append({'slot': trial['slot'], 'best_passing512_S': best512})
    passing_entries = [row for row in archive.values()
                       if row.get('development512', {}).get('failed_inputs') == 0]
    chosen = min(passing_entries, key=lambda row: row['development512']['S']) if passing_entries else {}
    displayed_scope = 'current passing-512 development endpoint'
    if finalist.get('artifact'):
        artifact = finalist['artifact']
        chosen = archive.get(artifact['id'], {'id': artifact['id'], 'recipe': artifact.get('recipe', {})})
        displayed_scope = 'frozen primary finalist'
    elif calibration['contract'].get('artifact'):
        artifact = calibration['contract']['artifact']
        chosen = archive.get(artifact['id'], {'id':artifact['id'],'recipe':artifact.get('recipe',{})})
        displayed_scope = 'separate C02 rejected candidate'
    chosen_settings = chosen.get('recipe', {}).get('settings', {})
    prior_settings = controls.get('prior_frontier_cap1316', {}).get('recipe', {}).get('settings', {})
    scalar_names = {'PP_WALK_MAX_QUBITS': 'Walk cap', 'CP_FOLD_CAP': 'Fold cap',
                    'PP_REPLAY_FOLD_WINDOW': 'Division fold window',
                    'PP_REPLAY_FOLD_WINDOW_MUL': 'Multiplication fold window',
                    'PP_REPLAY_CHUNK_COMPARE': 'Chunk comparison width',
                    'PP_REPLAY_FLAG_COMPARE': 'Flag comparison width',
                    'FOLD_GUARD': 'Constant-fold guard', 'ERASE_COMPARE': 'Erasure comparison width'}
    scalar_comparison = [{'setting': label, 'prior': prior_settings.get(key),
                          'selected': chosen_settings.get(key)} for key, label in scalar_names.items()]
    dev = read(root / 'development/probe/summary.json', [], warnings)
    structural = read(root / 'development/matched-structural/summary.json', [], warnings)
    greedy = read(root / 'development/matched-greedy/summary.json', [], warnings)
    cells = []
    for row in dev:
        plan = row['plan']
        cells.append({'bits': plan['bits'], 'shape': plan['shape'], 'control_S': row['control_S'],
                      'best_S': row['best_S'], 'Q': row['best_Q'], 'T': row['best_T'],
                      'gain_percent': row['gain_over_best_global_percent'],
                      'maximum_depth': row['maximum_depth'], 'evaluations': row['evaluations']})
    ledger = []
    ledger_path = root / 'ledger/journal.jsonl'
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines():
            try:
                ledger.append(json.loads(line))
            except json.JSONDecodeError:
                warnings.append('An incomplete ledger line was skipped in this snapshot.')
    counts = get('rule-counts.json', {})
    families = sorted({key.split('/')[0] for key in counts})
    rules = [{'rule': name, **{key: counts.get(name + '/' + key, 0) for key in
                              ('opportunity', 'attempted', 'applied', 'inactive', 'duplicate',
                               'same_gate_stream', 'rejected', 'error')}} for name in families]
    scoped = Counter()
    construction_rejections = []
    other_errors = []
    for trial in trials:
        entry = archive.get(trial.get('id'), {})
        if trial.get('action', {}).get('rule') in ('fold_site', 'inlet_sites', 'inline_sites'):
            scoped['requested_records'] += 1
            if trial.get('active_scoped_edit') is True:
                scoped['profile_match_records'] += 1
            if trial.get('active_scoped_edit') is False:
                scoped['inactive_records'] += 1
            parents = trial.get('parents', [])
            if entry and trial.get('status') != 'duplicate' and parents and parents[0] in archive:
                scoped['emitted_records'] += 1
                changed = entry['artifact']['sha256'] != archive[parents[0]]['artifact']['sha256']
                scoped['changed_primary_parent_streams'] += changed
                scoped['unchanged_primary_parent_streams'] += not changed
        if trial.get('status') == 'instrument_error':
            stderr = native / 'circuits' / trial.get('id', '') / 'stderr.txt'
            text = stderr.read_text() if stderr.exists() else ''
            if 'panicked at' in text:
                construction_rejections.append(trial['slot'])
            else:
                other_errors.append(trial['slot'])
    goal_snapshots = []
    for path in (root/'verification').glob('goal-meter-*.json'):
        record = read(path,{})
        goal_record = record.get('measurement',{}).get('goal',record.get('goal',{}))
        if goal_record:
            goal_snapshots.append((goal_record.get('updatedAt',0),path,record,goal_record))
    _,goal_meter_file,goal_meter,goal = max(goal_snapshots,key=lambda row:row[0]) if goal_snapshots else (0,None,{}, {})
    matched = read(root / 'controls/results.json', [], warnings)
    matched_scope = 'primary finalist'
    matched_path = 'controls/results.json'
    rejected_matched = read(root / 'diagnostics/rejected-candidate-controls/results.json', [], warnings)
    if not matched and (rejected_matched or (root/'diagnostics/rejected-candidate-controls/plan.json').exists()):
        matched = rejected_matched
        matched_scope = 'separate rejected C02 candidate'
        matched_path = 'diagnostics/rejected-candidate-controls/results.json'
    matched_rows = matched.get('results', matched.get('rows', [])) if isinstance(matched, dict) else matched
    matched_labels = {'selected': 'Frozen selected recipe', 'without_stream': 'Streamed repair disabled',
                      'without_inlet': 'Early inlet retirement disabled',
                      'without_inline': 'Routed selector reuse disabled',
                      'without_all': 'All new exact rules disabled',
                      'original_error_windows': 'Inherited error windows restored',
                      'prior_frontier_recipe': 'Previous qualified local recipe'}
    matched_rows = [{'variant': row.get('name', row.get('variant', row.get('label', 'Control'))),
                     **row.get('result', row.get('evaluation', row))} for row in matched_rows]
    for row in matched_rows:
        row['name'] = matched_labels.get(row['variant'], row['variant'])
    q = qualification(audit, official, finalist, reservation, config)
    if q['qualified']:
        stage = 'qualified improvement' if q['improves_previous_campaign'] else 'qualified; no lower S than the prior campaign'
    elif q['audit_complete'] and not q['independent_pass']:
        stage = 'independent audit did not qualify'
    elif q['independent_pass'] and official:
        stage = 'independent audit passed; official confirmation failed or incomplete'
    elif q['independent_pass']:
        stage = 'independent audit passed; official confirmation pending'
    elif finalist:
        stage = 'finalist frozen; independent audit in progress'
    elif no_finalist:
        stage = 'primary selection stopped; no confirmed finalist'
    elif confirmation_results:
        stage = 'development confirmation in progress'
    elif stage1_results:
        stage = 'development stage one complete; confirmation pending'
    elif selection_protocol:
        stage = 'prospective diverse selection in progress'
    elif get('selection-stop.json', {}):
        stage = 'selection stopped without a passing finalist'
    elif get('selection-results.json', []):
        stage = 'selection in progress'
    elif extension_started and not extension_result:
        stage = 'extended development search in progress'
    elif budget.get('amendment') and not extension_result:
        stage = 'search extension authorised; launch or results pending'
    elif trials:
        stage = 'development search in progress' if not get('search-result.json', {}) else 'development search complete'
    else:
        stage = 'preflight; search results pending'
    timing = Counter()
    processes = []
    for pattern, category in [('circuits/*/process.json', 'circuit emission'),
                              ('emission-errors/*/process.json', 'failed emission'),
                              ('evaluations/*/process.json', 'evaluation'),
                              ('official-tool/process.json', 'official check'),
                              ('checker-controls/process.json', 'checker controls'),
                              ('official-tool/build.json', 'official tool build')]:
        for path in native.glob(pattern):
            proc = read(path, {})
            wall = proc.get('wall_seconds', 0)
            if isinstance(wall, (int, float)):
                stage_name = category
                if category == 'evaluation':
                    name = path.parent.name
                    if name.startswith('selection-diverse-stage1-'):
                        stage_name = '32,768-pair diverse selection'
                    elif name.startswith('selection-diverse-confirmation-'):
                        stage_name = '65,536-pair confirmation'
                    else:
                        prefix = name.split('-')[0]
                        stage_name = {'screen': '64-pair screen', 'development512': '512-pair development',
                                      'selection32768': '32,768-pair selection',
                                      'independent': 'fresh final audit'}.get(prefix, category)
                timing[stage_name] += wall
                processes.append({'file': str(path.relative_to(root)), 'category': stage_name,
                                  'wall_seconds': wall, 'returncode': proc.get('returncode')})
    verifications=[]
    for path in (root/'circuit-archive/replay-sessions').glob('*/verify-receipt.json'):
        record=read(path,{})
        if record.get('verified_utc'):
            verifications.append((record['verified_utc'],path,record))
    _,verification_file,verification_record=max(verifications,key=lambda row:row[0]) if verifications else ('',None,{})
    return {'root': str(root), 'rendered_utc': datetime.now(timezone.utc).isoformat(),
            'stage': stage, 'config': config, 'budget': budget,
            'qualification': q, 'prior_campaign_S': PRIOR_S, 'controls': controls,
            'archive': archive, 'trials': trials, 'trace': trace, 'passing512_curve': curve,
            'displayed_recipe_id': chosen.get('id'), 'scalar_comparison': scalar_comparison,
            'displayed_recipe_scope': displayed_scope,
            'search': initial_search, 'extension_started': extension_started,
            'extension_result': extension_result, 'segment_boundary': segment_boundary,
            'native_search_elapsed_seconds': trace[-1].get('elapsed_seconds') if trace else initial_search.get('wall_seconds'),
            'selection': get('selection-results.json', []),
            'selection_protocol': selection_protocol, 'stage1_results': stage1_results,
            'confirmation_results': confirmation_results, 'confirmation_plan': confirmation_plan,
            'selection_rows': selection_rows, 'unconfirmed_stage1_passes': unconfirmed,
            'primary_no_finalist': no_finalist, 'exploratory_calibration': calibration,
            'selection_funnel': {'planned_stage1': selection_protocol.get('actual_candidates'),
                                 'completed_stage1': len(stage1_results),
                                 'passing_stage1': sum(row['failed_inputs']==0 for row in stage1_results),
                                 'planned_confirmation': len(confirmation_plan.get('ids',[])),
                                 'completed_confirmation': len(confirmation_results),
                                 'passing_confirmation': sum(row['failed_inputs']==0 for row in confirmation_results),
                                 'primary_finalists': int(bool(finalist)), 'primary_audits': int(bool(audit))},
            'finalist': finalist, 'audit': audit, 'official': official,
            'audit_progress': get('audit-progress.json', {}), 'rules': rules,
            'scoped_activity': dict(scoped), 'construction_rejection_slots': construction_rejections,
            'other_instrument_error_slots': other_errors,
            'proposal_status_counts': dict(Counter(row['status'] for row in trials)),
            'matched_controls': matched_rows, 'goal_meter': goal_meter,
            'goal_meter_file': str(goal_meter_file.relative_to(root)) if goal_meter_file else None,
            'matched_controls_scope': matched_scope,
            'matched_controls_path': matched_path,
            'repair_prediction': read(root/'diagnostics/rejected-candidate-controls/repair-prediction.json',{}),
            'peak_diagnostic': read(root/'diagnostics/peak-profile/result.json',{}),
            'peak_diagnostic_plan': read(root/'diagnostics/peak-profile/plan.json',{}),
            'circuit_archive': {'index':read(root/'circuit-archive/index-receipt.json',{}),
                                'reconstructions':read(root/'circuit-archive/self-check-receipt.json',{}),
                                'verification':verification_record,
                                'verification_file':str(verification_file.relative_to(root)) if verification_file else None,
                                'retained_raw':read(root/'circuit-archive/retained-raw-circuits.json',{})},
            'slots': len(trials), 'archived_recipes': len(archive),
            'emitted': sum(value for key,value in counts.items() if key.endswith('/applied')),
            'maximum_depth': max((r.get('depth', 0) for r in archive.values()), default=0),
            'distinct_gate_streams': len({r['artifact']['sha256'] for r in archive.values()}),
            'cells': cells, 'structural': structural, 'greedy': greedy,
            'development_aggregate': read(root / 'development/aggregate.json', {}),
            'confirmation': read(root / 'development/confirmation/results.json', {}),
            'ledger': ledger, 'process_timing_seconds': dict(timing), 'process_receipts': processes,
            'warnings': warnings,
            'model_usage': {'loop_cli_or_api_calls': 0, 'goal_service_tokens': goal.get('tokensUsed'),
                            'goal_service_elapsed_seconds': goal.get('timeUsedSeconds'),
                            'goal_service_updated_at': goal.get('updatedAt'),
                            'provider_input_output_cache_split': None, 'per_agent_split': None,
                            'host_provider_spend': None,
                            'scope': 'Zero measured-loop CLI/API calls. Goal-service totals come from the saved checkpoint. Provider and per-agent splits and billed spend are unavailable.'}}


def plots(root, data):
    os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'grammar-swarm-matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    figures = root / 'figures'
    figures.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'figure.facecolor': '#faf9f6', 'axes.facecolor': '#faf9f6'})
    def save(fig, name):
        fig.tight_layout()
        for extension in ('png', 'svg', 'pdf'):
            fig.savefig(figures / f'{name}.{extension}', dpi=180)
        plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 5.1))
    screen = [r for r in data['trace'] if r.get('best_S') is not None]
    passing = [r for r in data['passing512_curve'] if r['best_passing512_S'] is not None]
    if screen:
        ax.step([r['slot'] + 1 for r in screen], [r['best_S'] for r in screen], where='post',
                color='#a96b24', linestyle='--', linewidth=2, label='64-pair screen best')
    if passing:
        ax.step([r['slot'] + 1 for r in passing], [r['best_passing512_S'] for r in passing],
                where='post', color='#12635f', linewidth=2.7, label='512-pair passing best')
    if data['segment_boundary'] is not None:
        ax.axvline(data['segment_boundary'], color='#687580', linestyle='-.', linewidth=1,
                   label=f'Authorised extension after {data["segment_boundary"]} slots')
    failed512 = []
    for trial in data['trials']:
        entry = data['archive'].get(trial.get('id'), {})
        if entry.get('development512', {}).get('failed_inputs', 0):
            failed512.append((trial['slot'] + 1, entry['screen']['S']))
    if failed512:
        ax.scatter(*zip(*failed512), marker='x', s=55, color='#ad3f35', zorder=6,
                   label='Screen endpoint failed 512-pair check')
    selection = data['finalist'].get('selection', {})
    end = max([r['slot'] + 1 for r in data['trials']] or [1])
    if selection:
        ax.scatter([end + 1], [selection['S']], marker='D', s=65, color='#333d4a',
                   label=f'Frozen {selection["pairs"]:,}-pair confirmation', zorder=7)
    if data['audit']:
        audit = data['audit']
        passed = data['qualification']['qualified']
        independent = data['qualification']['independent_pass']
        ax.scatter([end + 2], [audit['S']], marker='*' if passed else 'D' if independent else 'X', s=160,
                   color='#12635f' if passed else '#a96b24' if independent else '#ad3f35', zorder=8,
                   label='Qualified final audit + official pass' if passed else
                   'Independent audit passed; official confirmation pending' if independent else
                   'Final independent audit did not qualify')
    ax.axhline(PRIOR_S, color='#7d6e98', linestyle=':', linewidth=1.4,
               label='Previous qualified local control (separate campaign)')
    values = [r['best_S'] for r in screen] + [r['best_passing512_S'] for r in passing] + [PRIOR_S]
    for name in ('S3_split_off', 'PC001_cap1332'):
        if name in data['controls']:
            row = data['controls'][name]
            values.append(row['S'])
            ax.axhline(row['S'], color='#b4bdc2', linewidth=.8)
            ax.text(.99, row['S'], row['name'], transform=ax.get_yaxis_transform(),
                    ha='right', va='bottom', color='#687580', fontsize=8)
    if data['audit']:
        values.append(data['audit']['S'])
    pad = max((max(values) - min(values)) * .12, PRIOR_S * .0008)
    ax.set_ylim(min(values) - pad, max(values) + pad)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value / 1e6:,.1f}'))
    ax.set(xlabel='Proposal slot, including duplicates and errors', ylabel='Full point-addition S (millions)',
           title='Complete point-addition search · zoom around the prior local control · lower is better')
    if not screen:
        ax.text(.5, .55, 'Search trace pending\nPreflight controls are measured below.',
                ha='center', transform=ax.transAxes)
    ax.legend(frameon=False, fontsize=8, loc='upper right')
    ax.grid(axis='y', alpha=.15)
    save(fig, 'native-search')

    if any(row['stage1_S'] is not None for row in data['selection_rows']):
        fig, ax = plt.subplots(figsize=(12, 5.5))
        rows = data['selection_rows']
        for passed, color, marker, label in [(True,'#12635f','o','32,768 pairs: zero failures'),
                                              (False,'#ad3f35','x','32,768 pairs: observed failure')]:
            points = [(i,row['stage1_S']/1e6) for i,row in enumerate(rows)
                      if row['stage1_S'] is not None and (row['stage1_failures']==0)==passed]
            if points:
                ax.scatter(*zip(*points),color=color,marker=marker,s=50,label=label)
        for passed, color in [(True,'#333d4a'),(False,'#ad3f35')]:
            points = [(i,row['confirmation_S']/1e6) for i,row in enumerate(rows)
                      if row['confirmed'] and (row['confirmation_failures']==0)==passed]
            if points:
                ax.scatter(*zip(*points),facecolors='none',edgecolors=color,marker='s',s=150,linewidths=1.5,
                           label='65,536 pairs: zero failures' if passed else '65,536 pairs: observed failure',zorder=6)
        labels = []
        for i,row in enumerate(rows):
            prefix = 'S3' if row['name'].startswith('Compact') else 'Prior' if row['name'].startswith('Prior') else f'Case {i+1}'
            labels.append(f'{prefix}\nw{row["window"]}')
            if row['stage1_failures']:
                ax.annotate(str(row['stage1_failures'])+' fail',(i,row['stage1_S']/1e6),
                            xytext=(0,8),textcoords='offset points',ha='center',fontsize=8,color='#ad3f35')
        ax.set_xticks(range(len(rows)),labels,fontsize=8)
        ax.set(xlabel='Frozen stage-one streams, ordered by stage-one S',ylabel='Measured S (millions)',
               title='Development selection · larger packets reject apparent low-cost endpoints')
        ax.axhline(PRIOR_S/1e6,color='#7d6e98',linestyle=':',linewidth=1,
                   label='Previous campaign local control (separate evidence)')
        ax.legend(frameon=False,fontsize=8,loc='lower right')
        ax.grid(axis='y',alpha=.15)
        save(fig,'selection-stages')

    fig, ax = plt.subplots(figsize=(11, 3.5))
    rows = list(data['controls'].values())
    if rows:
        ax.barh(range(len(rows)), [r['S'] / 1e9 for r in rows], color=
                ['#a4aab0' if r['name'] == 'Original cost reference' else '#12635f' for r in rows])
        ax.set_yticks(range(len(rows)), [r['name'] for r in rows])
        ax.invert_yaxis()
        for i, row in enumerate(rows):
            ax.text(row['S'] / 1e9 + .04, i, f'{row["S"] / 1e9:.4f}', va='center', fontsize=9)
        ax.set_xlim(0, max(r['S'] / 1e9 for r in rows) * 1.13)
    ax.set(xlabel='S (billions), 64-pair preflight/screen measurements',
           title='Cost context · the original has known historical phase faults')
    save(fig, 'cost-context')

    fig, axes = plt.subplots(2, 2, figsize=(11, 6.7), sharex=True)
    for ax, (bits, shape) in zip(axes.flat, [(16, 'payload'), (16, 'stationary_window'),
                                         (256, 'payload'), (256, 'stationary_window')]):
        cell = next((r for r in data['cells'] if r['bits'] == bits and r['shape'] == shape), None)
        if cell:
            for policy, label, color, style in [('matched-structural', 'Structural archive', '#12635f', '-'),
                                               ('matched-greedy', 'Cost elite', '#b76827', '--')]:
                curve = read(root / 'development' / policy / f'{bits}-{shape}' / 'curve.json', [])
                curve = [r for r in curve if r['evaluation'] >= 4]
                if curve:
                    ax.step([r['evaluation'] for r in curve],
                            [100 * r['best_S'] / cell['control_S'] for r in curve],
                            where='post', label=label, color=color, linestyle=style, linewidth=2)
        ax.axhline(100, linewidth=.7, color='#939a9d')
        ax.set(title=f'{bits} bits · {"ordinary payload" if shape == "payload" else "stationary window"}',
               xlabel='Emitted candidate evaluations', ylabel='Best S / best global S (%)', ylim=(88, 102))
        ax.grid(axis='y', alpha=.15)
    axes.flat[0].legend(frameon=False, fontsize=8)
    fig.suptitle('Modular-cell search · measured curves after the four global controls', fontsize=13)
    save(fig, 'cell-search')


def esc(value):
    return html.escape(str(value), quote=True)


def number(value):
    if value is None:
        return 'Pending'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, float):
        if value and abs(value) < .001:
            return f'{value:.7g}'
        return f'{value:,.4f}'.rstrip('0').rstrip('.')
    return f'{value:,}' if isinstance(value, int) else str(value)


def table(rows, fields):
    if not rows:
        return '<p class="muted">No completed receipt in this snapshot.</p>'
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(label)}</th>' for _, label in fields) + \
        '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{esc(number(row.get(key)))}</td>'
        for key, _ in fields) + '</tr>' for row in rows) + '</tbody></table></div>'


def md_table(rows, fields):
    if not rows:
        return 'No completed receipt in this snapshot.'
    lines = ['| ' + ' | '.join(label for _,label in fields) + ' |',
             '| ' + ' | '.join('---' for _ in fields) + ' |']
    lines += ['| ' + ' | '.join(number(row.get(key)) for key,_ in fields) + ' |' for row in rows]
    return '\n'.join(lines)


def source_href(reference):
    path, separator, line = reference.rpartition(':')
    if separator and line.isdigit():
        return (path if path.startswith('/') else '../../' + path) + '#L' + line
    return reference if reference.startswith('/') else '../../' + reference


def pipeline_svg():
    labels = [('State the invariant', 'Read sources and controls'), ('Propose a guarded edit', 'Keep larger and neutral parents'),
              ('Emit and screen', '64 pairs → 512 pairs'), ('Confirm before freezing', '14×32,768 → ≤3×65,536'),
              ('Audit a frozen finalist', '1M fresh pairs + official check')]
    pieces = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 130" role="img" aria-label="Evidence pipeline">',
              '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="#12635f"/></marker></defs>']
    for i, (title, detail) in enumerate(labels):
        x = 4 + i * 225
        if i:
            pieces.append(f'<path d="M{x-24},54 H{x-5}" stroke="#12635f" marker-end="url(#arrow)"/>')
        pieces.append(f'<rect x="{x}" y="12" width="210" height="88" rx="8" fill="#fff" stroke="#c5d5ce"/>')
        pieces.append(f'<text x="{x+12}" y="42" font-size="13" font-weight="650">{esc(title)}</text>')
        pieces.append(f'<text x="{x+12}" y="66" font-size="10" fill="#59676b">{esc(detail)}</text>')
    return ''.join(pieces) + '</svg>'


def selection_funnel_svg(data):
    values = data['selection_funnel']
    labels = [('Stage-one circuits',values['completed_stage1']),
              ('Stage-one zero failures',values['passing_stage1']),
              ('Confirmation circuits',values['completed_confirmation']),
              ('Confirmation zero failures',values['passing_confirmation']),
              ('Primary finalists',values['primary_finalists']),
              ('Primary final audits',values['primary_audits'])]
    pieces = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 125" role="img" aria-label="Observed primary selection counts">']
    for i,(label,count) in enumerate(labels):
        x=4+i*186
        pieces.append(f'<rect x="{x}" y="10" width="176" height="95" rx="8" fill="#fff" stroke="#c5d5ce"/>')
        pieces.append(f'<text x="{x+12}" y="40" font-size="11">{esc(label)}</text>')
        pieces.append(f'<text x="{x+12}" y="78" font-size="29" fill="#12635f">{count}</text>')
    return ''.join(pieces)+'</svg>'


def ledger_svg(ledger):
    roles = ['coordinator', 'sources', 'native-streamed-rule', 'development']
    role = lambda agent: 'sources' if agent == 'native-review' else agent
    counts = Counter(role(r['agent']) for r in ledger)
    roles += sorted(set(counts) - set(roles))
    by_id = {r['id']: r for r in ledger}
    edges = Counter()
    for row in ledger:
        parent = by_id.get(row.get('reply_to'))
        if parent and role(parent['agent']) != role(row['agent']):
            edges[(role(parent['agent']), role(row['agent']))] += 1
    columns = 2 if len(roles) <= 4 else 3
    width = 340 if columns == 2 else 300
    positions = {name: (30 + (i % columns) * (530 if columns == 2 else 330),
                        25 + (i // columns) * 155) for i,name in enumerate(roles)}
    height = ((len(roles) + columns - 1) // columns) * 155 + 25
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {height}" role="img" aria-label="Actual ledger reply links between swarm roles">']
    for (a, b), count in edges.items():
        if a in positions and b in positions:
            x1, y1 = positions[a]; x2, y2 = positions[b]
            pieces.append(f'<path d="M{x1+width//2},{y1+40} L{x2+width//2},{y2+40}" stroke="#9fb6b0" stroke-width="{1+min(count,8)/3}"/>')
    for name in roles:
        x, y = positions[name]
        latest = next((r for r in reversed(ledger) if role(r['agent']) == name), None)
        target = '#ledger-' + latest['id'] if latest else '#discussion'
        pieces.append(f'<a href="{target}"><rect x="{x}" y="{y}" width="{width}" height="80" rx="8" fill="#fff" stroke="#8ea9a0"/>')
        pieces.append(f'<text x="{x+18}" y="{y+32}" font-size="16">{esc(name)}</text><text x="{x+18}" y="{y+56}" font-size="12" fill="#59676b">{counts[name]} recorded messages</text></a>')
    pieces.append(f'<text x="500" y="{height-12}" text-anchor="middle" font-size="12" fill="#59676b">Lines represent recorded replies between roles. Boxes link to the latest message.</text>')
    return ''.join(pieces) + '</svg>'


def render(root):
    root = Path(root).resolve()
    data = snapshot(root)
    plots(root, data)
    q, audit, progress = data['qualification'], data['audit'], data['audit_progress']
    top_S = q['qualified_S']
    passing = [r['best_passing512_S'] for r in data['passing512_curve'] if r['best_passing512_S'] is not None]
    best512 = min(passing) if passing else None
    best_screen = min((r['best_S'] for r in data['trace'] if r.get('best_S') is not None), default=None)
    data['best_screen_S'] = best_screen
    data['best_passing512_S'] = best512
    data['best_independently_qualified_S'] = top_S
    data['pipeline_svg'] = 'figures/pipeline.svg'
    (root / 'figures/pipeline.svg').write_text(pipeline_svg())
    (root / 'figures/ledger.svg').write_text(ledger_svg(data['ledger']))
    (root / 'figures/selection-funnel.svg').write_text(selection_funnel_svg(data))
    cell_fields = [('bits', 'Bits'), ('shape', 'Cell'), ('control_S', 'Best global S'),
                   ('best_S', 'Best cell S'), ('gain_percent', 'Reduction (%)')]
    control_fields = [('name', 'Control'), ('Q', 'Q'), ('T', 'Mean T'), ('S', 'S'),
                      ('pairs', 'Inputs'), ('failed_inputs', 'Failed inputs')]
    rule_fields = [('rule', 'Rule family'), ('opportunity', 'Opportunities'), ('attempted', 'Attempts'),
                   ('applied', 'Emitted'), ('inactive', 'Inactive scoped edits'), ('duplicate', 'Duplicates'),
                   ('rejected', 'Observed failures'), ('same_gate_stream', 'Same gate stream')]
    stage_rows = [{'stage': 'Independent final audit', 'pairs': audit.get('pairs', progress.get('completed_pairs')),
                   'failed': audit.get('failed_inputs', sum(r.get('failed_inputs',0) for r in progress.get('chunks',[]))),
                   'bound': audit.get('epsilon_upper95'), 'passed': q['independent_pass']},
                  {'stage': 'Pinned official check', 'pairs': data['official'].get('observed',{}).get('shots'),
                   'failed': None, 'bound': None, 'passed': q['official_pass']}]
    primary_stopped = bool(data['primary_no_finalist'])
    qualified_label = 'None' if primary_stopped and top_S is None else number(top_S)
    selection_fields = [('name','Frozen stream'),('window','Multiplication window'),
                         ('stage1_S','32,768-pair S'),('stage1_failures','Stage-one failed inputs'),
                         ('confirmation','Confirmation failed / inputs')]
    funnel = data['selection_funnel']
    selection_text = (f'The prospective selector evaluated {funnel["completed_stage1"]} distinct streams on 32,768 pairs. '
                      f'{funnel["passing_stage1"]} had zero failures. It tested {funnel["completed_confirmation"]} streams '
                      f'in confirmation on 65,536 new pairs; {funnel["passing_confirmation"]} had zero failures.')
    limitation = ('The rule confirms the two cheapest stage-one passes plus the prior reference. '
                  'Other stage-one passes can remain unconfirmed. The w62 and w63 candidates passed stage one '
                  'and received no confirmation slots. This run does not establish whether they meet the final failure target.')
    calibration = data['exploratory_calibration']
    calibration_result = calibration['result'].get('result',calibration['result'])
    calibration_rows = [calibration_result] if calibration_result.get('pairs') is not None else []
    calibration_status = (('Complete; exploratory epsilon criterion met' if calibration_result.get('exploratory_meets_epsilon') else
                           'Complete; exploratory epsilon criterion not met') if calibration_rows else
                          'Frozen exploratory contract; result pending' if calibration['contract'] else
                          'Preregistered; audit contract pending' if calibration['preregistration'] else
                          'No exploratory contract recorded')
    calibration_progress = calibration['progress'].get('completed_pairs',calibration_result.get('pairs',0))
    calibration_failed = sum(row.get('failed_inputs',0) for row in calibration['progress'].get('chunks',[]))
    peak = data['peak_diagnostic']
    peak_rows = []
    for row in peak.get('repeated_global_peak_allocations',[]):
        peak_rows.append({'phase':row['phase'],'location':row['file']+':'+str(row['line']),
                          'count':row['count'],'active':row['active']})
    ripple_hits=sum(row['count'] for row in peak_rows if row['location'].endswith('modular.rs:75'))
    fold_hits=sum(row['count'] for row in peak_rows if row['location'].endswith('pingpong.rs:2030'))
    first_ripple=sum(row['count'] for row in peak.get('first_peak_owners',[]) if row['file'].endswith('modular.rs'))
    first_boundaries=sum(row['count'] for row in peak.get('first_peak_owners',[]) if row['file'].endswith('pingpong.rs') and row['line']==1802)
    peak_width=data['peak_diagnostic_plan'].get('artifact',{}).get('recipe',{}).get('settings',{}).get('PP_REPLAY_FOLD_WINDOW_MUL')
    peak_text = (f'The accepted peak profile uses frozen development recipe {peak.get("canonical_id","")[:12]} with multiplication window {peak_width}. '
                 'This recipe differs from the C02 window-59 candidate. Its compressed operations match the canonical hash exactly. '
                 f'Replay ripple carries reach active Q={peak["first_global_peak"]["active"]:,} on {ripple_hits:,} allocation events; fold carries do so on {fold_hits:,} events. '
                 f'The first peak includes {first_ripple} ripple carries and {first_boundaries} live chunked-add boundary carries. '
                 'Fold-only scratch reductions leave these other replay peaks unchanged. Event counts measure peak-reaching allocations. Runtime has a separate receipt. '
                 'The diagnostic adds no reliability evidence.' if peak.get('accepted') else
                 'The frozen peak diagnostic has no accepted result in this snapshot.')
    matched_label = 'Fixed-scalar attribution controls: '+data['matched_controls_scope']
    prediction_rows=[]
    measured={row['variant']:row for row in data['matched_controls']}
    measured_base=measured.get('selected',{})
    for row in data['repair_prediction'].get('variants',[]):
        actual=measured.get(row['name'],{})
        prediction_rows.append({'variant':row['name'],'repair_T':row.get('expected_repair_T') if row['known'] else 'Unknown: no prior profile',
                                'predicted_delta':row.get('delta_expected_repair_T_vs_selected') if row['known'] else 'Unknown',
                                'measured_whole_T_delta':actual['T']-measured_base['T'] if actual and measured_base else None,
                                'measured_Q_delta':actual['Q']-measured_base['Q'] if actual and measured_base else None})
    prediction_fields=[('variant','Variant'),('repair_T','Predicted repair T'),('predicted_delta','Repair delta vs selected'),
                       ('measured_whole_T_delta','Measured whole-T delta'),('measured_Q_delta','Measured Q delta')]
    c01_text='Matched control results are pending.'
    c01_limit='The per-input records contain no executed-T counts. A paired standard error is unavailable. Tiny whole-T differences receive no statistical primitive-rule credit.'
    if all(key in measured for key in ('selected','without_all','without_inlet','original_error_windows','prior_frontier_recipe')):
        selected=measured['selected'];original=measured['original_error_windows'];prior=measured['prior_frontier_recipe']
        exact_step=prior['S']-original['S'];window_step=original['S']-selected['S'];total=prior['S']-selected['S']
        inlet_delta=measured['without_inlet']['T']-selected['T']
        matched_delta=measured['without_all']['S']-selected['S']
        c01_text=(f'On the common 16,384-pair packet, Q stays 1,316 in all seven controls. '
                  f'The observed S decrease from the prior recipe to all exact rules at original windows is {exact_step:,}. '
                  f'The next step to multiplication window 59 decreases S by {window_step:,}, or {100*window_step/total:.1f}% of the observed total difference. '
                  f'At window 59, selected versus all new exact rules disabled differs by {matched_delta:,} S. '
                  'These are finite-batch observations and establish no qualified gain. '
                  f'The repair-only prediction without inlet retirement is +6.5 T; the observed whole-T difference is +{inlet_delta:.3f}. '
                  'Measurement-order noise or unmatched CCX regions may contribute. The discrepancy remains unresolved.')
        data['c01_observed_decomposition']={'exact_step_S':exact_step,'window_step_S':window_step,
                                          'window_fraction_of_observed_total':window_step/total,
                                          'matched_without_all_delta_S':matched_delta,
                                          'inlet_repair_prediction_delta_T':6.5,'inlet_observed_whole_delta_T':inlet_delta,
                                          'statistical_primitive_credit':False}
    primary_receipts = ('The [primary stop record](full-point-add/selection-protocol-amendment/no-finalist.json) records the no-finalist outcome.'
                        if primary_stopped else
                        'The [audit receipt](full-point-add/audit-result.json) records the independent result.' if audit else
                        'The primary independent audit has no completed receipt.')
    archive_info = data['circuit_archive']
    archive_index = archive_info['index']
    archive_verify = archive_info['verification']
    rebuild_count = sum(row.get('verified') is True for row in archive_info['reconstructions'].get('results',[]))
    archive_text = (f'The recipe index records {number(archive_index.get("entries"))} native artifacts and '
                    f'{number(archive_index.get("expected_raw_bytes"))} operation-file bytes. '
                    'Each entry stores its recipe, expected byte count and hash, with a frozen generator and source identity. '
                    f'The measured reconstruction count is {rebuild_count}; every saved reconstruction matched its original bytes. '
                    f'The separate {archive_verify.get("verified_utc")} receipt verified '
                    f'{number(archive_verify.get("present_raw_files_verified"))} original local files. '
                    'Original operation files remain local during the campaign. The repository retains the recipe index, codebook and selected raw artifacts.')
    timing_rows = [{'stage': k, 'seconds': v} for k,v in data['process_timing_seconds'].items()]
    summary = {k:v for k,v in data.items() if k not in ('archive','ledger','root','structural','greedy')}
    summary['ledger_message_count'] = len(data['ledger'])
    summary['artifact'] = data['finalist'].get('artifact')
    (root/'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    status = esc(data['stage'].capitalize())
    boundary = ('The final success gate requires the complete fresh audit, a one-sided failure bound at most 1e-5, '
                'and the pinned official pass on the same frozen circuit. Two streams count as one input pair.')
    finding = (f'Qualified S is {number(top_S)}.' if top_S is not None else
               'The primary selector produced no confirmed finalist. No new primary qualified S is reported.' if primary_stopped else
               'This snapshot has no newly qualified S.')
    amendment = data['budget'].get('amendment', {})
    amendment_text = (f'Budget amendment at {amendment.get("time")}: {amendment.get("authorisation")}. '
                      f'{amendment.get("interpretation")} The first {data["segment_boundary"]} search slots remain preserved.'
                      if amendment else 'The initial campaign budget remains in force.')
    findings = [
        '# Grammar swarm pilot', '', f'Generated at {data["rendered_utc"]}. Status: **{data["stage"]}**.', '',
        'The task is to reduce the cost of complete 256-bit point addition with guarded, location-aware circuit edits.',
        'The search can retain neutral or larger exact parents and combine edits. The fixed evaluator checks output, phase and scratch.', '',
        finding, boundary, '',
        f'The 64-pair screen best is {number(best_screen)}. The separate passing-512 best is {number(best512)}.',
        f'The previous campaign reported a qualified control at S = {PRIOR_S:,}. Its evidence belongs to that earlier campaign.', '',
        '![Native search](figures/native-search.png)', '',
        'The dashed curve uses 64 common input pairs. A screen endpoint can fail its later 512-pair check.',
        'The solid curve uses only endpoints with zero observed failures on the 512-pair packet.',
        'The final selected, audited and official measurements use different inputs and can have different mean Toffoli counts.', '',
        selection_text, limitation, '',
        '![Primary selection funnel](figures/selection-funnel.svg)', '',
        md_table(data['selection_rows'],selection_fields), '',
        '![Selection on larger development packets](figures/selection-stages.png)', '',
        f'This snapshot has {data["slots"]} proposal slots, {data["archived_recipes"]} archived recipes, '
        f'{data["distinct_gate_streams"]} distinct gate streams, and maximum ancestry depth {data["maximum_depth"]}.', '',
        'The original reference costs about 10.705 billion and has known historical phase faults.',
        'It provides cost context. Its small current packet supplies no reliability qualification.', '',
        md_table(list(data['controls'].values()), control_fields), '',
        '![Cost context](figures/cost-context.png)', '',
        ('The primary audit and official check did not run because no candidate passed the frozen confirmation rule.'
         if primary_stopped else f'The final audit records {number(audit.get("failed_inputs"))} failed input pairs out of '
        f'{number(audit.get("pairs",progress.get("completed_pairs")))}. Its upper bound is '
        f'{number(audit.get("epsilon_upper95"))}. The official pass is '
        f'{"recorded" if q["official_pass"] else "pending or absent"}.'),
        primary_receipts, '',
        matched_label,
        'C01 holds the candidate scalar settings fixed while it disables exact storage rules.',
        'Its variants cannot change the primary result or the exploratory candidate. Conditional-T sample noise supplies no primitive-rule credit.', '',
        md_table(data['matched_controls'], [('name','Matched variant'),('Q','Q'),('T','Mean T'),('S','S'),
                                           ('pairs','Input pairs'),('failed_inputs','Failed inputs')]), '',
        c01_text,c01_limit,'',
        'The repair expectation was recorded before the control draws. It sums (chunk−1)/2 over actual split-fold profiles.',
        'Predicted repair deltas cover that module alone. Whole-circuit T predictions remain unknown because other CCX regions were not independently matched.', '',
        md_table(prediction_rows,prediction_fields),'',
        '## Retired-inlet invariant', '',
        'The inlet c is the carry out of bit 0 and into bit 1. Let a be the old accumulator bit and z the selected addend bit.',
        'The low-bit sum is s. After the low chunk unwinds, s and z decode the carry:', '',
        '$$',r's=a\oplus z,\qquad c=az=z(1\oplus s)=z\oplus(sz).','$$','',
        'HMR measures in the X basis and clears the measured wire. Outcome m contributes the phase (-1)^(mc).',
        'Conditional Z(z) and CZ(s,z) apply the same phase and cancel it.',
        'The source distributes these Clifford corrections over the unchanged selector parity for z.',
        'The inverse path retires c only after the low chunk is final and c has no later consumer. The high chunk uses its boundary carry.',
        'The guards require a real split, an inverse retirement request, and unchanged disjoint selectors. The forward caller retains its inlet.',
        'HMR clears c before the builder releases it. The [native source](../../native/generator/src/pingpong/pingpong.rs) implements this identity and its phase cleanup.', '',
        'The current displayed recipe uses these scalar settings. A narrower fold window changes an inherited approximation.', '',
        md_table(data['scalar_comparison'], [('setting','Setting'),('prior','Prior local control'),('selected','Displayed recipe')]), '',
        '## Separate exploratory C02 audit', '', calibration_status+'.',
        'C02 freezes the rejected w59 candidate before one million fresh pairs under two streams.',
        'It measures this candidate against the declared failure criterion. It cannot replace the primary finalist or estimate the selection policy error rate.', '',
        md_table(calibration_rows,[('Q','Q'),('T','Mean T'),('S','S'),('pairs','Input pairs'),
                                   ('failed_inputs','Failed inputs'),('epsilon_upper95','95% failure upper bound'),('exploratory_meets_epsilon','Audit criterion met')]), '',
        '[C02 preregistration](diagnostics/rejected-candidate-audit/PREREGISTRATION.json) retains the scope and prior decision.', '',
        '## Resource peak explanation', '',peak_text,'',
        md_table(peak_rows,[('phase','Phase'),('location','Allocation site'),('count','Peak-reaching events'),('active','Active Q')]),'',
        '[The identity receipt](diagnostics/peak-profile/result.json) retains all first-peak owners and protected source hashes.', '',
        'This bounded grammar study supplies no circuit-saturation result. The peak map identifies replay carries and retained sign/tape wires as future targets.', '',
        'The separate modular-cell study saves 9.33% and 9.13% in stationary windows at 16 and 256 bits.',
        'Ordinary payloads tie their best global controls. Native ping-pong replay has no direct stationary-mask match.',
        'The cell gains therefore supply no numerical forecast for complete point addition.', '',
        '![Cell search](figures/cell-search.png)', '',
        'Structural retention and the cost elite reached the same cell endpoints.',
        'The greedy payload runs stopped after 58 and 61 evaluations against 96 planned. Their common prefixes still tie.',
        'Eight saved endpoints passed 4,096 fresh tuples per cell under two streams. This finite check supplies no 1e-5 qualification.', '',
        'The native mechanisms are streamed boundary repair, early inverse-inlet retirement, and in-place routed-selector borrowing.',
        'Their guards and derivations appear in [the source hypotheses](sources/HYPOTHESES.md) and [the native report](native-rule/REPORT-v2.md).',
        'The source review supports these circuit transformations. This work makes no claim of a new arithmetic algorithm.', '',
        f'Campaign budget: {data["budget"].get("started_utc")} to {data["budget"].get("deadline_utc")}.',
        amendment_text,
        f'The native configuration records search cutoff {data["config"].get("search_cutoff_utc")}. '
        f'The budget file records {data["budget"].get("search_cutoff_utc")}.',
        'Timing totals come from saved process receipts. Overlapping process times do not equal elapsed campaign time.',
        f'The loops made zero CLI/API model calls. The saved goal-service counter records '
        f'{number(data["model_usage"]["goal_service_tokens"])} total tokens and '
        f'{number(data["model_usage"]["goal_service_elapsed_seconds"])} elapsed seconds.',
        'Provider input/output/cache and per-agent splits, model prices and billed spend are unavailable.', '',
        'See [the dashboard](dashboard.html), [the machine-readable summary](summary.json), and [the ledger](ledger/DISCUSSION.md).',
        'The dashboard links the controls, rule counts, frozen configuration and replay instructions.', '',
        '## Circuit retention and reconstruction', '',archive_text,'',
        'The saved executable and sandbox require the recorded macOS platform. Cross-platform binary reproducibility remains untested.',
        'Read [the archive guide](circuit-archive/README.md) for bounded hydration of operation files outside the retained raw subset.', '',
        '[The raw-retention policy](circuit-archive/retained-raw-circuits.json) lists seven files totalling 355,441,798 bytes.', '',
        'Rerender with:', '', '```sh',
        'PYTHONPATH=src /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 tools/report_grammar_swarm.py runs/grammar-swarm-20261023', '```',
    ]
    (root/'FINDINGS.md').write_text('\n'.join(findings) + '\n')
    ledger_rows = []
    for row in reversed(data['ledger']):
        references = ' '.join(f'<a href="{esc(source_href(ref))}">{esc(ref)}</a>' for ref in row.get('references',[]) if not ref.startswith('http'))
        external = ' '.join(f'<a href="{esc(ref)}">Primary source</a>' for ref in row.get('references',[]) if ref.startswith('http'))
        ledger_rows.append(f'<details id="ledger-{esc(row["id"])}"><summary>{esc(row["agent"])} · {esc(row["kind"])} · {esc(row["id"])}</summary>'
                           f'<p>{esc(row["message"])}</p><p class="muted">{esc(row["time"])} · reply to {esc(row.get("reply_to") or "—")}</p>'
                           f'<p>{external} {references}</p></details>')
    evidence_links = [('full-point-add/config.json','Frozen native configuration'), ('full-point-add/archive.json','Native archive'),
                      ('full-point-add/trials.json','Every native proposal'), ('full-point-add/trace.json','Search trace'),
                      ('full-point-add/rule-counts.json','Native rule counts'), ('full-point-add/selection-results.json','Selection results'),
                      ('full-point-add/finalist.json','Frozen finalist'), ('full-point-add/audit-result.json','Independent audit'),
                      ('full-point-add/official-result.json','Official receipt'), ('native-rule/final-build-receipt.json','Native source and test receipt'),
                      ('sources/HYPOTHESES.md','Source hypotheses'), ('development/FINDINGS.md','Cell development findings'),
                      ('development/confirmation/results.json','Fresh cell replays'), ('ledger/DISCUSSION.md','Full attributed discussion')]
    primary_future={'full-point-add/finalist.json','full-point-add/audit-result.json','full-point-add/official-result.json','full-point-add/selection-results.json'}
    evidence_links=[(path,label) for path,label in evidence_links if path not in primary_future or (root/path).exists()]
    if primary_stopped:
        evidence_links.append(('full-point-add/selection-protocol-amendment/no-finalist.json','Primary no-finalist outcome'))
    links = ''.join(f'<li><a href="{path}">{label}</a>{"" if (root/path).exists() else " — pending"}</li>' for path,label in evidence_links)
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Grammar swarm pilot · measured circuit search</title><style>
body{{margin:0;background:#faf9f6;color:#232b30;font:16px/1.55 system-ui,sans-serif}}main{{max-width:1160px;margin:auto;padding:45px 24px 80px}}
h1{{font-size:42px;line-height:1.12;max-width:900px}}h2{{margin-top:48px;font-size:27px}}h3{{font-size:19px}}p{{max-width:960px}}a{{color:#12635f}}.tag{{color:#12635f;font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:12px}}
.lede{{font-size:20px;max-width:950px}}.muted,figcaption{{color:#59676b;font-size:13px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}}.card{{background:#fff;border:1px solid #d6ddd9;border-radius:8px;padding:20px}}.card strong{{display:block;font-size:28px;color:#12635f}}.status{{border-left:4px solid #12635f;padding:15px 20px;background:#edf3ee}}img,svg{{width:100%;height:auto}}figure{{margin:22px 0}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #d6ddd9}}th{{font-weight:650}}.scroll{{overflow:auto}}code,pre{{font:12px/1.55 ui-monospace,monospace}}pre{{padding:18px;background:#edf0ed;overflow:auto}}details{{border-top:1px solid #d6ddd9;padding:12px 0}}summary{{cursor:pointer;font-weight:600}}details p{{font-size:14px}}nav{{display:flex;gap:18px;flex-wrap:wrap}}.split{{display:grid;grid-template-columns:1fr 1fr;gap:25px}}@media(max-width:760px){{.grid,.split{{grid-template-columns:1fr}}h1{{font-size:32px}}main{{padding:28px 16px}}}}
</style></head><body><main><div class="tag">Actual circuits · guarded edits · frozen evaluator</div>
<h1>Can deeper, location-aware edits reduce complete point-addition cost?</h1>
<p class="lede">A reversible circuit must preserve its arithmetic result, phase and clean scratch. This pilot retains neutral or larger parents, combines edits, and measures the resulting circuits.</p>
<nav><a href="#native">Native search</a><a href="#qualification">Primary selection</a><a href="#calibration">C02 audit</a><a href="#cells">Cell development</a><a href="#rules">Rule use</a><a href="#discussion">Swarm discussion</a><a href="#replay">Evidence and replay</a></nav>
<p class="status"><strong>{status}.</strong> {esc(finding)}</p>
<p class="muted">{esc(amendment_text)}</p>
<div class="grid"><div class="card">New primary qualified S<strong>{esc(qualified_label)}</strong><span class="muted">Primary final audit + linked official pass required</span></div>
<div class="card">Passing 512-pair best<strong>{esc(number(best512))}</strong><span class="muted">Development evidence</span></div>
<div class="card">Previous campaign control<strong>{PRIOR_S:,}</strong><span class="muted">Separate prior qualification evidence</span></div></div>
<figure>{pipeline_svg()}<figcaption>The pipeline records each evidence stage. Each measurement stream belongs to one independently drawn input pair.</figcaption></figure>
<h2 id="native">Actual complete point-addition search</h2><p>Q counts the referenced logical qubits. T is the mean executed Toffoli count. S equals Q times rounded T. Positive half ties round upward. Lower S is better.</p>
<p>The search archive contains <strong>{data['archived_recipes']}</strong> recipes and <strong>{data['distinct_gate_streams']}</strong> distinct gate streams across <strong>{data['slots']}</strong> proposal slots. The search recorded <strong>{data['emitted']}</strong> emitted artifacts, including a timed-out record outside the archive. Maximum ancestry depth: <strong>{data['maximum_depth']}</strong>.</p>
<p>The controls are compact ping-pong (S3), the known fold split (PC001), and the previous qualified local control at cap 1316. The previous control belongs to an earlier local campaign. This report makes no claim about the current global challenge frontier.</p>
<figure><img src="figures/native-search.svg" alt="Measured native screen and passing development curves"><figcaption>The dashed 64-pair curve can include endpoints that fail a later check. The solid 512-pair curve includes only passing endpoints. The final audit point uses fresh inputs. Its mean T can differ.</figcaption></figure>
<p><a href="figures/native-search.png">PNG</a> · <a href="figures/native-search.pdf">PDF</a> · <a href="full-point-add/trace.json">Actual trace data</a></p>
{table(list(data['controls'].values()),control_fields)}
<figure><img src="figures/cost-context.svg" alt="Original and current native control costs"><figcaption>The original reference has <a href="../full-path-20261023/regressions/original-control/README.md">known historical phase faults</a>. Its current 64-pair pass supplies cost context. The zoomed search plot makes small changes around the prior local control visible.</figcaption></figure>
<h2 id="qualification">One frozen finalist, then fresh qualification</h2><p>{esc(boundary)}</p>
<p>{esc(selection_text)}</p><figure>{selection_funnel_svg(data)}<figcaption>Observed primary selection counts. Zero primary finalists means the primary million-pair audit and official check did not start.</figcaption></figure>
{table(data['selection_rows'],selection_fields)}
<figure><img src="figures/selection-stages.svg" alt="Actual stage-one and confirmation scores and observed failures"><figcaption>Each point is an actual saved packet result. Failure labels count input pairs. Squares show the separate 65,536-pair confirmation. The mean T can differ between packets.</figcaption></figure>
<p>{esc(limitation)}</p><p><a href="full-point-add/selection-protocol-amendment/protocol.json">Prospective selection protocol</a> · <a href="full-point-add/selection-protocol-amendment/stage1-results.json">Stage one</a> · <a href="full-point-add/selection-protocol-amendment/confirmation-results.json">Confirmation</a></p>
{table(stage_rows,[('stage','Stage'),('pairs','Completed inputs / shots'),('failed','Failed inputs'),('bound','Failure upper bound'),('passed','Pass recorded')])}
<p>Audit progress: {number(progress.get('completed_pairs',audit.get('pairs')))} of {number(data['config'].get('audit_pairs',1000000))} planned pairs. Freeze precedes fresh inputs. The controller permits no replacement after audit.</p>
<p>The previous qualified control remains a separate reference. A new development low supplies no final improvement claim.</p>
<h3>{esc(matched_label)}</h3><p>C01 disables exact storage rules one at a time and together, while it holds the candidate's scalar settings fixed. These variants cannot change the primary result or the exploratory candidate. Compare Q and matched mean T before assigning rule credit. Approximate window changes remain separate choices; conditional-T sample noise supplies no primitive-rule saving.</p>
{table(data['matched_controls'],[('name','Matched variant'),('Q','Q'),('T','Mean T'),('S','S'),('pairs','Input pairs'),('failed_inputs','Failed inputs')])}
<p>{esc(c01_text)}</p><p>{esc(c01_limit)}</p>
<p>The repair expectation was recorded before control draws. It sums (chunk−1)/2 over actual split-fold profiles. It predicts repair-module expectation only. Whole-circuit T differences remain unknown because other CCX regions were not independently matched.</p>
{table(prediction_rows,prediction_fields)}
<p><a href="diagnostics/rejected-candidate-controls/repair-prediction.json">Timestamped repair prediction</a>. A missing pre-control profile remains unknown.</p>
<p><a href="{esc(data['matched_controls_path'])}">Matched measurements</a> · <a href="#ledger-a20f70a910c6">Prior C01 hypothesis</a></p>
<details><summary>Scalar settings: {esc(data['displayed_recipe_scope'])}</summary><p>Recipe <code>{esc(data['displayed_recipe_id'])}</code>. These fields include inherited approximate windows and phase-comparison widths.</p>{table(data['scalar_comparison'],[('setting','Scalar setting'),('prior','Prior local control'),('selected','Displayed recipe')])}</details>
<h2 id="calibration">Separate exploratory C02 audit</h2><p><strong>{esc(calibration_status)}.</strong> C02 freezes the rejected w59 candidate before one million fresh input pairs under two streams. It tests this candidate against the audit criterion. The primary result remains fixed. Policy-level error rates remain unmeasured.</p>
<p>Completed C02 input pairs: {number(calibration_progress)} of 1,000,000. Failed inputs in completed chunks: {number(calibration_failed)}. The driver completes every declared chunk.</p>
{table(calibration_rows,[('Q','Q'),('T','Mean T'),('S','S'),('pairs','Input pairs'),('failed_inputs','Failed inputs'),('epsilon_upper95','95% failure upper bound'),('exploratory_meets_epsilon','Audit criterion met')])}
<p><a href="diagnostics/rejected-candidate-audit/PREREGISTRATION.json">C02 preregistration</a>. The cause of the shared confirmation failure remains unresolved. The primary qualified headline stays separate.</p>
<p><a href="full-point-add/diagnostics/rejected-candidate-audit/contract.json">Separate C02 contract</a> · <a href="full-point-add/diagnostics/rejected-candidate-audit/result.json">C02 result</a></p>
<h3>Why local scratch savings can leave global Q unchanged</h3><p>{esc(peak_text)}</p>
{table(peak_rows,[('phase','Phase'),('location','Allocation site'),('count','Peak-reaching events'),('active','Active Q')])}
<p><a href="diagnostics/peak-profile/result.json">Exact artifact identity and peak owners</a>. A lower feasible cap or a change to the replay allocation schedule must also address these non-fold peaks.</p>
<p>This bounded grammar study supplies no circuit-saturation result. The peak map identifies replay carry allocations and retained sign/tape wires as future targets.</p>
<h2 id="cells">Separate modular-cell development</h2><p>The stationary window retains one masked multiplicand across repeated arithmetic. The window saves 9.33% at 16 bits and 9.13% at 256 bits. Ordinary payloads tie their best global controls.</p>
{table(data['cells'],cell_fields)}
<figure><img src="figures/cell-search.svg" alt="Actual modular-cell structural and cost-elite search curves"><figcaption>Each curve uses the saved candidate evaluations. The first four evaluations establish the global controls. Both policies reach the same endpoints. Greedy payload runs exhaust their parent-attempt budget after 58 and 61 evaluations, against 96 planned.</figcaption></figure>
<p>Eight fixed endpoints passed 4,096 fresh tuples per cell under two measurement streams. This finite result supplies no 1e-5 qualification. Native replay changes operand roles and signs. The source review found no direct stationary-mask match, so the 9% cell gain supplies no numerical point-addition forecast.</p>
<h2 id="rules">Native rules and observed use</h2><div class="split"><div><h3>Exact storage mechanisms</h3><p>Streamed boundary repair recreates each selected constant bit on one scratch wire. Early inverse-inlet retirement releases a carry after its last use. Routed-selector borrowing reuses a live control and restores it before cleanup.</p><p><a href="native-rule/REPORT-v2.md">Derivation and exact local checks</a> · <a href="sources/HYPOTHESES.md">Sources, guards and critiques</a></p></div><div><h3>Scoped edits and global controls</h3><p>Per-site edits name actual fold occurrences. Apply-all switches cover every applicable occurrence. A request can stay inactive when its guards fail. Scalar edits also vary caps, windows and compare widths; their approximation failures remain in the evaluator.</p></div></div>
<p>For an n-bit boundary with n at least two, streamed repair reduces scratch from 2n−1 wires to n at a fixed comparison width. Inlet retirement releases one wire before the high fold. Routed-selector reuse saves one live wire and two CX gates at a fixed fold layout. The local exact tests verify phase and clean scratch. C01 measures global effects with scalar settings fixed.</p>
<details><summary>Retired-inlet invariant and phase decoder</summary><p>The inlet c is the carry out of bit 0 and into bit 1. Let a be the old accumulator bit, z the selected addend bit, and s the low sum bit.</p><pre>s = a XOR z
c = a AND z = z AND (NOT s) = z XOR (s AND z)</pre><p>HMR outcome m contributes phase (−1)^(mc). Conditional Z(z) and CZ(s,z) apply the same phase and cancel it. The source distributes the corrections over the unchanged selector parity for z.</p><p>The inverse caller retires c after the low chunk is final and c has no later consumer. The high chunk uses the boundary carry. Guards require a real split, the inverse retirement request, and unchanged disjoint selectors. The forward caller retains its inlet. HMR clears c before release.</p><p><a href="../../native/generator/src/pingpong/pingpong.rs">Native implementation</a> · <a href="native-rule/REPORT-v2.md">Exact tests and resource evidence</a></p></details>
{table(data['rules'],rule_fields)}
<p class="muted">Opportunities count legal actions offered at selected parents. Emitted counts count candidate records. Profiles record site activations. Inactive counts apply to scoped edit requests. These totals do not isolate a rule's causal contribution. Global seed combinations retain joint attribution.</p>
<p>Scoped requests: {number(data['scoped_activity'].get('requested_records',0))}; emitted scoped records: {number(data['scoped_activity'].get('emitted_records',0))}; profile matches: {number(data['scoped_activity'].get('profile_match_records',0))}; emitted operation streams changed from the primary parent: {number(data['scoped_activity'].get('changed_primary_parent_streams',0))}. A matching mode-zero request can preserve all gates. Combined candidates retain both parent edges; this hash comparison uses the first listed parent.</p>
<p>Construction rejections with a saved native panic: {len(data['construction_rejection_slots'])}. Other instrument-error records: {len(data['other_instrument_error_slots'])}. Other invalid proposals: {data['proposal_status_counts'].get('invalid',0)}. Construction rejections have no complete circuit or checker outcome. Their original status strings and stderr remain in the archive.</p>
<h2 id="discussion">Source review and swarm discussion</h2><p>The source agents proposed guards and critiqued control lifetimes. The native worker implemented and tested exact rules. The development worker tested modular cells. The coordinator selected the native contract and finalist.</p>
<figure>{ledger_svg(data['ledger'])}<figcaption>Recorded reply links connect the roles. The boxes open their latest messages below. This graph uses the saved ledger.</figcaption></figure>
<details><summary>Read {len(data['ledger'])} attributed ledger messages</summary>{''.join(ledger_rows)}</details>
<h2>Time and model accounting</h2><p>The campaign runs from {esc(data['budget'].get('started_utc'))} to {esc(data['budget'].get('deadline_utc'))}. The native configuration records cutoff {esc(data['config'].get('search_cutoff_utc'))}; the budget file records {esc(data['budget'].get('search_cutoff_utc'))}.</p>
{table(timing_rows,[('stage','Saved process stage'),('seconds','Sum of wall seconds')])}
<p>The modular development receipts sum to {number(data['development_aggregate'].get('sum_case_wall_seconds'))} case wall seconds. The latest native trace records {number(data['native_search_elapsed_seconds'])} search seconds, including cached records and controller work. Process sums can overlap. They do not equal elapsed campaign time. Host-agent orchestration time has no complete process receipt.</p>
<p>The measured loops made zero CLI/API model calls. The saved goal-service counter records <strong>{number(data['model_usage']['goal_service_tokens'])}</strong> total tokens and <strong>{number(data['model_usage']['goal_service_elapsed_seconds'])}</strong> elapsed seconds. This aggregate is separate from CLI/API receipts.</p>
<p>Provider input/output/cache splits, per-agent splits, model prices and billed spend are unavailable. <a href="{esc(data['goal_meter_file'])}">Saved goal-meter snapshot</a> · <a href="profiling/ACCOUNTING.md">Accounting scope and timestamps</a>. The report makes no cost-efficiency or model-superiority claim.</p>
<h2 id="replay">Evidence and replay</h2><div class="split"><ul>{links}</ul><div><p>Render this snapshot again:</p><pre>PYTHONPATH=src /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 tools/report_grammar_swarm.py runs/grammar-swarm-20261023</pre><p>Review the saved inputs, expected values, measurement labels and operation hashes before replay. The frozen controller validates them without resampling completed records.</p><pre>PYTHONPATH=src /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 tools/run_swarm_native.py audit runs/grammar-swarm-20261023/full-point-add</pre><p class="muted">Run the audit replay after the complete result exists. It verifies the saved final cohort. The report command reads evidence and writes report artifacts.</p><p><a href="../../docs/grammar-swarm-pilot.md">Reproduction guide</a> · <a href="FINDINGS.md">Findings</a> · <a href="summary.json">Summary JSON</a></p></div></div>
<h3>Circuit retention and exact reconstruction</h3><p>{esc(archive_text)}</p><p>Hydration accepts a reconstruction only when its bytes match the saved length and SHA-256. The recorded executable and emission sandbox require macOS. Cross-platform binary reproducibility remains untested.</p>
<pre>PYTHONPATH=src /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 tools/package_swarm_circuits.py hydrate runs/grammar-swarm-20261023/full-point-add --id CANDIDATE_ID --replay-wall-seconds 600</pre>
<p>Use the explicit replay budget after the campaign. Hydration adds no evaluation inputs. <a href="circuit-archive/README.md">Archive guide</a> · <a href="circuit-archive/index-receipt.json">Current index</a> · <a href="circuit-archive/self-check-receipt.json">Measured reconstructions</a> · <a href="{esc(archive_info['verification_file'])}">Latest raw-file verification receipt</a> · <a href="circuit-archive/retained-raw-circuits.json">Seven retained raw circuits: 355,441,798 bytes</a></p>
<p class="muted">Snapshot rendered {esc(data['rendered_utc'])}. Pending files remain pending. This report makes no claim of a new arithmetic algorithm.</p>
</main><script>function revealLedger(){{const node=document.getElementById(location.hash.slice(1));if(!node)return;for(let p=node;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;node.scrollIntoView();}}window.addEventListener('hashchange',revealLedger);if(location.hash)revealLedger();</script></body></html>'''
    (root/'dashboard.html').write_text(page)
    return summary
