"""A bounded grammar comparison with the existing saved-circuit evaluator."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil
from statistics import median
import subprocess
import time

from . import block_program, division, engine, search_space, study
from .arithmetic import designs


def remaining_cost(rows):
    """Retain every baseline, including endpoints that fail qualification."""
    total = sum(row['weight'] for row in rows)
    if not total:
        raise ValueError('The baseline denominator must be positive')
    return sum(row['weight'] * (min(1, row['S']/row['starting_S'])
               if row['eligible'] else 1) for row in rows)/total


def walk_requirements(bits, p):
    """Enumerate the finite denominator domain before candidate selection."""
    if not 2 < p < 1 << bits or p % 2 != 1 or bits > 16:
        raise ValueError('The finite walk guard supports at most sixteen bits')
    widths = [1] * (2*bits)
    witnesses = [1] * (2*bits)
    maximum = 0
    for x in range(1, p):
        u, v = p, x
        for t in range(2*bits):
            width = max(u, v).bit_length()
            if width > widths[t]:
                widths[t], witnesses[t] = width, x
            if t == 2*bits-1:
                if v:
                    raise ValueError('The plain walk bound failed')
                break
            if v:
                maximum = max(maximum, t+1)
            b = v & 1
            if b and u > v:
                u, v = v, u
            v = (v-b*u)//2
    return {'bits':bits, 'prime':p, 'widths':widths,
            'witnesses':witnesses, 'max_steps':maximum, 'denominators':p-1,
            'scope':'All denominators of this modulus; no claim at other widths.'}


def exact_guard(recipe, requirements):
    """A sufficient walk guard. The unchanged circuit checker checks the payload."""
    n, family, steps = recipe['bits'], recipe['family'], recipe['steps']
    if (n, recipe['prime']) != (requirements['bits'], requirements['prime']):
        return False
    if family not in ['inverse', 'replay', 'width']:
        return steps >= search_space.schedule(family, n)
    if steps < requirements['max_steps']:
        return False
    if family == 'width':
        widths = recipe.get('widths') or [designs.width_bound(n,t) for t in range(steps+1)]
        if any(w < requirements['widths'][min(t,2*n-1)] for t,w in enumerate(widths)):
            return False
        # u is odd. When comparison is enabled, v is also odd.
        # Dropping their common low bit preserves their strict order.
        if recipe.get('compare_bits',n) < n-1:
            return False
    return True


def assess(rows, reduction=.1, historical_remaining=.5130103828443335, expected_seeds=None):
    arms = {}
    for arm in ['A','B','C']:
        seeds = sorted({r['seed'] for r in rows if r['strategy']==arm})
        scores = [remaining_cost([r for r in rows if r['strategy']==arm and r['seed']==s])
                  for s in seeds]
        arms[arm] = {'seeds':seeds,'remaining_costs':scores,
                     'median_remaining':median(scores) if scores else None}
    controls = [historical_remaining] + [arms[a]['median_remaining'] for a in ['A','B']
                                        if arms[a]['median_remaining'] is not None]
    strongest = min(controls)
    baseline_checks = {}
    for baseline in sorted({r['baseline'] for r in rows}):
        values = {a:[r['S'] if r['eligible'] else r['starting_S'] for r in rows
                     if r['strategy']==a and r['baseline']==baseline] for a in arms}
        baseline_checks[baseline] = bool(values['C'] and
            median(values['C']) <= min(median(values[a]) for a in ['A','B'] if values[a]))
    crows = [r for r in rows if r['strategy']=='C']
    qualified = bool(crows) and all(r['eligible'] for r in crows)
    expected = sorted(expected_seeds) if expected_seeds is not None else arms['C']['seeds']
    baselines = {r['baseline'] for r in rows}
    complete = bool(expected) and all(arms[a]['seeds']==expected for a in arms)
    complete = complete and all(sum(r['strategy']==a and r['seed']==s and r['baseline']==b
                                   for r in rows)==1
                                for a in arms for s in expected for b in baselines)
    c = arms['C']['median_remaining']
    return {'arms':arms,'strongest_control_remaining':strongest,
            'target_remaining':(1-reduction)*strongest,'baseline_no_regression':baseline_checks,
            'all_C_qualified':qualified,'complete_matrix':complete,
            'relative_cost_reduction':1-c/strongest if c is not None else None,
            'target_met':bool(complete and qualified and all(baseline_checks.values()) and
                              c <= (1-reduction)*strongest)}


def _expired(deadline, reserve=0):
    return datetime.now(timezone.utc).timestamp()+reserve >= deadline


def _propose(root, state, rng, arm, trial, requirements):
    viable = [e for e in state['archive'].values() if not e['result']['failed_inputs']
              and e['result']['stage']=='long' and exact_guard(e['recipe'],requirements)]
    groups = {}
    for entry in viable:
        groups.setdefault(entry['recipe']['family'],[]).append(entry)
    group = groups[rng.choice(sorted(groups))]
    parent = rng.choice(group) if rng.random()<.35 else min(group,key=lambda e:e['result']['S'])
    r = copy.deepcopy(parent['recipe'])
    # Shared bridge proposals keep useful intermediate families reachable.
    bridges = [dict(family='replay',cleanup='measured'),dict(family='width',cleanup='measured'),
               dict(family='width',cleanup='measured',compare_bits=r['bits']-1),
               dict(family='width',cleanup='measured',widths=requirements['widths'])]
    if trial <= len(bridges):
        r = {**search_space.baseline_recipes(r['bits'])['width'], **bridges[trial-1]}
        return parent, r, 'shared_bridge'
    if arm=='C' and (trial < 13 or rng.random()<.4):
        if r['family'] not in ['inverse','replay','width']:
            r = {**search_space.baseline_recipes(r['bits'])['width'],'cleanup':'measured'}
        programs = block_program.seeds()
        if trial == 5:
            program = programs[3]
        elif trial == 6:
            program = programs[-1]
        elif rng.random()<.25:
            program = rng.choice(block_program.mutations(r.get('block_program',block_program.DEFAULT),True))
        else:
            program = rng.choice(programs)
        r['block_program'] = program
        return parent,r,'block_program'
    change = search_space.propose(root/'rules',r,rng)
    return parent,change['candidate'],change['history']['rule_id']


def search(root, arm, seed, requirements, deadline):
    root = Path(root); config = study.check(root); cal = config['calibration']['16']
    rid = f'16-{arm}-compact_json-{seed}'; out = root/'runs'/rid
    out.mkdir(parents=True,exist_ok=False); rng = random.Random(seed); start = time.monotonic()
    run = {'id':rid,'bits':16,'strategy':arm,'format':'compact_json','seed':seed,'status':'running',
           'source_sha256':config['source_sha256'],'contract_sha256':engine.digest(config),
           'baselines':{},'trials':[],'trace':[],'model_calls':[]}
    for bid,recipe in search_space.baseline_recipes(16).items():
        result = study.evaluate(root,recipe,cal['long_pairs'],'development/16',rid+'-'+bid+'-start','long')
        entry = {'id':engine.digest(recipe),'recipe':recipe,'result':result,'parent_id':None}
        run['baselines'][bid] = {'starting':entry,'selected':entry,'archive':{entry['id']:entry},
                                 'seen':[entry['id']],'operation_seen':[result['operation_sha256']]}
    study.save(out/'run.json',run)
    for trial in range(1,config['trials_per_baseline']+1):
        for bid,state in run['baselines'].items():
            if _expired(deadline,900) or time.monotonic()-start>=config['wall_seconds_per_run']:
                run['status']='budget_exhausted';study.save(out/'run.json',run);return run
            t0=time.monotonic(); record={'baseline':bid,'trial':trial,'status':'duplicate','generation_attempts':0}
            parent,recipe,rule = _propose(root,state,rng,arm,trial,requirements)
            cid=engine.digest(recipe);record['generation_attempts']=1
            if cid in state['seen']:
                record['proposal_wall_seconds']=time.monotonic()-t0
                run['trials'].append(record);study.append(out/'events.jsonl',record);continue
            state['seen'].append(cid)
            record.update(id=cid,parent_id=parent['id'],rule_id=rule,recipe=recipe)
            if not exact_guard(recipe,requirements):
                record['status']='walk_guard_rejected'
            else:
                try:
                    result=None
                    for stage,pairs in [('quick',cal['quick_pairs']),('long',cal['long_pairs'])]:
                        if not pairs:continue
                        result=study.evaluate(root,recipe,pairs,'development/16',f'{rid}-{bid}-{trial}-{stage}',stage,
                            timeout=max(.1,min(300,config['wall_seconds_per_run']-(time.monotonic()-start))))
                        if stage=='quick':
                            h=result['operation_sha256']
                            if h in state['operation_seen']:
                                record['status']='operation_duplicate';break
                            state['operation_seen'].append(h)
                            if result['failed_inputs']:break
                    record.update(S=result['S'],failed_inputs=result['failed_inputs'])
                    if record['status']!='operation_duplicate':
                        record['status']='failed' if result['failed_inputs'] else 'long_pass'
                        entry={'id':cid,'recipe':recipe,'result':result,'parent_id':parent['id']}
                        state['archive'][cid]=entry
                        if not result['failed_inputs'] and result['stage']=='long' and result['S']<state['selected']['result']['S']:
                            state['selected']=entry;record['status']='improvement'
                except (ValueError,subprocess.SubprocessError) as exc:
                    record.update(status='evaluation_error',error=str(exc))
            record['proposal_wall_seconds']=time.monotonic()-t0
            run['trials'].append(record);study.append(out/'events.jsonl',record)
        study.save(out/'run.json',run)
        if trial%10==0:
            print(json.dumps({'arm':arm,'seed':seed,'trial':trial,'best':
                  {b:s['selected']['result']['S'] for b,s in run['baselines'].items()}}),flush=True)
    run['status']='complete';run['wall_seconds']=time.monotonic()-start
    study.save(out/'run.json',run);return run


def freeze(root, seeds):
    root=Path(root);config=study.check(root);final=root/'final';runs=[]
    if sorted(seeds)!=sorted(config['seeds']) or sorted(seeds)!=sorted(config['grammar_comparison']['seeds']):
        raise ValueError('The frozen seed matrix changed')
    for arm in ['A','B','C']:
        for seed in seeds:
            rid=f'16-{"random" if arm=="A" else arm}-compact_json-{seed}'
            run=study.load(root/'runs'/rid/'run.json')
            if run['status']!='complete' or run['contract_sha256']!=engine.digest(config):
                raise ValueError('The declared comparison is incomplete')
            if set(run['baselines'])!=set(search_space.baseline_recipes(16)):
                raise ValueError('The baseline set changed')
            for bid in run['baselines']:
                if sum(t['baseline']==bid for t in run['trials'])!=config['trials_per_baseline']:
                    raise ValueError('Proposal accounting is incomplete')
                state=run['baselines'][bid]
                if state['starting']['recipe']!=search_space.baseline_recipes(16)[bid]:
                    raise ValueError('The starting circuit changed')
                if state['selected']!=state['archive'].get(state['selected']['id']):
                    raise ValueError('The selected archive entry changed')
            if run['source_sha256']!=config['source_sha256']:
                raise ValueError('The search source changed')
            runs.append((arm,run))
    final.mkdir(exist_ok=False);circuits={};selections=[]
    for arm,run in runs:
        for bid,state in run['baselines'].items():
            refs={}
            for kind in ['starting','selected']:
                entry=state[kind];cid=entry['id'];refs[kind]=cid
                if cid!=engine.digest(entry['recipe']) or entry['result']['recipe']!=entry['recipe']:
                    raise ValueError('The endpoint recipe identity changed')
                if entry['result']['stage']!='long' or entry['result']['failed_inputs']:
                    raise ValueError('A frozen endpoint needs a long pass')
                if cid not in circuits:
                    target=final/'circuits'/cid
                    shutil.copytree(root/entry['result']['circuit_directory'],target)
                    circuits[cid]={'recipe':entry['recipe'],'operation_sha256':entry['result']['operation_sha256'],
                        'files':{str(p.relative_to(final)):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in target.rglob('*') if p.is_file()}}
            selections.append({'run':run['id'],'strategy':arm,'format':'compact_json','seed':run['seed'],
                'bits':16,'baseline':bid,'weight':config['calibration']['16']['weights'][bid],**refs})
    manifest={'contract_sha256':engine.digest(config),'source_sha256':config['source_sha256'],
              'circuits':circuits,'selections':selections,'pairs':config['audit_pairs'],
              'epsilon':config['epsilon'],'alpha':config['alpha'],'comparison_size':len(circuits),'controls':[]}
    study.save(final/'manifest.json',manifest,True)
    (final/'manifest.sha256').write_text(engine.digest(manifest)+'\n')
    return manifest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('root');parser.add_argument('--reference',required=True)
    parser.add_argument('--deadline',required=True);parser.add_argument('--seeds',nargs='+',type=int,default=[11,29,47,101,103,107])
    args=parser.parse_args();root=Path(args.root)
    deadline=datetime.fromisoformat(args.deadline.replace('Z','+00:00')).timestamp()
    if not root.exists():
        config=study.initialise(root,bits=[16],trials=100,seeds=args.seeds,provider='offline',strategies=['random'],
                                format_arms=[],wall_seconds=900,max_model_calls=0)
        reference=study.load(Path(args.reference)/'study.json')
        config['calibration']={'16':reference['calibration']['16']}
        config['grammar_comparison']={'arms':{'A':'unchanged random policy and grammar',
             'B':'diverse novelty policy, finite walk guard, old grammar',
             'C':'same policy and guard, editable block programs'},'seeds':args.seeds,
             'reference':str(args.reference),'deadline_utc':args.deadline,'paid_provider_calls':0}
        study.save(root/'study.json',config)
        (root/'contract.sha256').write_text(engine.digest(config)+'\n')
    config=study.check(root)
    if sorted(args.seeds)!=sorted(config['seeds']) or args.deadline!=config['grammar_comparison']['deadline_utc']:
        raise ValueError('Resume must preserve the frozen seed matrix and deadline')
    requirements=walk_requirements(16,65521);study.save(root/'walk-requirements.json',requirements)
    for seed in args.seeds:
        for arm in ['A','B','C']:
            rid=f'16-{"random" if arm=="A" else arm}-compact_json-{seed}'
            if (root/'runs'/rid/'run.json').exists():
                if study.load(root/'runs'/rid/'run.json')['status']=='complete':continue
                raise ValueError('An incomplete run needs a new recorded epoch')
            if _expired(deadline,1200):
                print('Checkpoint: reserve the remaining time for validation',flush=True);return
            if arm=='A':study.search(root,16,'random',seed)
            else:search(root,arm,seed,requirements,deadline)
            print(json.dumps({'complete':rid}),flush=True)
    freeze(root,args.seeds)
    result=study.audit(root)
    summary=assess(result['rows'],expected_seeds=config['seeds']);study.save(root/'assessment.json',summary,True)
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
