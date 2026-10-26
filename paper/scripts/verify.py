#!/usr/bin/env python3
"""Verify saved measurements and the compiled paper. Rerun no circuits."""
from pathlib import Path
import gzip
import hashlib
import json
import math
import statistics
import subprocess
import sys

import fitz
from scipy.stats import beta

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_figures import eligible, frontier
from build_submission_summary import collect as collect_submission_families

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def read(name):
    return json.loads((DATA / name).read_text())


def check_saved_hash(path, expected, *, decompress=False):
    """Check exact bytes or a recorded shareable-copy transformation."""
    path = Path(path)
    raw = path.read_bytes()
    actual = hashlib.sha256(gzip.decompress(raw) if decompress else raw).hexdigest()
    if actual == expected:
        return
    name = 'paper/' + path.relative_to(ROOT).as_posix()
    records = read('shareable-copies.json')['artifacts']
    entry = next((item for item in records if item['artifact'] == name), None)
    assert entry is not None, (name, 'No transformed-copy record')
    suffix = '_uncompressed_sha256' if decompress else '_sha256'
    assert entry['original' + suffix] == expected, (name, 'Original hash changed')
    assert entry['shareable' + suffix] == actual, (name, 'Shareable copy changed')


def verify_point_addition_search():
    study = read('point-addition-search.json')
    provenance = read('point-addition-search-provenance.json')
    assert hashlib.sha256((DATA / 'point-addition-search.json').read_bytes()).hexdigest() == provenance['results_sha256']
    assert study['status'] == 'complete' and study['protocol_sha256'] == provenance['protocol_sha256']
    assert len(study['audits']) == 17 and len(study['searches']) == 20
    assert len(study['seeds']) == 5
    audit_by_id = {r['recipe_id']: r for r in study['audits']}
    assert len({r['packet_sha256'] for r in study['audits']}) == 1
    for row in study['audits']:
        assert row['pairs'] == 16384 and row['streams'] == 2 and row['alpha'] == .05 / 20
        upper = beta.ppf(1-row['alpha'], row['failed_inputs']+1, row['pairs']-row['failed_inputs'])
        assert abs(upper-row['binomial_upper']) < 1e-12
        assert row['meets_bound'] == (upper <= .001)
        assert row['QT'] == row['Q'] * row['T']
    parent, raw = study['prepared_parent'], study['raw_parent']
    assert parent['meets_bound'] and raw['meets_bound']
    assert abs(study['shared_setup_gain_percent'] - 100*(1-parent['QT']/raw['QT'])) < 1e-12
    assert [r['recipes'] for r in study['grammar_capacity']] == [4, 8, 16]
    for row in study['grammar_capacity']:
        best = audit_by_id[row['best_recipe_id']]
        assert best['meets_bound'] and all(row[k] == best[k] for k in ['Q', 'T', 'QT'])
        assert abs(row['gain_percent'] - 100*(1-row['QT']/parent['QT'])) < 1e-12
    for run in study['searches']:
        assert len(run['slots']) == 8 and run['complete']
        assert run['baseline']['ops_sha256'] == parent['ops_sha256']
        assert len(run['calls']) == (4 if run['arm'] in ['claude', 'codex'] else 0)
        for checkpoint in run['checkpoints']:
            costs = [run['baseline']['QT']] + [s['QT'] for s in run['slots'][:checkpoint['proposals']]
                                              if s['status'] == 'passed']
            assert checkpoint['QT'] == min(costs)
            assert abs(checkpoint['gain_percent'] - 100*(1-min(costs)/run['baseline']['QT'])) < 1e-12
    for row in study['search_summaries']:
        gains = [next(c['gain_percent'] for c in run['checkpoints'] if c['proposals'] == row['proposals'])
                 for run in study['searches'] if run['arm'] == row['arm']]
        assert gains == row['seed_gains_percent']
        assert row['median_gain_percent'] == statistics.median(gains)
    for row in study['paired_differences']:
        differences = []
        for seed in study['seeds']:
            def gain(arm):
                run = next(r for r in study['searches'] if r['arm'] == arm and r['seed'] == seed)
                return next(c['gain_percent'] for c in run['checkpoints'] if c['proposals'] == row['proposals'])
            differences.append(gain(row['arm']) - gain(row['control']))
        assert differences == row['seed_differences_percentage_points']
        assert statistics.median(differences) == row['median_difference_percentage_points']
    assert len(study['finalists']) == 20
    for row in study['finalists']:
        selected = audit_by_id[row['selected_recipe_id']]
        assert row['qualified'] == selected['meets_bound']
        retained = selected if row['qualified'] else parent
        assert row['retained_recipe_id'] == retained['recipe_id']
        assert abs(row['audit_gain_percent'] - 100*(1-retained['QT']/parent['QT'])) < 1e-12
    return {'search_runs': 20, 'proposal_slots': 160, 'model_calls': 40,
            'joint_audits': 17, 'audit_input_pairs': 17*16384,
            'results_sha256': provenance['results_sha256']}


