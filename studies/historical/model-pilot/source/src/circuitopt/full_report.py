"""Render measured full-circuit construction and search recovery results."""
import csv
import html
import json
from pathlib import Path
import statistics

from . import full_path, full_native as native, full_integrity, full_profile

HEADERS = {
    'edit': 'Edit', 'change': 'Rule application', 'failed_inputs': 'Failed inputs',
    'pairs': 'Inputs', 'strategy': 'Strategy', 'seed': 'Seed', 'proposals': 'Proposals',
    'median_proposals_to_endpoint_cost': 'Median proposals to endpoint cost',
    'successful_runs': 'Successful runs', 'runs': 'Runs',
    'first_reference_recipe': 'First exact recipe',
    'first_reference_cost': 'First endpoint cost', 'first_google_cost': 'First below Google cost',
    'measured_cleanup': 'Measured cleanup', 'correction_fusion': 'Correction fusion',
    'split_walk': 'Split walk', 'epsilon_upper_adjusted': '95% failure bound',
    'qualified': 'Meets failure target',
}


def display(row, key):
    value = row.get(key)
    if key == 'strategy':
        return {'random': 'Random', 'evolution': 'Evolution', 'llm': 'Evolution + Claude'}.get(value, value)
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return str(value)


def report(root):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    root = Path(root).resolve()
    config = full_integrity.read_sealed(root / 'config.json')
    profiling = full_profile.profile(root, Path(__file__).resolve().parents[2])
    costs = profiling['costs']
    oracle = json.loads((root / 'development.json').read_text())
    construction = json.loads((root / 'construction.json').read_text())
    searches = json.loads((root / 'search-results.json').read_text()) if (root / 'search-results.json').exists() else []
    audit = json.loads((root / 'audit-final.json').read_text()) if (root / 'audit-final.json').exists() else {}
    audit_status = audit.get('status', 'running' if (root / 'audit-final-contract.json').exists() else 'not yet run')
    audit_rows = list(audit.get('rows', {}).values())
    qualified = audit_status == 'complete' and bool(audit_rows) and all(r['qualified'] for r in audit_rows)
    audit_label = 'Qualified' if qualified else audit_status
    audit_details = '; '.join(f'{r["failed_inputs"]:,}/{r["pairs"]:,} failed inputs; '
                             f'95% upper bound {r["epsilon_upper_adjusted"]:.3g}' for r in audit_rows)
    audit_table = [{k: r[k] for k in ['Q', 'T', 'S', 'failed_inputs', 'pairs', 'epsilon_upper_adjusted', 'qualified']}
                   for r in audit_rows]
    official_file = root / 'official/result.json'
    official = json.loads(official_file.read_text()) if official_file.exists() else {}
    official_observed = official.get('observed', {})
    official_text = ('The one local official check passed all 9,024 shots. '
                     f'It reported Q = {official_observed["score"]["metrics"]["qubits"]:,}, '
                     f'rounded T = {official_observed["score"]["metrics"]["toffoli"]:,}, '
                     f'and S = {official_observed["score"]["score"]:,}. No remote submission occurred.'
                     if official.get('official_check_passed') else 'The local official check has no passing result recorded.')
    effective_edits = sum(a['artifact_sha256'] != b['artifact_sha256'] for a, b in zip(construction, construction[1:]))
    distinct_streams = len({r['artifact_sha256'] for r in oracle.values()})
    figures = root / 'figures'
    figures.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'axes.spines.top': False,
                         'axes.spines.right': False, 'figure.facecolor': '#faf9f6',
                         'axes.facecolor': '#faf9f6', 'font.size': 11})
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.plot([r['edit'] for r in construction], [r['S'] / 1e9 for r in construction],
            marker='o', color='#12635f', linewidth=2.5)
    ax.axhline(3, color='#b76827', linestyle='--', label='Google reference: approximately 3.0 billion')
    ax.set(xlabel='Applied structural edits', ylabel='Full point-addition S (billions)',
           title='Supplied construction: actual 256-bit circuits')
    ax.set_xticks(range(len(construction)))
    for r in construction:
        ax.annotate(f'{r["S"] / 1e9:.3f}', (r['edit'], r['S'] / 1e9), xytext=(5, 9), textcoords='offset points')
    ax.legend(frameon=False)
    fig.tight_layout()
    for ext in ['svg', 'png', 'pdf']:
        fig.savefig(figures / f'construction.{ext}', dpi=180)
    plt.close(fig)
    if searches:
        fig, ax = plt.subplots(figsize=(10, 5))
        colors = {'random': '#68758b', 'evolution': '#12635f', 'llm': '#b76827'}
        styles = {'random': '-', 'evolution': '--', 'llm': ':'}
        labels = {'random': 'Random search', 'evolution': 'Evolution', 'llm': 'Evolution + Claude'}
        for strategy in config['strategies']:
            group = [r for r in searches if r['strategy'] == strategy]
            if not group:
                continue
            length = min(len(r['trace']) for r in group)
            values = [[r['trace'][i]['best_S'] / 1e9 for r in group] for i in range(length)]
            x = list(range(length))
            ax.step(x, [statistics.median(v) for v in values], where='post',
                    color=colors[strategy], linestyle=styles[strategy],
                    linewidth=3.2 if strategy == 'evolution' else 2.3, label=labels[strategy])
            ax.fill_between(x, [min(v) for v in values], [max(v) for v in values],
                            step='post', alpha=.13, color=colors[strategy])
        ax.axhline(3, color='#383b40', linestyle='--', linewidth=1, label='Google reference')
        target = construction[-1]['S'] / 1e9
        ax.axhline(target, color='#9478a9', linestyle=':', linewidth=1, label='Supplied construction endpoint')
        ax.set(xlabel='Proposals, including duplicates and failures', ylabel='Best development S (billions)',
               title='Recovery of a supplied construction — measured circuit costs')
        ax.legend(frameon=False, fontsize=9)
        fig.tight_layout()
        for ext in ['svg', 'png', 'pdf']:
            fig.savefig(figures / f'autoresearch.{ext}', dpi=180)
        ax.set_xlim(0, 20)
        for ext in ['svg', 'png']:
            fig.savefig(figures / f'autoresearch-first20.{ext}', dpi=180)
        plt.close(fig)
    table = []
    for row in construction:
        table.append({'edit': row['edit'], 'rule': row.get('rule_id') or 'baseline',
                      'change': full_path.RULES.get(row.get('rule_id'), {}).get('name', 'Original challenge circuit'),
                      **{k: row[k] for k in ['Q', 'T', 'S', 'failed_inputs', 'pairs', 'epsilon_upper95']}})
    write_csv(root / 'construction.csv', table)
    recovery = [{k: r[k] for k in ['strategy', 'seed', 'selected_S', 'selected_ancestry_edits', 'distinct_tested_recipes']}
                | {'first_reference_recipe': r['first_hits']['recipe'],
                   'first_reference_cost': r['first_hits']['cost'],
                   'first_google_cost': r['first_hits']['google'],
                   'proposals': len(r['trials']), 'duplicates': r['counts'].get('duplicate', 0)} for r in searches]
    lineages = []
    for run, row in zip(searches, recovery):
        paths = {full_path.recipe_id(full_path.starting_recipe()): []}
        for trial in run['trials']:
            if trial['status'] in ['duplicate', 'invalid']:
                continue
            paths[trial['id']] = paths[trial['parent_id']] + [trial]
        steps = paths[run['selected']]
        row['selected_effective_edits'] = run['selected_effective_edits']
        row['selected_rule_sequence'] = ' -> '.join(t['rule_id'] for t in steps)
        lineages.append({'strategy': run['strategy'], 'seed': run['seed'],
                         'selected': run['selected'], 'steps': steps})
    native.save(root / 'lineages.json', lineages)
    write_csv(root / 'recovery.csv', recovery)
    medians = []
    for strategy in config['strategies']:
        group = [r for r in recovery if r['strategy'] == strategy]
        hits = [r['first_reference_cost'] for r in group if r['first_reference_cost'] is not None]
        if group:
            medians.append({'strategy': strategy,
                            'median_proposals_to_endpoint_cost': statistics.median(hits) if hits else None,
                            'successful_runs': len(hits), 'runs': len(group)})
    write_csv(root / 'recovery-medians.csv', medians)
    controls = []
    for recipe in full_path.candidate_pool()[1:]:
        r = oracle[full_path.recipe_id(recipe)]
        controls.append({**recipe, **{k: r[k] for k in ['Q', 'T', 'S', 'failed_inputs', 'pairs']}})
    write_csv(root / 'controls.csv', controls)
    contributions = []
    for field in full_path.FIELDS:
        for recipe in full_path.candidate_pool()[1:]:
            if recipe[field]:
                continue
            after = {**recipe, field: True}
            a, b = oracle[full_path.recipe_id(recipe)], oracle[full_path.recipe_id(after)]
            contributions.append({'mechanism': field, 'other_settings': json.dumps({k: recipe[k] for k in full_path.FIELDS if k != field}),
                                  'before_S': a['S'], 'after_S': b['S'], 'delta_T': b['T'] - a['T'],
                                  'delta_Q': b['Q'] - a['Q'], 'gain_percent': 100 * (1 - b['S'] / a['S']),
                                  'before_failures': a['failed_inputs'], 'after_failures': b['failed_inputs']})
    write_csv(root / 'mechanism-effects.csv', contributions)
    tokens = {'calls': 0, 'failed_calls': 0, 'input_tokens': 0, 'output_tokens': 0,
              'cached_input_tokens': 0, 'reasoning_tokens': 0, 'unknown_usage_calls': 0,
              'unknown_reasoning_calls': 0, 'provider_cost_estimate_usd': 0.0}
    for run in searches:
        for call in run['model_calls']:
            receipt = call['receipt']
            tokens['calls'] += 1
            tokens['failed_calls'] += receipt.get('status') != 'completed'
            usage = receipt.get('usage')
            if not usage:
                tokens['unknown_usage_calls'] += 1
                continue
            for key in ['input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_tokens', 'provider_cost_estimate_usd']:
                if usage.get(key) is not None:
                    tokens[key] += usage[key]
            tokens['unknown_reasoning_calls'] += usage.get('reasoning_tokens') is None
    native.save(root / 'model-usage.json', tokens)
    first_cross = next((r['edit'] for r in construction if r['S'] <= 3e9 and not r['failed_inputs']), None)
    findings = [
        '# Full-circuit construction and recovery', '',
        'Goal: count the structural edits in a supplied full-circuit construction, then measure search effort to recover it.', '',
        f'The supplied construction has four rule applications and {effective_edits} changes to the emitted gates. '
        f'Its first development result below 3.0 billion occurs at edit {first_cross}.',
        'PA01 imports a complete ping-pong implementation. Its internal changes receive joint attribution.',
        'PA02, PA03, and PA04 expose cleanup, correction fusion, and walk storage through controlled comparisons.', '',
        'PA04 is inactive under the fixed live-space budget. Every split-walk on/off pair has identical gates.',
        f'The nine recipes therefore represent {distinct_streams} distinct compiled circuits.', '',
        md_table(table, ['edit', 'change', 'Q', 'T', 'S', 'failed_inputs', 'pairs']), '',
        'Each development row uses 512 independent inputs and two measurement streams.',
        'Q counts referenced logical qubits. T is the average executed Toffoli count. S equals Q times rounded T.',
        'A development pass supplies no proof or 1e-5 qualification.', '',
        'The original control has two known phase-failing inputs in a separate 32,768-input historical test.',
        'Its canonical operation hash matches the saved control. The [regression record](regressions/original-control/README.md) retains the counterexamples.',
        'The original serves as a starting cost reference. This study does not admit it as a reliability-qualified baseline.', '',
        '![Measured construction](figures/construction.png)', '',
        '## Search recovery', '',
        md_table(medians, ['strategy', 'median_proposals_to_endpoint_cost', 'successful_runs', 'runs']), '',
        md_table(recovery, ['strategy', 'seed', 'first_reference_recipe', 'first_reference_cost', 'first_google_cost', 'proposals']), '',
        'Endpoint cost recovery counts the first proposal with S at or below the supplied endpoint.',
        'Exact recipe recovery also requires the inactive split-walk toggle. These are different stopping measures.',
        'The [saved lineages](lineages.json) record the actual edit sequence in each selected circuit.',
        'Recovery measures the endpoint. It permits a different rule order from the reference construction.',
        'All strategies share the supplied rules and the same measured development table.',
        'Each proposal applies one rule. Repeated candidates consume proposal slots.',
        'The model sees observed candidates and legal actions. It receives no reference path or audit outcomes.',
        'This finite positive control measures recovery of supplied implementations. It establishes no new arithmetic discovery.',
        'All strategies reached the same cost. Three seeds in this small grammar establish no reliable Claude advantage.',
        f'{costs["calls_after_reference_cost_hit"]} of six feedback calls occur after that run first reaches the reference cost.',
        'The construction measurements are shared setup cost. Search traces use cached results rather than new input draws.', '',
        '![Measured search trajectories](figures/autoresearch.png)', '',
        '## Independent qualification', '',
        f'Final audit status: {audit_status}. The target is epsilon <= 1e-5 per input.',
        'The final audit uses one million fresh inputs per frozen circuit, in predeclared disjoint chunks.',
        'Qualification uses the final cohort alone and adjusts for all frozen circuits.', '',
        md_table(audit_table, ['Q', 'T', 'S', 'failed_inputs', 'pairs', 'epsilon_upper_adjusted', 'qualified']), '',
        'Two selected recipe aliases emitted one distinct circuit. The final audit therefore tested one frozen gate stream.',
        'The final audit found two coordinate-failing inputs. One input also failed the phase check. Both streams failed on each input.',
        'The final audit found no dirty workspace or changed addend. The full outcome records retain all four failed shots.',
        'The upper bound treats inputs as independent draws and clusters their two streams. It qualifies the declared sampled-input distribution.',
        'Conditional gates give slightly different mean T values on development, final, and official inputs.',
        'The circuit and Q remain fixed across these evaluations.', '',
        official_text, '',
        'See the [official evidence](official/README.md). The official check supplies separate protocol evidence; the million-input cohort supplies the reported failure bound.', '',
        '## Mathematical claim and informative failure', '',
        'The [derivation](DERIVATION.md) states the correction-fusion identity, selector conditions, and measured-cleanup phase correction.',
        'Controlled comparisons hold the inherited approximation settings fixed.',
        'The walk-split prediction failed: 2,952 dispatches triggered zero splits. The largest workspace request was 1,289, below the 1,332 cap.',
        'The [activation diagnostic](diagnostics/walk-activation/README.md) reproduced the frozen operation hash.',
        'This rule receives zero measured contribution here. Further storage search needs an activation profile before another cost claim.', '',
        '## Model use', '',
        f'Model calls: {tokens["calls"]}; input tokens: {tokens["input_tokens"]}; output tokens: {tokens["output_tokens"]}.',
        f'Cached input: {tokens["cached_input_tokens"]}; reported reasoning: {tokens["reasoning_tokens"]}; unknown reasoning records: {tokens["unknown_reasoning_calls"]}.',
        f'Estimated provider list-price cost: ${tokens["provider_cost_estimate_usd"]:.4f}.',
        'Reasoning tokens are part of output. Provider dollar values are estimates, not subscription invoice charges.', '',
        '## Recorded work', '',
        f'Circuit emission: {costs["emission"]["wall_seconds"]:.2f} seconds; cold development evaluation: '
        f'{costs["development"]["wall_seconds"]:.2f} seconds.',
        f'Search execution: {costs["search_total_wall_seconds"]:.2f} seconds, including '
        f'{costs["model_wall_seconds_included_in_search"]:.2f} model seconds.',
        f'Independent long evaluation: {costs["independent_long"]["wall_seconds"]:.2f} seconds; '
        f'completed final audit chunks: {costs["independent_final"]["wall_seconds"]:.2f} seconds.',
        f'The separate official check took {costs["official_check"]["wall_seconds"]:.2f} seconds.',
        'The detailed profile keeps CPU time, failed emission attempts, model packets, and build records separate.',
        costs['setup_coverage'], '',
        'Reproduce: see docs/full-circuit-path.md. The snapshot retains sources, circuits, inputs, receipts, and the frozen configuration.',
    ]
    for cid, result in audit.get('rows', {}).items():
        findings.extend(['', f'Circuit `{cid}`: {result["failed_inputs"]}/{result["pairs"]} failed inputs; '
                         f'upper bound {result["epsilon_upper_adjusted"]:.8g}; qualified: {result["qualified"]}.'])
    (root / 'FINDINGS.md').write_text('\n'.join(findings) + '\n')
    content = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Full-circuit construction and recovery</title><style>
