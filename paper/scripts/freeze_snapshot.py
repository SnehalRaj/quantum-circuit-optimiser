#!/usr/bin/env python3
"""Freeze a supplied public response at an explicit publication cutoff."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import argparse
import csv
import gzip
import hashlib
import json
import math
import shutil

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def eligible(r,cutoff):
    m=r.get('officialMetrics') or {}
    status=r['status']=='accepted' or (r['status']=='rejected' and r.get('rejectionReason')=='score did not improve current best')
    return status and bool(r.get('scorePublishedAt')) and timestamp(r['scorePublishedAt'])<=timestamp(cutoff) and all(
        isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>0
        for v in [r.get('officialScore'),m.get('qubits'),m.get('toffoli')])


def curve(rows):
    result=[];best=math.inf
    for q,t in sorted({(r['officialMetrics']['qubits'],r['officialMetrics']['toffoli']) for r in rows}):
        if t<best:result.append((q,t));best=t
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('response');parser.add_argument('--cutoff',required=True);parser.add_argument('--captured-utc')
    args=parser.parse_args();source=Path(args.response);raw=source.read_bytes();doc=json.loads(raw)
    rows=[r for r in doc['submissions'] if eligible(r,args.cutoff)];front=curve(rows)
    own=[r for r in rows if r['solverUsername']=='SnehalRaj'];hist=[]
    for r in own:
        q,t=r['officialMetrics']['qubits'],r['officialMetrics']['toffoli']
        prior=[s for s in rows if s['scorePublishedAt']<r['scorePublishedAt']]
        new=not any(s['officialMetrics']['qubits']<=q and s['officialMetrics']['toffoli']<=t for s in prior)
        hist.append({'id':r['id'],'Q':q,'T':t,'S':r['officialScore'],'source':r['submissionCommitSha'],
                     'published':r['scorePublishedAt'],'historical_new_point':new,'current_pareto':(q,t)in front})
    first={}
    for r in sorted(rows,key=lambda r:(r['scorePublishedAt'],r['id'])):
        qt=(r['officialMetrics']['qubits'],r['officialMetrics']['toffoli'])
        if qt in front:first.setdefault(qt,r)
    users=Counter(r['solverUsername'] for r in first.values());verified=Counter(r['solverUsername'] for r in rows)
    all_entries=[r for r in doc['submissions'] if r.get('createdAt') and timestamp(r['createdAt'])<=timestamp(args.cutoff)]
    submitted=Counter(r['solverUsername'] for r in all_entries)
    commits={user:{r['submissionCommitSha'] for r in all_entries if r['solverUsername']==user and r.get('submissionCommitSha')} for user in users}
    user_rows=[]
    for user,count in sorted(users.items(),key=lambda x:(-x[1],-verified[x[0]],x[0])):
        qs=[q for (q,t),r in first.items() if r['solverUsername']==user]
        user_rows.append({'user':user,'curve_points':count,'share_percent':100*count/len(front),
                          'verified_rows':verified[user],'public_submissions':submitted[user],
                          'unique_submission_commits':len(commits[user]),
                          'frontier_yield_percent':100*count/submitted[user],
                          'distinct_commit_yield_percent':100*count/len(commits[user]) if commits[user] else None,
                          'minimum_Q':min(qs),'maximum_Q':max(qs)})
    old_summary=json.loads((DATA/'frontier-summary.json').read_text())
    old_cutoff=old_summary['analysis_cutoff_utc']
    archive_tag=timestamp(old_cutoff).strftime('%Y%m%dT%H%M%SZ')
    for filename in ['public-submissions.json.gz','frontier-summary.json','frontier-users.json','frontier-users.csv','family-census.json']:
        path=DATA/filename
        suffix=''.join(path.suffixes)
        archive=DATA/(path.name[:-len(suffix)]+'-'+archive_tag+suffix)
        if path.exists() and not archive.exists():shutil.copyfile(path,archive)
    best=min(rows,key=lambda r:r['officialScore'])
    summary={'captured_utc':args.captured_utc or datetime.fromtimestamp(source.stat().st_mtime,timezone.utc).isoformat(),
             'analysis_cutoff_utc':args.cutoff,'scope':'Fetched response filtered by public score publication timestamp',
             'raw_sha256':hashlib.sha256(raw).hexdigest(),'rows':len(doc['submissions']),'eligible':len(rows),
             'frontier_coordinates':len(front),'own_verified':len(own),
             'own_historical_pareto':sum(r['historical_new_point'] for r in hist),
             'own_current_pareto':sum(r['current_pareto'] for r in hist),
             'global_best':best['officialScore'],'global_metrics':best['officialMetrics'],
             'submissions':sorted(hist,key=lambda r:r['Q'])}
    (DATA/'public-submissions.json.gz').write_bytes(gzip.compress(raw,mtime=0))
    (DATA/'frontier-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (DATA/'frontier-users.json').write_text(json.dumps({'analysis_cutoff_utc':args.cutoff,
        'unique_coordinates':len(front),'allocation':'Each coordinate appears once; ties use earliest scorePublishedAt, then id.',
        'scope':'Coverage by first submitter; not algorithmic credit or reliability ranking.',
        'yield_denominator':'All public submission entries created by the cutoff, including failed and pending entries. Repeated commits count as separate submissions. Distinct commit counts are also preserved.',
        'source_raw_sha256':summary['raw_sha256'],'rows':user_rows},indent=2)+'\n')
    with (DATA/'frontier-users.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(user_rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(user_rows)
    print(json.dumps({k:v for k,v in summary.items() if k!='submissions'},indent=2))


if __name__=='__main__':main()
