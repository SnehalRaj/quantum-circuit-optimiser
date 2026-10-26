#!/usr/bin/env python3
"""Classify note labels in the frozen public snapshot. Make no network calls."""
from pathlib import Path
import gzip
import json
import re
from freeze_snapshot import eligible

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    ("Quotient recomputation", "Frogdrop", r"frogdrop"),
    ("Packed Euclidean walk", "Froghop", r"froghop"),
    ("Cross-packed divsteps", "Lilypad", r"lilypad"),
    ("Shared cofactor frame", "SKY-COF", r"sky[ _-]?cof"),
    ("Recycled-history walk", "Skywalk", r"skywalk"),
    ("Coupled walk and replay", "Leapfrog / Spookyfrog", r"leapfrog|spookyfrog"),
    ("Recorded Euclidean replay", "Dialog / Jump-2 / PingPong", r"ping[ _-]?pong|trailmix|dialog[ _-]?gcd|jump[ _-]?2|\bTLM_"),
    ("Kaliski inversion", "Kaliski", r"kaliski"),
]
MANUAL_NOTES = {
    "a21ec672": ("Recorded Euclidean replay", "Dialog / Jump-2 / PingPong",
                 "The note continues the Q1133 route and names its TLM settings beyond the automatic prefix."),
    "aefedb12": ("Shared cofactor frame", "SKY-COF",
                 "The title adds a gate catalog to Q1082; the note identifies parent d0abb28f, whose title names SKY-COF CAP1082."),
    "a42ed949": ("Shared cofactor frame", "SKY-COF",
                 "The title adds a gate catalog to Q1095; the note identifies parent 8a57279a, whose title names SKY-COF CAP1095."),
}


def classify(row):
    text = row.get("note") or ""
    title = next((line for line in text.splitlines() if line.startswith("# ")), "")
    for prefix, (label, alias, evidence) in MANUAL_NOTES.items():
        if row["id"].startswith(prefix):
            return label, alias, "manual note review", evidence, title
    for part, evidence in [(title, "title"), (text[:1500], "intro")]:
        matches = []
        for label, alias, pattern in PATTERNS:
            match = re.search(pattern, part, re.I)
            if match:
                matches.append((match.start(), label, alias, match.group()))
        if matches:
            _, label, alias, matched = min(matches)
            return label, alias, evidence, matched, title
    return "Unclassified", "", "none", "", title


def main():
    doc = json.loads(gzip.decompress((ROOT / "data/public-submissions.json.gz").read_bytes()))
    snapshot=json.loads((ROOT/'data/frontier-summary.json').read_text())
    cutoff=snapshot.get('analysis_cutoff_utc','9999')
    rows = []
    for r in doc["submissions"]:
        if not eligible(r, cutoff):continue
        label, alias, evidence, matched, title = classify(r)
        metrics = r.get("officialMetrics") or {}
        if not all(isinstance(v, (int,float)) and not isinstance(v,bool) and v > 0
                   for v in (metrics.get("qubits"),metrics.get("toffoli"),r.get("officialScore"))):
            continue
        rows.append({"id":r["id"], "source":r["submissionCommitSha"],
                     "Q":metrics["qubits"], "T":metrics["toffoli"], "S":r["officialScore"], "published":r['scorePublishedAt'],
                     "family":label, "source_alias":alias, "evidence":evidence,
                     "matched_text":matched, "title":title})
    frontier, best = set(), float("inf")
    for q, t in sorted({(r["Q"],r["T"]) for r in rows}):
        if t < best:
            frontier.add((q,t))
            best = t
    representatives = {}
    for r in sorted(rows,key=lambda r:(r['published'],r['id'])):
        r["on_frontier"] = (r["Q"],r["T"]) in frontier
        if r["on_frontier"]:
            representatives.setdefault((r["Q"],r["T"]),r)
    summary = []
    for family in [p[0] for p in PATTERNS] + ["Unclassified"]:
        rr = [r for r in rows if r["family"] == family]
        ff = [r for r in representatives.values() if r["family"] == family]
        summary.append({"family":family, "eligible_rows":len(rr),
                        "frontier_coordinates":len(ff),
                        "minimum_S":min((r["S"] for r in rr),default=None),
                        "minimum_Q":min((r["Q"] for r in rr),default=None)})
    accepted_ids={r['id'] for r in doc['submissions'] if r['status']=='accepted'}
    own_ids={r['id'] for r in doc['submissions'] if r.get('solverUsername')=='SnehalRaj'}
    unique={}
    for r in rows:unique.setdefault((r['source'],r['Q'],r['T']),r)
    admission=[]
    for label, cohort in [('Accepted only',[r for r in rows if r['id'] in accepted_ids]),
                          ('Validated results',rows),('Deduplicated observations',list(unique.values()))]:
        cf=set();bt=float('inf')
        for q,t in sorted({(r['Q'],r['T']) for r in cohort}):
            if t<bt:cf.add((q,t));bt=t
        own={(r['Q'],r['T']) for r in cohort if r['id'] in own_ids and (r['Q'],r['T']) in cf}
        admission.append({'variant':label,'rows':len(cohort),'curve_coordinates':len(cf),'own_curve_points':len(own)})
    assert len(rows) == snapshot['eligible'] and len(frontier) == snapshot['frontier_coordinates']
    result = {
        "scope":"Note-reported family tags; title first, then first1500characters; first named match; documented manual note annotations; unclassified retained",
        "limitation":"Tags describe public notes. They are not an independent audit of every active constructor. Duplicate source submissions remain distinct in row counts.",
        "source":"data/public-submissions.json.gz", "analysis_cutoff_utc":cutoff, "patterns":PATTERNS, "manual_notes":MANUAL_NOTES,
        "rows":rows, "summary":summary, "admission_ablation":admission,
    }
    (ROOT / "data/family-census.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(summary,indent=2))


if __name__ == "__main__":
    main()