def verify_study_records():
    """Recount the packaged records independently of the paper aggregates."""
    search = ROOT / 'studies/point-addition-search-20261026'
    assert (search / 'results.json').read_bytes() == (DATA / 'point-addition-search.json').read_bytes()
    subprocess.run([sys.executable, str(search / 'audit_saved.py')],
                   check=True, capture_output=True, text=True)
    search_check = json.loads((search / 'shared-verification.json').read_text())
    assert search_check['recounted_stream_verdicts'] == 557056
    fine_output = ROOT / 'build/finetuning-recount.json'
    subprocess.run([sys.executable, str(ROOT / 'studies/historical/finetuning/recompute.py'),
                    '--output', str(fine_output)], check=True, capture_output=True, text=True)
    assert json.loads(fine_output.read_text()) == read('finetuning-summary.json')
    return {'point_addition_stream_verdicts': search_check['recounted_stream_verdicts'],
            'finetuning_aggregate_matches_recount': True}


def main():
    manifest = read("source-manifest.json")
    for r in manifest:
        check_saved_hash(ROOT / r['artifact'], r['sha256'])
    raw = gzip.decompress((DATA / "public-submissions.json.gz").read_bytes())
    summary = read("frontier-summary.json")
    check_saved_hash(DATA/'public-submissions.json.gz', summary['raw_sha256'], decompress=True)
    public = json.loads(raw)
    rows = [r for r in public["submissions"] if eligible(r)]
    curve = frontier(rows)
    own = [r for r in rows if r["solverUsername"] == "SnehalRaj"]
    assert (len(public["submissions"]), len(rows), len(curve), len(own)) == (2211, 1778, 215, 52)
    current = [r for r in own if (r["officialMetrics"]["qubits"], r["officialMetrics"]["toffoli"]) in curve]
    historical = []
    for r in own:
        q, t = r["officialMetrics"]["qubits"], r["officialMetrics"]["toffoli"]
        assert q * t == r["officialScore"]
        prior = [s for s in rows if s["scorePublishedAt"] < r["scorePublishedAt"]]
        if not any(s["officialMetrics"]["qubits"] <= q and s["officialMetrics"]["toffoli"] <= t for s in prior):
            historical.append(r)
    assert (len(current), len(historical)) == (46, 51)
    family_summary = read('own-submission-families.json')
    assert family_summary == collect_submission_families()
    records = family_summary['records']
    assert len(records) == 52
    assert len({r['commit'] for r in records if r['commit']}) == 52
    assert sum(r['current_frontier'] for r in records) == 46
    groups = {r['family']: r for r in family_summary['groups']}
    assert [(g['family'], g['max_T_reduction'], g['max_Q_reduction'])
            for g in family_summary['groups']] == [('Froghop', 271963, 6), ('Leapfrog', 63584, 22), ('SKY-COF', 1119, 6)]
    assert (groups['Froghop']['measured_commits'], groups['Froghop']['frontier_points']) == (45, 44)
    assert groups['Froghop']['Q_change_at_max_T'] == 20
    assert groups['Froghop']['T_change_at_max_Q'] == 1811710
    assert (groups['SKY-COF']['measured_commits'], groups['SKY-COF']['frontier_points']) == (6, 2)
    assert groups['SKY-COF']['T_change_at_max_Q'] == 85793
    assert groups['Leapfrog']['Q_change_at_max_T'] == -22
    historical_snapshot = read('frontier-summary-20261025T154600Z.json')
    historical_current_ids = {r['id'] for r in historical_snapshot['submissions'] if r['current_pareto']}
    assert len(historical_current_ids) == 14 and historical_snapshot['frontier_coordinates'] == 101
    expansion = read('frontier-expansion-20261026.json')
    prior_snapshot = read('frontier-summary-20261026T015112Z.json')
    prior_ids = {r['id'] for r in prior_snapshot['submissions'] if r['current_pareto']}
    assert len(prior_ids) == 28 and prior_snapshot['frontier_coordinates'] == 161
    assert prior_ids <= {r['id'] for r in current}
    assert {r['id'] for r in expansion['records']} == prior_ids - historical_current_ids
    public_by_id = {r['id']: r for r in rows}
    for entry in expansion['records']:
        source = public_by_id[entry['id']]
        assert hashlib.sha256(source['note'].encode()).hexdigest() == entry['note_sha256']
        assert source['submissionCommitSha'] == entry['source']
        assert source['officialMetrics'] == {'qubits': entry['Q'], 'toffoli': entry['T']}
        assert entry['reported_audit']['pairs'] == 20480
    assert sorted(r['reported_audit']['failed_inputs'] for r in expansion['records']) == [0]*13+[1]
    comparator = public_by_id['46ae05c5-afc3-4787-add8-1208f74ac582']
    assert comparator['submissionCommitSha'] == '6b0327d60e40068fcbed5fedcb7f66d926f12320'
    assert comparator['officialMetrics'] == {'qubits': 1234, 'toffoli': 742031}
    froghop = read('froghop-targets.json')['targets']
    assert {r['submission_id'] for r in froghop} == historical_current_ids
    assert sum(r['variant'] == 'one' for r in froghop) == 9
    assert sum(r['variant'] == 'double' for r in froghop) == 5
    assert sum(r['parent_policy'] == 'mfs' for r in froghop) == 6
    assert sum(r['parent_policy'] == 'mfsr' for r in froghop) == 8
    assert all(r['historical_policy'] == 'mfsur' for r in froghop)
    assert min(r["officialScore"] for r in own) == 1008123460
    fine = read('finetuning-summary.json')
    fine_source = read('finetuning-provenance.json')
    assert hashlib.sha256((DATA/'finetuning-summary.json').read_bytes()).hexdigest() == fine_source['summary_sha256']
    assert fine['study_commit'] == fine_source['source_commit']
    assert fine['seeded']['v14ctl']['parents_with_passing_sample'] == 3
    assert fine['seeded']['v14']['parents_with_passing_sample'] == 1
    assert fine['historical_acceptance']['classical_rate_limit'] == .0015
    assert fine['historical_acceptance']['phase_and_ancilla_excluded_from_acceptance']
    assert not fine['historical_acceptance']['independent_joint_failure_qualification']
    generated_arms = {name: r for name, r in fine['v1_v6'].items() if name != 'oracle'}
    assert all(r['improve'] == 0 for r in generated_arms.values())
    assert fine['v1_v6']['oracle']['improve'] == 47
    best_fine = min(fine['seeded']['v14ctl']['accepted_samples'], key=lambda r: r['score_change'])
    assert abs(best_fine['score_change'] + 2117532.5775000155) < 1e-6
    assert best_fine['classical_mismatches'] == 108
    assert best_fine['inputs_per_seed'] * best_fine['seeds'] == 72192
    assert best_fine['round_literal_changes'] == [{'after': ['698'], 'before': ['700']}]
    reliability = read("reliability-evidence.json")
    audits = reliability["independent_audits"]
    for r in audits:
        k, n = r["failed_inputs"], r["n"]
        u = float(beta.ppf(1 - r["alpha"], k + 1, n - k))
        assert abs(u - r["upper"]) < 1e-12, r["variant"]
        assert r["streams"] == 2
    current_audits = [r for r in audits if r["on_current_curve"]]
    assert {r["public_id"] for r in current_audits} == historical_current_ids
    assert len(current_audits) == 14
    assert all(r["n"] == 20480 and r["upper"] <= .001 for r in current_audits)
    assert min(r["failed_inputs"] for r in current_audits) == 0
    assert max(r["failed_inputs"] for r in current_audits) == 3
    common = [r for r in read("common-reliability.json")["results"] if r["status"] == "complete"]
    assert len(common) == 12
    assert len({r["counts"]["packet_sha256"] for r in common}) == 1
    for r in common:
        k, n = r["counts"]["failed_inputs"], r["pairs"]
        assert n == 32768
        assert abs(beta.ppf(1 - .05/16, k+1, n-k) - r["bounds"]["family_upper95"]) < 1e-12
    diag = reliability["cof_diagnostic_comparison"]
    assert len({r["label"] for r in diag}) == 1
    assert all(r["n"] == 262144 for r in diag)
    assert [r["failed_inputs"] for r in diag] == [57, 43, 34, 35]
    # The source JSON also includes one parent control and later Lilypad work.
    stats = reliability["screens_by_region"]
    selected_regions = {k: v for k, v in stats.items() if k.startswith(("cof-", "froghop-"))}
    assert sum(r["screened"] for r in selected_regions.values()) == 31
    assert sum(r["passed"] for r in selected_regions.values()) == 19
    assert sum(r["failed"] for r in selected_regions.values()) == 12
    strategy = read("strategy-evidence.json")
    for r in strategy["strategies"]:
        values = r["rows"]
        assert len(values) == 36
        assert all(x["pairs"] == 8192 for x in values)
        savings = [x["parent_T"] - x["candidate_T"] for x in values]
        percentages = [100*s/x["parent_T"] for s,x in zip(savings,values)]
        assert sum(s > 1e-9 for s in savings) == r["improved_sources"]
        assert abs(statistics.mean(savings)-r["mean_saving_T"]) < 1e-9
        assert abs(statistics.mean(percentages)-r["mean_percent_reduction"]) < 1e-10
    p6a = read("p6a-audit256.json")
    assert p6a["circuit"]["T"] == 1633537 and p6a["circuit"]["Q"] == 2050
    assert p6a["audit"]["failed_inputs"] == 0
    assert abs(beta.ppf(1-.05/32,1,20480)-p6a["audit"]["upper_bound"]) < 1e-12
    tex = (ROOT / "main.tex").read_text()
    assert '46 of 215' in tex and r'21.4\%' in tex
    assert '1,152 submissions' in tex and '1,778 validated results' in tex
    assert '26 October 2026' in tex and '24 October comparator' in tex
    assert "—" not in tex and "---" not in tex
    log = (ROOT / "build/main.log").read_text()
    assert "Overfull" not in log
    assert "undefined" not in log.lower()
    assert "! LaTeX Error" not in log
    grammar = read("grammar-ablation.json")
    assert grammar["status"] == "complete"
    assert grammar["model_calls"] == 0
    assert sum(a["proposal_slots"] for a in grammar["arms"]) == 2700
    for arm in grammar["arms"]:
        seed_values = []
        for seed in grammar["seeds"]:
            rr = [r for r in grammar["rows"] if r["arm"] == arm["arm"] and r["seed"] == seed]
            assert len(rr) == 3
            value = sum(r["weight"]*r["retained_S"]/r["starting_S"] for r in rr)/sum(r["weight"] for r in rr)
            seed_values.append(value)
            recorded = next(s for s in arm["seed_scores"] if s["seed"] == seed)
            assert abs(value-recorded["remaining"]) < 1e-12
        assert abs(statistics.median(seed_values)-arm["median_remaining"]) < 1e-12
        assert sum(arm["proposal_outcomes"].values()) == 540
    for r in grammar["rows"]:
        assert abs(beta.ppf(1-grammar["audit_alpha_per_member"],r["failed_inputs"]+1,r["pairs"]-r["failed_inputs"])-r["upper"]) < 1e-12
        assert r["eligible"] == (r["upper"] <= .001)
    timing = read("timing-study.json")
    assert timing["status"] == "complete" and len(timing["rows"]) == 3
    for r in timing["rows"]:
        assert r["status"] == "complete" and r["pairs"] == 100000 and r["streams"] == 2
        assert abs(beta.ppf(1-r["alpha"],r["failed_inputs"]+1,r["pairs"]-r["failed_inputs"])-r["upper_bound"]) < 1e-12
        check_saved_hash(ROOT/r['result_file'], r['result_sha256'])
    point_timing = read('point-addition-timing.json')
    assert point_timing['status'] == 'complete'
    assert [r['id'] for r in point_timing['rows']] == ['point_addition16', 'point_addition']
    for r in point_timing['rows']:
        assert r['pairs'] == 100000 and r['streams'] == 2
        check_saved_hash(ROOT/r['result_file'], r['result_sha256'])
        assert abs(beta.ppf(1-r['alpha'], r['failed_inputs']+1, r['pairs']-r['failed_inputs'])-r['upper_bound']) < 1e-12
        for key in ['construction_wall_seconds', 'evaluation_wall_seconds', 'evaluation_cpu_seconds']:
            assert r[key] > 0
    assert point_timing['rows'][1] == next(r for r in timing['rows'] if r['id'] == 'point_addition')
    small = point_timing['rows'][0]
    small_root = ROOT/'studies/point-addition-timing/small16'
    audit = json.loads((small_root/'audit.json').read_text())
    contract = json.loads((small_root/'contract.json').read_text())
    check_saved_hash(small_root/'contract.json', small['contract_sha256'])
    check_saved_hash(small_root/'audit.json', small['audit_sha256'])
    assert hashlib.sha256((small_root/'circuit.qops.zst').read_bytes()).hexdigest() == small['operation_sha256']
    assert audit['label'] == contract['label']
    assert (small['Q'], small['T'], small['threads']) == (214, 28051, 1)
    for stem, prefix in [('emission', 'construction'), ('evaluation', 'evaluation')]:
        process = json.loads((small_root/f'{stem}.process.json').read_text())
        for unit in ['wall_seconds', 'cpu_seconds']:
            assert small[f'{prefix}_{unit}'] == process[unit]
    outcomes_hash = hashlib.sha256()
    shots = 0
    failed = set()
    inputs = set()
    with gzip.open(small_root/'outcomes.jsonl.gz', 'rb') as stream:
        for line in stream:
            outcomes_hash.update(line)
            outcome = json.loads(line)
            inputs.add(outcome['idx'])
            shots += 1
            if any(outcome['failures'].values()):
                failed.add(outcome['idx'])
    assert outcomes_hash.hexdigest() == small['per_input_sha256']
    assert shots == 200000 and inputs == set(range(100000))
    assert len(failed) == small['failed_inputs'] == audit['failed_inputs']
    timing_table = (DATA/'timing-table.tex').read_text()
    assert 'division' not in timing_table.lower()
    assert '16-bit point addition' in timing_table and 'secp256k1 point addition' in timing_table
    model = read("model-pilot.json")
    assert model["executable_matrix_status"] == "complete" and len(model["arms"]) == 6
    assert model["status"] in ("complete", "complete_with_identifier_amendment")
    for arm in model["arms"]:
        assert arm["status"] == "complete"
        assert len(arm["seed_scores"]) == 3
        for seed in arm["seed_scores"]:
            rr=[r for r in model["selections"] if r["condition"]==arm["condition"] and r["arm"]==arm["arm"] and r["seed"]==seed["seed"]]
            assert len(rr)==3
            remaining=sum(r["weight"]*r["retained_S"]/r["starting_S"] for r in rr)/sum(r["weight"] for r in rr)
            assert abs(remaining-seed["remaining"])<1e-12
        assert abs(statistics.median(r["remaining"] for r in arm["seed_scores"])-arm["median_remaining"]) < 1e-12
        assert abs(100*(1-arm["median_remaining"])-arm["median_improvement_percent"]) < 1e-10
    assert len(model["selections"])==54
    for r in model["selections"]:
        upper=beta.ppf(1-model["audit_alpha_per_circuit"],r["audit_failed_inputs"]+1,r["audit_pairs"]-r["audit_failed_inputs"])
        assert abs(upper-r["audit_upper"])<1e-12
        assert r["qualified"]==(upper<=.001)
    families = read("family-census.json")
    assert sum(r["eligible_rows"] for r in families["summary"]) == 1778
    assert sum(r["frontier_coordinates"] for r in families["summary"]) == 215
    assert [(r["rows"],r["curve_coordinates"],r["own_curve_points"]) for r in families["admission_ablation"]] == [(660, 9, 0), (1778, 215, 46), (1730, 215, 46)]
    users=read('frontier-users.json')
    matched=read('matched-point-addition.json')
    assert matched['status']=='complete' and matched['runtime']=='Rust' and matched['model_calls']==0
    assert matched['epsilon']==.001 and matched['family_alpha']==.05 and matched['alpha_per_test']==.0125
    assert len(matched['rows'])==4
    for r in matched['rows']:
        saved=ROOT/r['receipt_file']
        check_saved_hash(saved, r['receipt_sha256'])
        q=json.loads(saved.read_text())
        assert (q['field_width'],q['qubits'],q['mean_toffoli'],q['failed_inputs'],q['pairs']) == (r['field_width'],r['Q'],r['T'],r['failed_inputs'],r['pairs'])
        assert q['streams']==r['streams']==2
        assert abs(beta.ppf(1-.0125,r['failed_inputs']+1,r['pairs']-r['failed_inputs'])-r['upper'])<1e-12
        assert r['upper']<=.001
    for width in [16,256]:
        rr={r['recipe']:r for r in matched['rows'] if r['field_width']==width}
        assert rr['baseline']['Q']-rr['free-s']['Q']==width
        assert rr['baseline']['T']==rr['free-s']['T']
        assert rr['baseline']['packet_sha256']==rr['free-s']['packet_sha256']
    assert sum(r['pairs']for r in matched['rows'])==232768
    small_rows = [r for r in matched['rows'] if r['field_width'] == 16]
    assert len(small_rows) == 2
    assert all(r['alpha'] == .0125 and r['pairs'] == 100000 for r in small_rows)
    small_table = (DATA / 'small-point-addition-table.tex').read_text()
    assert all(r['description'] in small_table and f"{r['Q']:,}" in small_table for r in small_rows)
    assert '3,592' not in small_table and '3,336' not in small_table
    assert r'\input{data/small-point-addition-table.tex}' not in tex
    assert r'\input{data/matched-point-addition-table.tex}' not in tex
    pipeline = (ROOT / 'figures/fig2_pipeline.svg').read_text()
    assert '16-bit point addition' in pipeline and 'Full secp256k1 point addition' in pipeline
    assert all(label in pipeline for label in ['SETUP', 'FAST LOOP', 'SUBMISSION PHASE'])
    assert 'GitHub commits and commit messages' in pipeline
    assert 'Point addition: 16 / 256 bits' not in pipeline
    assert 'The experiments evaluate the components separately.' in tex
    assert tex.count('0.001') == 1
    assert tex.index('0.001') > tex.index(r'\label{sec:approximation}')
    assert tex.index('0.001') < tex.index(r'\subsection{Search and submission pipeline}')
    assert not any(phrase in tex.lower() for phrase in ['original 14', 'fourteen additional', 'lowest qualified', 'do not establish', 'cannot prove'])
    results_text = tex.split(r'\section{ECDSA.fail leaderboard results}')[1].split(r'\section{Methods}')[0]
    assert all(handle not in results_text for handle in ['gnuchev', 'odinfree', 'gopikannappan', 'krishparadox', 'Champollion9012'])
    assert r'\code{' not in results_text
    assert 'The model comparisons use full 16-bit point addition.' in tex
    assert users['unique_coordinates']==215 and sum(r['curve_points'] for r in users['rows'])==215
    first={}
    for r in sorted(rows,key=lambda r:(r['scorePublishedAt'],r['id'])):
        qt=(r['officialMetrics']['qubits'],r['officialMetrics']['toffoli'])
        if qt in curve:first.setdefault(qt,r)
    for u in users['rows']:
        count=sum(r['solverUsername']==u['user'] for r in first.values())
        submitted=[r for r in public['submissions'] if r['solverUsername']==u['user'] and r['createdAt']<=summary['analysis_cutoff_utc']]
        commits={r['submissionCommitSha'] for r in submitted if r.get('submissionCommitSha')}
        assert count==u['curve_points'] and len(submitted)==u['public_submissions']
        assert len(commits)==u['unique_submission_commits']
        assert abs(100*count/len(submitted)-u['frontier_yield_percent'])<1e-12
        assert abs(100*count/len(commits)-u['distinct_commit_yield_percent'])<1e-12
    our_user=next(r for r in users['rows'] if r['user']=='SnehalRaj')
    assert our_user['public_submissions']==52 and abs(our_user['frontier_yield_percent']-100*46/52)<1e-12
    assert our_user['unique_submission_commits']==52 and abs(our_user['distinct_commit_yield_percent']-100*46/52)<1e-12
    unidentified = [r for r in public['submissions']
                    if r['solverUsername'] == 'SnehalRaj' and not r.get('submissionCommitSha')]
    assert not unidentified
    archived_raw=gzip.decompress((DATA/'public-submissions-140019.json.gz').read_bytes())
    check_saved_hash(DATA/'public-submissions-140019.json.gz', read('frontier-summary-140019.json')['raw_sha256'], decompress=True)
    check_saved_hash(DATA/'public-submissions-20261025T154600Z.json.gz', historical_snapshot['raw_sha256'], decompress=True)
    assert not any(word in tex.lower() for word in ["recruit", "assignment", "take-home", "three to four hours"])
    point_search = verify_point_addition_search()
    study_records = verify_study_records()
    pdf = ROOT / "LLM_Assisted_Quantum_Circuit_Optimisation.pdf"
    with fitz.open(pdf) as doc:
        assert 5 <= len(doc) <= 12
        page_words = []
        uris = []
        for page in doc:
            text = page.get_text()
            assert text.strip()
            assert "Study in progress" not in text and "Study running" not in text
            page_words.append(len(text.split()))
            uris += [x["uri"] for x in page.get_links() if "uri" in x]
            for block in page.get_text("blocks"):
                x0,y0,x1,y1 = block[:4]
                assert x0 >= -1 and y0 >= -1 and x1 <= page.rect.width+1 and y1 <= page.rect.height+1
        assert doc.metadata['title'] == 'LLM-Assisted Quantum Circuit Optimisation: A Systematic Study'
        for suffix in ["2609.09582v2", "2603.28846v2", "q-2018-06-18-74", "post/1903", "2610.23832v2"]:
            assert any(suffix in u for u in uris), suffix
        pages = len(doc)
    result = {"status":"passed", "scope":"Saved evidence, figures, arithmetic, bounds, links, and PDF structure; no circuit reruns",
              "public_rows":len(public["submissions"]), "eligible_rows":len(rows),
              "frontier_coordinates":len(curve), "own_current":len(current), "own_historical":len(historical),
              "own_public_submission_yield_percent":our_user['frontier_yield_percent'], "public_user_table_rows":len(users['rows']),
              "own_distinct_commit_yield_percent":our_user['distinct_commit_yield_percent'],
              "archive_audit_bounds_recomputed":len(audits)+len(common)+1,
              "grammar_endpoint_bounds_recomputed":len(grammar["rows"]),
              "timing_bounds_recomputed":3,
              "historical_division_grammar_slots":2700,
              "historical_division_model_arms":6,
              "historical_division_model_endpoint_bounds_recomputed":54,
              "historical_division_model_proposal_slots":1296,
              "point_addition_search":point_search,
              "packaged_study_recount":study_records,
              "timing_input_pairs":300000,
              "point_addition_timing_rows":2,
              "point_addition_timing_inputs":200000,
              "fast_point_addition_stream_verdicts_checked":shots,
              "matched_full_addition_audits":4,
              "matched_full_addition_inputs":232768,
              "matched_full_addition_stream_verdicts":465536,
              "paired_strategy_rows_recomputed":sum(len(r["rows"]) for r in strategy["strategies"]),
              "copied_source_hashes_checked":len(manifest),
              "finetuning_summary_sha256":fine_source["summary_sha256"],
              "finetuning_scope":"Frozen interim classical-only protocol; no independent joint qualification",
              "pdf_pages":pages,
              "pdf_words_per_page":page_words,"pdf_sha256":hashlib.sha256(pdf.read_bytes()).hexdigest()}
    (DATA / "verification.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