body{{margin:0;background:#faf9f6;color:#232b30;font:16px/1.55 system-ui,sans-serif}}main{{max-width:1150px;margin:auto;padding:48px 24px}}
h1{{font-size:38px;line-height:1.15}}h2{{margin-top:45px;font-size:26px}}.tag{{color:#12635f;font-weight:650;letter-spacing:.08em;text-transform:uppercase;font-size:12px}}
.lede{{max-width:850px;font-size:19px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.card{{padding:22px;background:#fff;border:1px solid #d6ddd9;border-radius:8px}}
.card strong{{display:block;font-size:28px;color:#12635f}}img{{width:100%;height:auto;margin:18px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #d6ddd9}}a{{color:#12635f}}.scroll{{overflow:auto}}code{{font-size:13px}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
</style><main><div class="tag">Measured · 256-bit point addition · supplied construction</div>
<h1>How many edits?<br>How many attempts to find them?</h1>
<p class="lede">The construction records each structural change in a real point-addition circuit. The searches try to recover its endpoint with the same rules.</p>
<p>Q is the number of referenced logical qubits. T is the average executed Toffoli count. S = Q × rounded T. Lower S is better.</p>
<div class="grid"><div class="card"><strong>{effective_edits} circuit changes</strong>Four rule applications; the carry-splitting toggle emits identical gates here.</div>
<div class="card"><strong>{construction[-1]['S']/1e9:.3f} billion</strong>Endpoint S on development inputs.</div>
<div class="card"><strong>{html.escape(audit_label)}</strong>{html.escape(audit_details or 'Final independent audit status.')}</div></div>
<h2>The search curve</h2><p>Lines show the median across three seeds. Shading shows their range. Every point uses a measured circuit cost.</p>
<img src="figures/autoresearch.svg" alt="Measured best development cost against proposals">
<details><summary>Inspect the first 20 proposals</summary><img src="figures/autoresearch-first20.svg" alt="First twenty proposals"></details>
<p>The search uses a shared measured table of nine recipes and {distinct_streams} distinct circuits. These are recovery runs in a supplied library. Model calls add measured cost.</p>
<h2>The supplied construction</h2><img src="figures/construction.svg" alt="Full circuit cost after each structural edit">
<div class="scroll">{html_table(table,['edit','change','Q','T','S','failed_inputs','pairs'])}</div>
<p>The backend replacement imports several changes together. The remaining comparisons hold the inherited approximation settings fixed.</p>
<h2>Attempts needed</h2><div class="scroll">{html_table(medians,['strategy','median_proposals_to_endpoint_cost','successful_runs','runs'])}</div>
<p>Each run allows 100 proposals. Endpoint cost recovery counts the first proposal that reaches the supplied cost. Exact recipe recovery also requires the inactive toggle.</p>
<details><summary>All nine runs</summary><div class="scroll">{html_table(recovery,['strategy','seed','first_reference_recipe','first_reference_cost','first_google_cost','proposals'])}</div></details>
<p>The <a href="lineages.json">saved edit sequences</a> show each selected circuit's parents. Searches may reach the endpoint through a different rule order.</p>
<h2>Eight mechanism controls</h2><div class="scroll">{html_table(controls,['measured_cleanup','correction_fusion','split_walk','Q','T','S','failed_inputs'])}</div>
<h2>Evidence and reproduction</h2><p>Development and final qualification stay separate. The final failure target is one in 100,000 inputs.</p>
<div class="scroll">{html_table(audit_table,['Q','T','S','failed_inputs','pairs','epsilon_upper_adjusted','qualified'])}</div>
<p>The frozen endpoint has two failing inputs in one million fresh inputs. Both fail coordinates; one also fails phase. All scratch and addend checks pass. The bound clusters two measurement streams per input.</p>
<p>{html.escape(official_text)} See the <a href="official/README.md">official evidence</a>. Conditional gates cause small changes in average T across input samples.</p>
<p>The original control has two known phase failures in a separate 32,768-input test. Its <a href="regressions/original-control/README.md">saved regressions</a> remain part of the evidence. The original has no reliability qualification here.</p>
<p>The <a href="DERIVATION.md">mathematical derivation</a> gives the correction and cleanup conditions. The <a href="diagnostics/walk-activation/README.md">walk diagnostic</a> shows 2,952 dispatches and zero splits. This explains the final neutral rule.</p>
<p>Cold development evaluation: {costs['development']['wall_seconds']:.2f} seconds. Search: {costs['search_total_wall_seconds']:.2f} seconds, including {costs['model_wall_seconds_included_in_search']:.2f} model seconds. Completed final audit chunks: {costs['independent_final']['wall_seconds']:.2f} seconds.</p>
<p><a href="FINDINGS.md">Findings</a> · <a href="construction.csv">Construction CSV</a> · <a href="recovery.csv">Recovery CSV</a> · <a href="mechanism-effects.csv">Conditional effects</a> · <a href="model-usage.json">Model usage</a> · <a href="config.json">Configuration</a></p>
<p>Model: {tokens['calls']} calls; {tokens['input_tokens']:,} input tokens; {tokens['output_tokens']:,} output tokens; {tokens['cached_input_tokens']:,} cached input tokens. Estimated provider list-price cost: ${tokens['provider_cost_estimate_usd']:.4f}; this is no subscription invoice charge.</p>
<p><a href="profiling.json">Full time and packet profile</a>. {costs['calls_after_reference_cost_hit']} of six feedback calls occurred after that run reached the reference cost. Earlier implementation builds have incomplete time records.</p>
</main></html>'''
    (root / 'dashboard.html').write_text(content)
    summary = {'construction_rule_applications': 4, 'effective_circuit_changes': effective_edits,
               'first_google_edit': first_cross, 'endpoint_S': construction[-1]['S'],
               'searches': recovery, 'model_usage': tokens, 'final_audit_status': audit_status,
               'recovery_medians': medians, 'final_audit_qualified': qualified,
               'independent_results': audit_table, 'official_check': official_observed,
               'development_recipes': len(oracle), 'development_distinct_circuits': distinct_streams}
    native.save(root / 'summary.json', summary)
    return summary


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def md_table(rows, columns):
    return '\n'.join(['| ' + ' | '.join(HEADERS.get(k, k) for k in columns) + ' |', '| ' + ' | '.join(['---'] * len(columns)) + ' |'] +
                     ['| ' + ' | '.join(display(row, k) for k in columns) + ' |' for row in rows])


def html_table(rows, columns):
    return '<table><thead><tr>' + ''.join(f'<th>{html.escape(HEADERS.get(k, k))}</th>' for k in columns) + '</tr></thead><tbody>' + \
        ''.join('<tr>' + ''.join('<td>' + html.escape(display(row, k)) + '</td>' for k in columns) + '</tr>' for row in rows) + '</tbody></table>'
