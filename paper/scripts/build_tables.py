#!/usr/bin/env python3
"""Build tables only from completed study records."""
from pathlib import Path
import json
import build_submission_summary

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
END = chr(92) * 2


def write(name, columns, heading, rows):
    text = ["\\begin{tabular}{"+columns+"}","\\toprule",heading+" "+END,"\\midrule"]
    text += [" & ".join(row)+" "+END for row in rows]
    text += ["\\bottomrule","\\end{tabular}"]
    (DATA / name).write_text("\n".join(text)+"\n")


def main():
    build_submission_summary.main()
    point_search = DATA / 'point-addition-search.json'
    if point_search.exists():
        study = json.loads(point_search.read_text())
        assert study['status'] == 'complete'
        parent = study['prepared_parent']
        rows = [['Parent', 'Common preparation', '1', f"{parent['Q']:,}",
                 f"{parent['T']:,.1f}", f"{parent['QT']/1e6:.3f}", r'0.00\%']]
        descriptions = {'G0': 'Cleanup', 'G1': '+ register release', 'G2': '+ combined products'}
        for row in study['grammar_capacity']:
            rows.append([row['grammar'], descriptions[row['grammar']], str(row['recipes']),
                         f"{row['Q']:,}", f"{row['T']:,.1f}", f"{row['QT']/1e6:.3f}",
                         f"{row['gain_percent']:.2f}" + r'\%'])
        write('point-addition-grammar-table.tex', 'llrrrrr',
              r'Grammar & Available edits & Circuits & Q & Mean T & $QT/10^6$ & Gain', rows)
        rows = []
        for arm, name in [('random', 'Random'), ('evolution', 'Evolution'),
                          ('claude', 'Claude Opus 5.5'), ('codex', 'GPT 6.1 Sol')]:
            summaries = {r['proposals']: r for r in study['search_summaries'] if r['arm'] == arm}
            calls = [c for run in study['searches'] if run['arm'] == arm for c in run['calls']]
            successful = sum(c.get('success') is True and not c.get('error') for c in calls)
            minimum = min(r['QT'] for r in study['audits'])
            best_recipes = {r['recipe_id'] for r in study['audits'] if r['QT'] == minimum}
            hits = [sum(next(c['recipe_id'] for c in run['checkpoints'] if c['proposals'] == n)
                        in best_recipes for run in study['searches'] if run['arm'] == arm)
                    for n in [4, 8]]
            rows.append([name] + [f"{summaries[n]['median_gain_percent']:.2f}" + r'\%'
                                 for n in [1, 2, 4, 8]] + [f'{count}/5' for count in hits]
                        + [f'{successful}/{len(calls)}'])
        write('point-addition-model-table.tex', 'lrrrrrrr',
              r'Proposer & 1 proposal & 2 proposals & 4 proposals & 8 proposals & Best by 4 & Best by 8 & Calls', rows)
    family_file=DATA/"family-census.json"
    if family_file.exists():
        d=json.loads(family_file.read_text())
        write("admission-table.tex","lrrr",r"Selection rule & Rows & Curve points & Our points",
              [[r['variant'].replace('Deduplicated observations','Deduplicated'),str(r['rows']),str(r['curve_coordinates']),str(r['own_curve_points'])] for r in d['admission_ablation']])
    p=DATA/"grammar-ablation.json"
    if p.exists():
        d=json.loads(p.read_text())
        if d.get("status")=="complete":
            rows=[]
            for a in d["arms"]:
                mark=r"$\star$" if a["arm"] in d["best_arms"] else ""
                rows.append([a["arm"]+mark,"Two" if a["shape"] else "Fixed",
                             "Baseline/direct" if a["direct"] else "Baseline",
                             "Yes" if a["guard"] else "No",f"{a['median_remaining']:.6f}",
                             f"{a['median_improvement_percent']:.3f}\\%",
                             f"{a['qualified_endpoints']}/{a['total_endpoints']}"])
            write("grammar-table.tex","llllrrr",r"Version & Payload forms & Constant lowering & Guard & Remaining $R$ & Gain & Qualified",rows)
    p=DATA/"point-addition-timing.json"
    if p.exists():
        d=json.loads(p.read_text())
        if d.get("status")=="complete":
            rows=[]
            labels={"point_addition16":"16-bit point addition","point_addition":"secp256k1 point addition"}
            for r in d["rows"]:
                if r["status"]!="complete":continue
                label=labels.get(r['id'],r['name'])
                evaluator="Rust"
                rows.append([label,f"{r['Q']:,}",f"{r['T']:,.0f}",evaluator,
                             str(r['threads']),f"{r['evaluation_wall_seconds']:.2f}" + r"\,s"])
            write("timing-table.tex","lrrlrr",r"Environment & Q & Mean T & Evaluator & Threads & Time",rows)
    p=DATA/"model-pilot.json"
    if p.exists():
        d=json.loads(p.read_text())
        if d.get("executable_matrix_status")=="complete":
            rows=[]
            for condition,label in [("guarded-seeds-v1","Full path"),("guarded-cleanup-only-v1","Cleanup only")]:
                for provider,name in [("offline","Evolution"),("claude","Claude Opus 5.5"),("codex","GPT 6.1 Sol")]:
                    a=next(r for r in d["arms"] if r["condition"]==condition and r["provider"]==provider)
                    calls=a['feedback_calls']
                    if isinstance(calls,dict):calls=f"{calls['completed']}/{calls['attempted']}"
                    rows.append([label,name,f"{a['median_remaining']:.6f}",f"{a['median_improvement_percent']:.3f}\\%",
                                 f"{a['qualified_endpoints']}/{a['total_endpoints']}",str(calls)])
            write("model-table.tex","llrrrr",r"Shared construction & Proposer & Remaining $R$ & Gain & Qualified & Feedback calls",rows)
    p=DATA/"matched-point-addition.json"
    if p.exists():
        d=json.loads(p.read_text())
        if d.get('status')=='complete':
            rows=[[str(r['field_width']),r['description'],f"{r['Q']:,}",f"{r['T']:,.0f}",
                   f"{r['failed_inputs']}/{r['pairs']:,}",f"{r['upper']:.8f}"] for r in d['rows']]
            write('matched-point-addition-table.tex','rlrrrr',
                  r'Bits & Matched Kaliski profile & Q & Mean T & Failed inputs & Upper bound',rows)
            small_rows = [[r['description'], f"{r['Q']:,}", f"{r['T']:,.0f}",
                           f"{r['failed_inputs']}/{r['pairs']:,}", f"{r['upper']:.8f}"]
                          for r in d['rows'] if r['field_width'] == 16]
            write('small-point-addition-table.tex', 'lrrrr',
                  r'16-bit profile & Q & Mean T & Failed inputs & Upper bound', small_rows)


if __name__=="__main__":main()
