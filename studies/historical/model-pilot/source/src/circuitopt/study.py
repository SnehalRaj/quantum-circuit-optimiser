"""Bounded search studies with saved gates, calibration, costs, and frozen audits."""
import copy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import secrets
import shutil
import subprocess
import sys
import time
from .research_policy import DEFAULT_EPSILON
from . import division,engine,packets,providers,rules,search_space,study_policy


def save(path,value,exclusive=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if exclusive and path.exists():raise FileExistsError(path)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def load(path):return json.loads(Path(path).read_text())


def append(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as f:f.write(engine.canonical(value)+'\n')


def source_hashes():
    root=Path(__file__).parent
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.py'))}


def check(root):
    c=load(Path(root)/'study.json')
    if c['source_sha256']!=source_hashes():raise ValueError('Study implementation changed; use its recorded source version')
    if c['rules_sha256']!=engine.digest(rules.cards(Path(root)/'rules')):raise ValueError('Frozen study rules changed')
    sealed=Path(root)/'contract.sha256'
    if sealed.exists() and sealed.read_text().strip()!=engine.digest(c):raise ValueError('Frozen study contract changed')
    return c


def initialise(root,bits=None,trials=100,seeds=None,provider='codex',model=None,
               calibration_size=64,audit_pairs=16384,epsilon=DEFAULT_EPSILON,strategies=None,format_arms=None,
               review_rounds=None,max_model_calls=2,wall_seconds=900,reasoning_effort='medium',
               model_timeout=120,active_rules=None,grammar='recipes-v1',proposal_policy='historical-v1'):
    root=Path(root)
    widths=bits or [5,16];seeds=seeds or [11,29,47]
    if not widths or len(set(widths))!=len(widths) or any(b not in [5,16] for b in widths):raise ValueError('Pilot widths are 5 and 16')
    if type(trials) is not int or trials<1 or calibration_size<8 or calibration_size%2 or audit_pairs<1:raise ValueError('Invalid study budget')
    if len(set(seeds))!=len(seeds) or not 0<epsilon<1:raise ValueError('Invalid seeds or failure target')
    strategies=strategies if strategies is not None else ['random','evolution']+(['llm'] if provider!='offline' else [])
    format_arms=format_arms if format_arms is not None else (['text','columnar_json'] if 'llm' in strategies and 5 in widths else [])
    if not strategies or len(set(strategies))!=len(strategies) or not set(strategies)<={'random','evolution','llm'}:raise ValueError('Invalid strategy matrix')
    if len(set(format_arms))!=len(format_arms) or not set(format_arms)<={'text','columnar_json'}:raise ValueError('Invalid format matrix')
    from . import study_grammar
    if grammar not in study_grammar.GRAMMARS:raise ValueError('Unknown study grammar')
    if proposal_policy not in study_policy.POLICIES:raise ValueError('Unknown proposal policy')
    if proposal_policy=='guarded-seeds-v1' and grammar!='blocks-v1':raise ValueError('Guarded seeds require the block grammar')
    if active_rules is None:active_rules=search_space.ACTIVE_RULES+(['R21'] if grammar=='blocks-v1' else [])
    if ('R21' in active_rules)!=(grammar=='blocks-v1'):raise ValueError('The block rule requires the block grammar')
    root.mkdir(parents=True,exist_ok=False);rules.initialise(root/'rules')
    if grammar=='blocks-v1':study_grammar.install(root/'rules')
    config={'schema':1,'purpose':'modular_division_search_pilot','bits':widths,'trials_per_baseline':trials,
      'seeds':seeds,'provider':provider,'model':model,'reasoning_effort':reasoning_effort,
      'strategies':strategies,'format_arms':format_arms,
      'max_model_calls_per_run':max_model_calls,'review_rounds':[5,25] if review_rounds is None else review_rounds,
      'wall_seconds_per_run':wall_seconds,'model_timeout':model_timeout,
      'calibration_size':calibration_size,'audit_pairs':audit_pairs,'epsilon':epsilon,'alpha':.05,
      'grammar':grammar,'proposal_policy':proposal_policy,'active_rules':active_rules,'source_sha256':source_hashes(),
      'rules_sha256':engine.digest(rules.cards(root/'rules')),'calibration':{},
      'claim_limit':f'Pilot modular division. Complete 256-bit point addition requires separate evidence at epsilon {DEFAULT_EPSILON:g}.',
      'budget_comparison':'Matched proposal caps and evaluator settings. Record actual model/evaluator costs; equal money is not established.'}
    save(root/'study.json',config,True)
    save(root/'progress.json',{'runs':[],'stage':'initialised'},True)
    if proposal_policy=='guarded-seeds-v1':
        for n in widths:save(root/f'policy/walk-{n}.json',study_policy.requirements(n,search_space.PRIMES[n]),True)
    versions={}
    for name in ['scipy','matplotlib','numpy','tiktoken']:
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    save(root/'environment.json',{'python':sys.version,'packages':versions,'logical_cpus':os.cpu_count(),
        'evaluation_concurrency':1,'source_sha256':config['source_sha256']},True)
    (root/'hypotheses.md').write_text('''# Pre-run questions and predictions

Question: Does measured cleanup lower T while preserving the boundary map?
Hypothesis: Remove one Toffoli per unconditional temporary AND cleanup. Preserve Q under the same allocation.
Reason: The conditioned CZ removes the measurement sign.
Invariant: The temporary wire equals the AND of retained, unchanged controls.
Cheapest disproof: A phase, output, or scratch counterexample.
Acceptance: The controlled compiled pair matches the predicted delta and passes its declared independent test.

Question: Does model feedback improve discovery under matched proposal caps?
Hypothesis: Diagnostic repairs improve the gain curve or final independently tested reward.
Cheapest disproof: Repeated controls match or exceed those gains after recorded costs.
Acceptance: Describe the observed effect across all baselines and seeds. A null result is valid.

Question: Does the quick screen save work without losing useful candidates?
Hypothesis: The chosen fitting policy retains at least 90% of useful held-out candidates.
Cheapest disproof: Held-out false rejections or cascade overhead above direct long evaluation.

Question: Does a smaller model context improve token efficiency and search performance?
Hypothesis: Equivalent structural encodings lower reported tokens without losing useful edits or final gains.
Cheapest disproof: Token savings disappear in wrapper overhead, or proposal quality and circuit gains fall.

Keep observations separate from these predictions. Preserve failed hypotheses and consumed audits.
''')
    return config


def planned_runs(config):
    result={f'{b}-{s}-compact_json-{k}' for b in config['bits'] for s in config['strategies'] for k in config['seeds']}
    if 5 in config['bits'] and 'llm' in config['strategies']:
        result.update(f'5-llm-{f}-{k}' for f in config['format_arms'] for k in config['seeds'])
    return result


def evaluate(root,recipe,pairs,label,key,stage,timeout=300,circuit_directory=None):
    root=Path(root);target=root/'evaluations'/key;target.mkdir(parents=True,exist_ok=False)
    circuit=Path(circuit_directory) if circuit_directory else root/'circuits'/engine.digest(recipe)
    request={'recipe':recipe,'circuit':str(circuit.resolve()),'pairs':pairs,'label':label,
             'inputs':str((target/'inputs.jsonl.gz').resolve())}
    started=time.monotonic();env={k:v for k,v in os.environ.items() if k in ['PATH','TMPDIR','LANG','SYSTEMROOT']}
    env['PYTHONPATH']=str(Path(__file__).parent.parent)
    try:
        proc=subprocess.run([sys.executable,'-m','circuitopt.worker'],input=engine.canonical(request),
                            capture_output=True,text=True,env=env,timeout=timeout)
    except subprocess.SubprocessError:
        append(root/'costs.jsonl',{'kind':'evaluation','key':key,'stage':stage,'status':'timeout',
            'pairs_requested':pairs,'wall_seconds':time.monotonic()-started,'cpu_seconds':None})
        raise
    (target/'stderr.txt').write_text(proc.stderr)
    if proc.returncode:
        append(root/'costs.jsonl',{'kind':'evaluation','key':key,'stage':stage,'status':'failed',
            'pairs_requested':pairs,'wall_seconds':time.monotonic()-started,'cpu_seconds':None})
        raise ValueError('Circuit worker failed: '+proc.stderr[-500:])
    r=json.loads(proc.stdout)
    r.update(S=r['Q']*math.floor(r['T']+.5),stage=stage,recipe=recipe,label=label,
             wall_seconds=time.monotonic()-started,evaluation_directory=str(target.relative_to(root)),
             circuit_directory=str(circuit.relative_to(root)),inputs_file=str((target/'inputs.jsonl.gz').relative_to(root)))
    save(target/'receipt.json',r,True)
    append(root/'costs.jsonl',{'kind':'evaluation','key':key,'stage':stage,'status':'completed',
        'pairs':pairs,'wall_seconds':r['wall_seconds'],'cpu_seconds':r['cpu_seconds'],'peak_rss_bytes':r['peak_rss_bytes'],
        'Q':r['Q'],'T':r['T'],'S':r['S'],'failed_inputs':r['failed_inputs']})
    return r


def screen_metrics(rows):
    useful=sum(r['useful'] for r in rows);promoted=sum(r['quick_pass'] for r in rows)
    return {'candidates':len(rows),'promoted':promoted,'useful':useful,
      'useful_recall':sum(r['quick_pass'] and r['useful'] for r in rows)/useful if useful else None,
      'promotion_precision':sum(r['quick_pass'] and r['useful'] for r in rows)/promoted if promoted else None,
      'long_pass_precision':sum(r['quick_pass'] and r['long_pass'] for r in rows)/promoted if promoted else None,
      'false_rejections':sum(not r['quick_pass'] and r['useful'] for r in rows),
      'cascade_seconds':sum(r['quick_seconds']+(r['long_seconds'] if r['quick_pass'] else 0) for r in rows),
      'direct_long_seconds':sum(r['long_seconds'] for r in rows)}


def calibrate(root):
    root=Path(root);c=check(root)
    if c['calibration']:raise FileExistsError('Calibration already recorded')
    report={}
    for bits in c['bits']:
        rng=random.Random(1700+bits);bases=search_space.baseline_recipes(bits)
        long_pairs=512 if bits==5 else 4096;quick_sizes=[8,32,128] if bits==5 else [32,128,512]
        originals={bid:evaluate(root,r,long_pairs,f'calibration/{bits}/long',f'cal-{bits}-base-{bid}','long') for bid,r in bases.items()}
        costs={b:r['S'] for b,r in originals.items()}
        low,high=min(costs.values()),max(costs.values())
        weights={bid:100**(math.log(high/s)/math.log(high/low)) if high>low else 1 for bid,s in costs.items()}
        pool={engine.digest(r):{'recipe':r,'origin':'baseline'} for r in bases.values()}
        parents=list(bases.values())
        for _ in range(c['calibration_size']*200):
            if len(pool)>=c['calibration_size']:break
            parent=rng.choice(parents);change=search_space.propose(root/'rules',parent,rng,c['active_rules']);recipe=change['candidate'];cid=engine.digest(recipe)
            if cid not in pool:pool[cid]={'recipe':recipe,'history':change['history']};parents.append(recipe)
        if len(pool)<c['calibration_size']:raise ValueError('Calibration pool did not reach its declared size')
        items=list(pool.items());rng.shuffle(items);half=len(items)//2
        all_rows={n:[] for n in quick_sizes}
        for index,(cid,item) in enumerate(items):
            recipe=item['recipe'];item['id']=cid;item['split']='fit' if index<half else 'held_out'
            compiled=time.monotonic();cpu_start=time.process_time()
            division.export_circuit(recipe,root/'circuits'/cid)
            append(root/'costs.jsonl',{'kind':'compilation','stage':'calibration','id':cid,
                'wall_seconds':time.monotonic()-compiled,'cpu_seconds':time.process_time()-cpu_start})
            long=evaluate(root,recipe,long_pairs,f'calibration/{bits}/long',f'cal-{bits}-{index}-long','long')
            item['long']=long
            for n in quick_sizes:
                quick=evaluate(root,recipe,n,f'calibration/{bits}/quick/{n}',f'cal-{bits}-{index}-q{n}','quick')
                all_rows[n].append({'id':cid,'split':item['split'],'quick_pass':quick['failed_inputs']==0,
                  'useful':long['failed_inputs']==0 and long['S']<min(costs.values()),
                  'long_pass':long['failed_inputs']==0,'quick_seconds':quick['wall_seconds'],
                  'long_seconds':long['wall_seconds'],'quick_S':quick['S'],'long_S':long['S'],
                  'quick_receipt':quick['evaluation_directory'],'long_receipt':long['evaluation_directory']})
            save(root/'calibration'/str(bits)/f'{index}.json',item,True)
            save(root/'progress.json',{'runs':[],'stage':f'calibration {bits}: {index+1}/{len(items)}'})
        summaries={str(n):{split:screen_metrics([r for r in rs if r['split']==split]) for split in ['fit','held_out']} for n,rs in all_rows.items()}
        eligible=[n for n in quick_sizes if summaries[str(n)]['fit']['useful_recall'] is not None
                  and summaries[str(n)]['fit']['useful_recall']>=.9
                  and summaries[str(n)]['fit']['cascade_seconds']<summaries[str(n)]['fit']['direct_long_seconds']]
        chosen=min(eligible,key=lambda n:summaries[str(n)]['fit']['cascade_seconds']) if eligible else None
        report[str(bits)]={'long_pairs':long_pairs,'quick_pairs':chosen,'policy':'failure_screen' if chosen else 'direct_long',
              'weights':weights,'baselines':originals,'sizes':summaries,'rows':{str(n):rs for n,rs in all_rows.items()},
              'held_out_policy_changed':False,'reference_scope':'Finite long-development outcomes; no correctness proof.'}
        save(root/'calibration'/str(bits)/'summary.json',report[str(bits)],True)
    c['calibration']=report;save(root/'study.json',c)
    (root/'contract.sha256').write_text(engine.digest(c)+'\n')
    return report


def _packet(root,run):
    c=load(Path(root)/'study.json')
    parents=[];diagnostics=[]
    for bid,state in run['baselines'].items():
        entries=list(state['archive'].values())
        valid=sorted([e for e in entries if e['result']['stage']=='long' and not e['result']['failed_inputs']],key=lambda e:e['result']['S'])[:2]
        failed=[e for e in entries if e['result']['failed_inputs']][-2:]
        for index,e in enumerate(valid+failed):
            parents.append({'id':bid+'/p'+str(index),'recipe':e['recipe'],'S':e['result']['S'],
                            'failed_inputs':e['result']['failed_inputs']})
        for t in [t for t in run['trials'] if t['baseline']==bid][-4:]:
            diagnostics.append({'baseline':bid,'rule':t.get('rule_id'),'status':t['status'],
                                'S':t.get('S'),'first_failures':t.get('first_failures',[])[:1]})
    catalogue=[{k:card[k] for k in ['id','name','reasoning','parameters','conditions']}
               for card in rules.cards(Path(root)/'rules') if card['id'] in load(Path(root)/'study.json')['active_rules']]
    packet={'task':'modular_division','score':'S=Q*round_half_up(T), lower is better',
      'instruction':'Return at most eight targeted corrections or improvements. Use no tools. Each proposal changes one rule. '
                    'changes_json must encode {"rule_id":"Rxx","parameters":{...}}. '
                    'Use a listed parent ID. Preserve bits and prime. Explain the hypothesis. Avoid repeated failed settings.',
      'rules':catalogue,'parents':parents,'diagnostics':diagnostics,'response_schema':providers.SCHEMA}
    if c.get('grammar')=='blocks-v1':
        from . import study_grammar
        packet['block_grammar']=study_grammar.packet_description()
        policy=c.get('proposal_policy','historical-v1')
        if policy=='guarded-seeds-v1':packet['block_grammar']['walk_guards']=study_policy.description(run['bits'],search_space.PRIMES[run['bits']])
        for parent in parents:parent['legal_rule_ids']=study_grammar.legal_rules(parent['recipe'],c['active_rules'],policy)
    return packet


def _progress(root,run):
    directory=Path(root)/'runs'/run['id'];save(directory/'run.json',run);save(directory/'trace.json',run['trace'])
    index=load(Path(root)/'progress.json')
    entry={'id':run['id'],'bits':run['bits'],'strategy':run['strategy'],'format':run['format'],'seed':run['seed'],
           'trace':'runs/'+run['id']+'/trace.json','status':run['status']}
    other=[r for r in index.get('runs',[]) if r['id']!=run['id']]
    save(Path(root)/'progress.json',{'runs':other+[entry],'stage':run['status']+' '+run['id']})


def search(root,bits,strategy,seed,format_name='compact_json'):
    root=Path(root);c=check(root)
    if strategy not in ['random','evolution','llm']:raise ValueError('Unknown strategy')
    if format_name not in packets.FORMATS:raise ValueError('Unknown context format')
    if strategy=='llm' and (c['provider']=='offline' or not c['model']):raise ValueError('LLM searches require a live provider and model')
    if not c['calibration'] or str(bits) not in c['calibration']:raise ValueError('Calibrate the screen first')
    if seed not in c['seeds']:raise ValueError('Search seed is outside the contract')
    if (root/'final/manifest.json').exists():raise ValueError('Finalists are frozen; start another study')
    rid=f'{bits}-{strategy}-{format_name}-{seed}';out=root/'runs'/rid;out.mkdir(parents=True,exist_ok=False)
    if rid not in planned_runs(c):
        out.rmdir()
        raise ValueError('Search arm is outside the frozen comparison matrix')
    rng=random.Random(seed);cal=c['calibration'][str(bits)];start=time.monotonic()
    run={'id':rid,'bits':bits,'strategy':strategy,'format':format_name,'seed':seed,'status':'running',
         'baselines':{},'trials':[],'trace':[],'model_calls':[],'source_sha256':c['source_sha256'],
         'contract_sha256':engine.digest(c),'budget':{'proposals_per_baseline':c['trials_per_baseline'],'wall_seconds':c['wall_seconds_per_run']}}
    def cost(event):
        append(out/'costs.jsonl',event)
        if event['kind']=='model':append(root/'costs.jsonl',{'run':rid,**event})
    for bid,recipe in search_space.baseline_recipes(bits).items():
        r=evaluate(root,recipe,cal['long_pairs'],f'development/{bits}',rid+'-'+bid+'-start','long',
                   circuit_directory=out/'circuits'/engine.digest(recipe))
        if r['failed_inputs']:raise ValueError('A starting circuit fails development checks')
        entry={'id':engine.digest(recipe),'recipe':recipe,'result':r,'parent_id':None,'rule_history':None}
        run['baselines'][bid]={'archive':{entry['id']:entry},'starting':entry,'selected':entry,'seen':[entry['id']]}
        run['trace'].append({'baseline':bid,'trial':0,'best_S':r['S'],'candidate_S':r['S'],'status':'baseline',
                             'wall_seconds':time.monotonic()-start,'id':entry['id']})
        cost({'kind':'baseline','baseline':bid,'wall_seconds':r['wall_seconds'],'cpu_seconds':r['cpu_seconds'],'pairs':r['pairs']})
    _progress(root,run);pending={b:[] for b in run['baselines']}
    for trial in range(1,c['trials_per_baseline']+1):
        if time.monotonic()-start>=c['wall_seconds_per_run']:run['status']='budget_exhausted';break
        if strategy=='llm' and trial in c['review_rounds'] and len(run['model_calls'])<c['max_model_calls_per_run']:
            packet=_packet(root,run);prompt=packets.encode(packet,format_name)
            save(out/'model'/f'{trial}-packet.json',packet,True)
            (out/'model'/f'{trial}-prompt.txt').write_text(prompt)
            save(out/'model'/f'{trial}-profile.json',packets.profile(packet),True)
            answer,receipt=providers.generate(c['provider'],c['model'],prompt,
                timeout=min(c.get('model_timeout',120),max(1,c['wall_seconds_per_run']-(time.monotonic()-start))),
                reasoning_effort=c['reasoning_effort'],isolated=True,
                receipt_path=out/'model'/f'{trial}-receipt.json')
            receipt['round']=trial;run['model_calls'].append(receipt)
            save(out/'model'/f'{trial}-response.json',{'answer':answer,'receipt':receipt},True)
            cost({'kind':'model','round':trial,**receipt})
            _progress(root,run)
            if receipt.get('error') in ['AuthenticationRequired','RequestedModelMismatch']:
                run['status']='provider_blocked';run['wall_seconds']=time.monotonic()-start
                _progress(root,run)
                return run
            allowed={p['id']:p['id'].split('/')[0]+'/'+engine.digest(p['recipe']) for p in packet['parents']}
            for index,proposal in enumerate(answer['proposals'][:8]):
                if not isinstance(proposal,dict):proposal={'parent_id':'invalid','changes_json':'{}','hypothesis':'Malformed reply'}
                parent_ref=proposal.get('parent_id','')
                if not isinstance(parent_ref,str):
                    proposal={**proposal,'parent_id':'invalid-parent-type'};parent_ref='invalid-parent-type'
                bid=parent_ref.split('/')[0] if parent_ref in allowed else list(pending)[index%len(pending)]
                if parent_ref in allowed:proposal={**proposal,'parent_id':allowed[parent_ref],'context_parent_id':parent_ref}
                pending[bid].append(proposal)
        for bid,state in run['baselines'].items():
            if time.monotonic()-start>=c['wall_seconds_per_run']:run['status']='budget_exhausted';break
            record={'trial':trial,'baseline':bid,'origin':'mutation','status':'invalid'}
            t0=time.monotonic();cpu=0.0;eval_seconds=0.0;evals=0;input_cases=0
            try:
                policy=c.get('proposal_policy','historical-v1')
                seeded=study_policy.seed_change(root/'rules',state,trial,policy,c['active_rules'])
                if seeded:
                    parent,applied=seeded;record['origin']='seed'
                elif pending[bid]:
                    proposal=pending[bid].pop(0);record['origin']='model'
                    parent=state['archive'][proposal['parent_id'].split('/')[1]]
                    change=json.loads(proposal['changes_json'])
                    if set(change)!={'rule_id','parameters'} or change['rule_id'] not in c['active_rules']:raise ValueError('Invalid model rule')
                    applied=rules.apply(root/'rules',change['rule_id'],parent['recipe'],change['parameters'])
                    record['hypothesis']=proposal.get('hypothesis')
                else:
                    viable=[e for e in state['archive'].values() if e['result']['stage']=='long' and not e['result']['failed_inputs']]
                    parent=rng.choice(viable) if strategy=='random' or rng.random()<.3 else min(viable,key=lambda e:e['result']['S'])
                    applied=search_space.propose(root/'rules',parent['recipe'],rng,c['active_rules'],
                        admit=lambda candidate:study_policy.admit(candidate,policy))
                recipe=applied['candidate'];cid=engine.digest(recipe)
                record.update(id=cid,parent_id=parent['id'],rule_id=applied['history']['rule_id'],history=applied['history'])
                if not study_policy.admit(recipe,policy):
                    record.update(walk_guard_rejected=True,candidate_recipe=recipe)
                    raise ValueError('The candidate violates the recorded finite walk bounds')
                if cid in state['seen']:record['status']='duplicate'
                else:
                    state['seen'].append(cid);result=None
                    save(out/'candidates'/bid/f'{cid}.json',applied,True)
                    sizes=[('quick',cal['quick_pairs']),('long',cal['long_pairs'])] if cal['quick_pairs'] else [('long',cal['long_pairs'])]
                    for stage,pairs in sizes:
                        evals+=1;input_cases+=pairs;eval_start=time.monotonic()
                        try:
                            result=evaluate(root,recipe,pairs,f'development/{bits}',f'{rid}-{bid}-{trial}-{stage}',stage,
                                timeout=max(.1,min(300,c['wall_seconds_per_run']-(time.monotonic()-start))),
                                circuit_directory=out/'circuits'/cid)
                        finally:eval_seconds+=time.monotonic()-eval_start
                        cpu+=result['cpu_seconds']
                        if stage=='quick' and result['failed_inputs']:
                            if rng.random()>=.1:break
                            record['rejection_exploration']=True
                    entry={'id':cid,'recipe':recipe,'parent_id':parent['id'],'rule_history':applied['history'],'result':result}
                    state['archive'][cid]=entry
                    record.update(status='failed' if result['failed_inputs'] else 'quick_only' if result['stage']=='quick' else 'long_pass',
                                  S=result['S'],failed_inputs=result['failed_inputs'],first_failures=result['first_failures'][:2])
                    if result['stage']=='long' and not result['failed_inputs'] and result['S']<state['selected']['result']['S']:
                        state['selected']=entry;record['status']='improvement'
                cost({'kind':'proposal','trial':trial,'baseline':bid,'status':record['status'],
                      'evaluation_count':evals,'input_cases':input_cases,'evaluator_cpu_seconds':cpu,
                      'evaluator_wall_seconds':eval_seconds,'proposal_wall_seconds':time.monotonic()-t0})
            except (ValueError,KeyError,TypeError,IndexError,subprocess.SubprocessError) as exc:
                record.update(status='invalid' if evals==0 else 'evaluation_error',error=str(exc))
                cost({'kind':'proposal','trial':trial,'baseline':bid,'status':record['status'],
                      'evaluation_count':evals,'input_cases':input_cases,'evaluator_cpu_seconds':None if evals else 0,
                      'evaluator_wall_seconds':eval_seconds,'proposal_wall_seconds':time.monotonic()-t0})
            run['trials'].append(record);append(out/'events.jsonl',record)
            run['trace'].append({'baseline':bid,'trial':trial,'best_S':state['selected']['result']['S'],
               'candidate_S':record.get('S'),'status':record['status'],'wall_seconds':time.monotonic()-start,
               'id':record.get('id'),'rule_id':record.get('rule_id')})
            _progress(root,run)
    if run['status']=='running':run['status']='complete'
    run['wall_seconds']=time.monotonic()-start
    for bid,state in run['baselines'].items():
        for trial in [t for t in run['trials'] if t['baseline']==bid and t.get('status') in ['long_pass','improvement','failed']]:
            if trial.get('id') not in state['archive']:continue
            child=state['archive'][trial['id']];parent=state['archive'][child['parent_id']]
            if child['result']['stage']!='long' or parent['result']['stage']!='long':continue
            rules.record(root/'rules',{'id':rid+'-'+bid+'-'+str(trial['trial']),'stage':'development','kind':'paired',
                'rule_ids':[trial['rule_id']],'bits':bits,'interface':'modular_division','baseline_id':bid,
                'weight':cal['weights'][bid],'before':parent['result'],'after':child['result'],
                'attribution':'Recorded single-rule lineage; selected by search. This mean has selection bias.'})
    rules.report(root/'rules')
    _progress(root,run)
    return run


def freeze(root):
    import gzip
    root=Path(root);c=check(root);final=root/'final'
    runs=[load(p) for p in sorted((root/'runs').glob('*/run.json'))]
    if {r['id'] for r in runs}!=planned_runs(c):raise ValueError('The declared comparison matrix is incomplete')
    for run in runs:
        if run['status'] not in ['complete','budget_exhausted']:raise ValueError('An unfinished search cannot be frozen')
        if run['contract_sha256']!=engine.digest(c):raise ValueError('Search used a different task contract')
        expected=search_space.baseline_recipes(run['bits'])
        if set(run['baselines'])!=set(expected):raise ValueError('Every baseline must remain in the comparison')
        for bid,state in run['baselines'].items():
            if state['starting']['recipe']!=expected[bid]:raise ValueError('The starting circuit changed')
            selected=state['selected']
            if selected!=state['archive'].get(selected['id']) or selected['result']['stage']!='long' or selected['result']['failed_inputs']:raise ValueError('Selected circuit lacks a long pass')
            count=sum(t['baseline']==bid for t in run['trials'])
            if count>c['trials_per_baseline'] or (run['status']=='complete' and count!=c['trials_per_baseline']):raise ValueError('Search proposal accounting is incomplete')
    final.mkdir(exist_ok=False)
    circuits={};selections=[];controls=[]
    for run in runs:
        if run['contract_sha256']!=engine.digest(c):raise ValueError('Search used a different task contract')
        for bid,state in run['baselines'].items():
            refs={}
            for kind in ['starting','selected']:
                entry=state[kind];cid=entry['id'];refs[kind]=cid
                if cid not in circuits:
                    source=root/entry['result']['circuit_directory'];target=final/'circuits'/cid
                    shutil.copytree(source,target)
                    circuits[cid]={'recipe':entry['recipe'],'operation_sha256':entry['result']['operation_sha256'],
                      'files':{str(p.relative_to(final)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file()}}
            selections.append({'run':run['id'],'strategy':run['strategy'],'format':run['format'],'seed':run['seed'],
                  'bits':run['bits'],'baseline':bid,'weight':c['calibration'][str(run['bits'])]['weights'][bid],**refs})
            selected=state['selected']
            if selected['recipe']['cleanup']=='measured':
                control=copy.deepcopy(selected['recipe']);control['cleanup']='toffoli';cid=engine.digest(control)
                if cid not in circuits:
                    target=final/'circuits'/cid;meta=division.export_circuit(control,target)
                    circuits[cid]={'recipe':control,'operation_sha256':meta['operation_sha256'],
                        'files':{str(p.relative_to(final)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file()}}
                pair={'before':cid,'after':selected['id'],'rule':'R02','bits':run['bits']}
                if not any(p['before']==cid and p['after']==selected['id'] for p in controls):
                    count=0;conditional=False
                    with gzip.open(final/'circuits'/selected['id']/'gates.jsonl.gz','rt') as stream:
                        for line in stream:
                            op=json.loads(line)
                            if op[0]=='HMR':count+=1;conditional=conditional or op[2] is not None
                    pair.update(predicted_delta_T=-count if not conditional else None,predicted_delta_Q=0)
                    controls.append(pair)
    manifest={'contract_sha256':engine.digest(c),'source_sha256':c['source_sha256'],'circuits':circuits,
              'selections':selections,'pairs':c['audit_pairs'],'epsilon':c['epsilon'],'alpha':c['alpha'],
              'comparison_size':len(circuits),'controls':controls}
    save(final/'manifest.json',manifest,True);(final/'manifest.sha256').write_text(engine.digest(manifest)+'\n')
    return manifest


def audit(root):
    root=Path(root);c=check(root);final=root/'final';m=load(final/'manifest.json')
    if engine.digest(m)!=(final/'manifest.sha256').read_text().strip() or engine.digest(c)!=m['contract_sha256']:raise ValueError('Frozen manifest changed')
    if (final/'consumed.json').exists():raise FileExistsError('Audit inputs are already consumed')
    for item in m['circuits'].values():
        for name,sha in item['files'].items():
            if not (final/name).is_file() or hashlib.sha256((final/name).read_bytes()).hexdigest()!=sha:raise ValueError('Frozen circuit changed')
    label=secrets.token_hex(32);save(final/'consumed.json',{'label':label,'status':'consumed_before_evaluation'},True)
    receipts={}
    for cid,item in m['circuits'].items():
        r=evaluate(root,item['recipe'],m['pairs'],label,'audit-'+cid,'audit',timeout=1800,circuit_directory=final/'circuits'/cid)
        if r['operation_sha256']!=item['operation_sha256']:raise ValueError('Audit operation identity changed')
        r['epsilon_upper']=engine.upper(r['failed_inputs'],r['pairs'],m['alpha']/m['comparison_size'])
        r['eligible']=r['epsilon_upper']<=m['epsilon'];receipts[cid]=r
        save(final/'partial-results.json',receipts)
    rows=[]
    for s in m['selections']:
        before,after=receipts[s['starting']],receipts[s['selected']]
        eligible=before['eligible'] and after['eligible']
        rows.append({**s,'starting_S':before['S'],'S':after['S'],'T':after['T'],'Q':after['Q'],
             'failed_inputs':after['failed_inputs'],'pairs':after['pairs'],'epsilon_upper':after['epsilon_upper'],
             'eligible':eligible,'gain_percent':100*max(0,1-after['S']/before['S']) if eligible else 0})
    result={'status':'complete','rows':rows,'receipts':receipts,'multiplicity':m['comparison_size'],
            'epsilon':m['epsilon'],'scope':c['claim_limit'],'controls':m['controls']}
    save(final/'results.json',result,True)
    return result
