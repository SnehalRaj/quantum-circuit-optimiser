"""Check retained files and recount the historical study without model execution."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parent
SHOTS = 9024
SEEDS = {f's{i}' for i in range(8)}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def raw(path):
    path = ROOT/path
    return gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes()


def rows(path):
    return [json.loads(line) for line in raw(path).splitlines()]


def verify_files():
    manifest = json.loads((ROOT/'PROVENANCE.json').read_text())
    for item in manifest['files']:
        path = ROOT/item['bundle_path']
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != item['sha256']:
            raise ValueError('Changed retained file: ' + item['bundle_path'])
        if sha(raw(item['bundle_path'])) != item['uncompressed_sha256']:
            raise ValueError('Changed uncompressed bytes: ' + item['bundle_path'])
    return len(manifest['files'])


def old_results():
    values = rows('records/v1_v6_evaluations.jsonl.gz')
    parents = {r['parent']: r['result'] for r in values if r['label'] == 'parent'}
    output = {}
    for label in sorted({r['label'] for r in values}-{'parent'}):
        group = [r for r in values if r['label'] == label and parents[r['parent']].get('valid')]
        valid = [r for r in group if r.get('result', {}).get('valid')]
        output[label] = {'outputs': len(group), 'parents': len({r['parent'] for r in group}),
            'parse': sum(bool(r.get('parses')) for r in group), 'apply': sum(bool(r.get('applies')) for r in group),
            'compile': sum(bool(r.get('result', {}).get('compiles')) for r in group), 'valid': len(valid),
            'improve': sum(r['result']['score'] < parents[r['parent']]['score'] for r in valid),
            'equal_parent_score': sum(r['result']['score'] == parents[r['parent']]['score'] for r in valid)}
    return output


def seeded_results(name):
    values = rows('records/seeded/'+name+'.jsonl.gz')
    parents = {r['parent']: r for r in values if r['label'] == 'parent' and 'seeds' in r}
    candidates = [r for r in values if r['label'] != 'parent']
    jobs = {}
    if name in ('v14', 'v14ctl'):
        jobs = {(j['parent'], item['idx']): item for j in rows('records/jobs/'+name+'_jobs.jsonl.gz') for item in j['items']}
    accepted = []
    checked = 0
    incomplete = 0
    for candidate in candidates:
        if candidate.get('status') != 'evaluated':
            continue
        parent = parents[candidate['parent']]
        c, p = candidate['seeds'], parent['seeds']
        if set(c) != SEEDS or set(p) != SEEDS or not all(v.get('ok') for v in [*c.values(), *p.values()]):
            incomplete += 1
            continue
        differences = [c[s]['score']-p[s]['score'] for s in sorted(SEEDS)]
        mean = statistics.mean(differences)
        se = statistics.stdev(differences)/len(differences)**.5
        if abs(candidate['d_score_mean']-mean) > .001 or abs(candidate['d_score_se']-se) > .001:
            raise ValueError('Paired score summary differs from the saved seeds')
        checked += 1
        rate = sum(v['classical'] for v in c.values())/(len(c)*SHOTS)
        if mean < -max(50000, 2*se) and rate <= .0015:
            record = {'parent': candidate['parent'], 'label': candidate['label'], 'idx': candidate['idx'],
                'score_change': mean, 'score_change_se': se, 'classical_rate': rate,
                'classical_mismatches': sum(v['classical'] for v in c.values()),
                'phase_garbage_batches': sum(v['phase'] for v in c.values()),
                'ancilla_garbage_batches': sum(v['ancilla'] for v in c.values()),
                'seeds': len(c), 'inputs_per_seed': SHOTS,
                'outcome_signature': sha(json.dumps([candidate['parent'],candidate['n_ops'],c],sort_keys=True).encode())}
            if jobs:
                text = jobs[candidate['parent'],candidate['idx']].get('text', '')
                blocks = re.findall(r'<<<<<<< SEARCH (\S+)\n(.*?)=======\n(.*?)>>>>>>> REPLACE', text, re.S)
                record['patch_sha256'] = sha(json.dumps(blocks,sort_keys=True).encode())
                record['patch_files'] = [block[0] for block in blocks]
                record['round_literal_changes'] = []
                for _, before, after in blocks:
                    pattern = r'tuned_window\("SUB4_PP_ROUNDS",\s*&SLOT,\s*(\d+)\)'
                    old, new = re.findall(pattern,before), re.findall(pattern,after)
                    if old and new and old != new:
                        record['round_literal_changes'].append({'before':old,'after':new})
            accepted.append(record)
    return {'outputs':len(candidates),'parents_started':len({r['parent'] for r in candidates}),
        'statuses':dict(sorted(Counter(r.get('status','unknown') for r in candidates).items())),
        'paired_summaries_recomputed':checked,'incomplete_paired_evaluations':incomplete,
        'passing_sample_rows':len(accepted),
        'parents_with_passing_sample':len({r['parent'] for r in accepted}),
        'distinct_patch_hashes':len({r['patch_sha256'] for r in accepted}) if jobs else None,
        'distinct_outcome_signatures':len({r['outcome_signature'] for r in accepted}),
        'accepted_samples':accepted}


def dataset_results():
    datasets = {name:rows('datasets/v14/'+name+'.jsonl.gz') for name in
                ('train','val','test37','mix/train','mix/val','ctl/train','ctl/val')}
    output = {'counts':{name:{'rows':len(value),'commits':len({r['id'] for r in value})}
                       for name,value in datasets.items()},'overlaps':{}}
    comparisons = [('train','test37'),('mix/train','test37'),('ctl/train','test37'),
                   ('mix/train','mix/val'),('ctl/train','ctl/val')]
    for a,b in comparisons:
        def keys(name,field):
            if field in ('id','parent'):
                return {r[field] for r in datasets[name]}
            if field == 'source_commits':
                return {r[k] for r in datasets[name] for k in ('id','parent')}
            return {r['messages'][1 if field=='prompt' else 2]['content'] for r in datasets[name]}
        output['overlaps'][a+'__'+b] = {field:len(keys(a,field)&keys(b,field))
                                      for field in ('id','parent','source_commits','prompt','target')}
    output['frontier_train_commits_already_in_control'] = len({r['id'] for r in datasets['train']} & {r['id'] for r in datasets['ctl/train']})
    return output


def summarize():
    count = verify_files()
    return {'schema':'finetuning-study-summary-v1',
        'study_commit':'0f762c895372db5292b4d12994a42d433651ac7d',
        'files_verified':count,
        'status':'Frozen interim evaluation; no new training, circuit execution, or independent reliability audit.',
        'v1_v6':old_results(),
        'seeded':{name:seeded_results(name) for name in ('v10a_pilot','v10b','v11_pilot','v13fix_partb','v13fix_pilot','v14','v14ctl','v14_stage0')},
        'datasets':dataset_results(),
        'historical_acceptance':{'paired_seeds':8,'inputs_per_seed':SHOTS,'score_change_below':-50000,
            'score_change_below_negative_standard_errors':2,'classical_rate_limit':.0015,
            'phase_and_ancilla_excluded_from_acceptance':True,'multiple_comparison_adjustment':False,
            'independent_joint_failure_qualification':False},
        'report_snapshot':{'v14_outputs':266,'v14_control_outputs':254,'control_distinct_edits_reported':6,
            'scope':'Numbers stated in REPORT_v7_v14.md; retain separately from raw-record counts.'}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=summarize()
    if args.output:
        args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'files_verified':result['files_verified'],
        'v14':{k:v for k,v in result['seeded']['v14'].items() if k!='accepted_samples'},
        'v14_control':{k:v for k,v in result['seeded']['v14ctl'].items() if k!='accepted_samples'}},indent=2))


if __name__ == '__main__':
    main()
