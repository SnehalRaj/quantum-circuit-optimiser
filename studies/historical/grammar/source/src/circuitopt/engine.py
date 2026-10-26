"""A bounded typed search, a frozen audit, and a reward over the full baseline set."""
import copy
import hashlib
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

from scipy.stats import beta
from . import division, providers


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def save(path, value, exclusive=False):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x' if exclusive else 'w') as out:
        json.dump(value,out,indent=2)
        out.write('\n')


def load(path):
    return json.loads(Path(path).read_text())


def upper(failed, pairs, alpha):
    if not 0<=failed<=pairs or pairs<1 or not 0<alpha<1:
        raise ValueError('Invalid binomial counts')
    return (-math.expm1(math.log(alpha)/pairs) if failed==0 else
            1.0 if failed==pairs else float(beta.isf(alpha,failed+1,pairs-failed)))


def source_fingerprint(contract, directory):
    root=Path(__file__).parent
    records={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob('*.py'))}
    for name,expected in contract.get('evaluator',{}).get('source_sha256',{}).items():
        p=(Path(directory)/name).resolve()
        actual=hashlib.sha256(p.read_bytes()).hexdigest()
        if actual!=expected: raise ValueError('Evaluator source fingerprint changed')
        records[name]=actual
    return digest(records)


def check_recipe(contract, recipe, baseline=None):
    base=(baseline or contract['baselines'][0])['recipe']
    grammar=contract['grammar']
    if not isinstance(recipe,dict) or set(recipe)!=set(base):
        raise ValueError('Recipe fields differ from the fixed interface')
    for k,v in recipe.items():
        if k in grammar:
            if v not in grammar[k] or type(v) is not type(base[k]):
                raise ValueError(f'Illegal typed value for {k}')
        elif v!=base[k] or type(v) is not type(base[k]):
            raise ValueError(f'Fixed recipe field changed: {k}')
    if any(any(word in k.lower() for word in ['nonce','padding','reroll']) for k in recipe):
        raise ValueError('Excluded recipe field')


def validate(contract):
    if contract.get('frozen') is not True:
        raise ValueError('Task 1 contract must be frozen before search')
    baselines=contract.get('baselines',[])
    if not baselines or len({b['id'] for b in baselines})!=len(baselines):
        raise ValueError('Provide a nonempty set of unique baseline IDs')
    if not 0<contract['epsilon']<1 or not 0<contract.get('alpha',.05)<1:
        raise ValueError('Invalid failure criterion')
    for size in contract['samples'].values():
        if type(size) is not int or size<1: raise ValueError('Invalid sample size')
    for b in baselines:
        if not b.get('clean_source_reviewed') or not math.isfinite(b['weight']) or b['weight']<=0:
            raise ValueError('Every baseline needs a clean-source record and fixed positive weight')
        check_recipe(contract,b['recipe'],b)
    evaluator=contract['evaluator']
    if evaluator['kind'] not in ['division','command']: raise ValueError('Unknown evaluator')
    if evaluator['kind']=='command':
        if not evaluator.get('source_sha256') or not isinstance(evaluator.get('argv'),list):
            raise ValueError('Command evaluators need an argument list and pinned sources')
    return contract


def example_contract(bits=16):
    if bits not in (5,16,256): raise ValueError('Example widths: 5, 16, or 256')
    p={5:17,16:65521,256:2**256-2**32-977}[bits]
    return {'schema':1,'name':f'division-example-{bits}','purpose':'software_example',
      'frozen':True,'interface':'modular_division','epsilon':DEFAULT_EPSILON,'alpha':.05,
      'development_label':f'division-example-{bits}-development-v1',
      'samples':{'quick':16 if bits==5 else 1024,'development':32 if bits==5 else 8192},
      'evaluator':{'kind':'division'},'grammar':{'cleanup':['toffoli','measured'],
      'family':['inverse','replay','width']},
      'baselines':[{'id':family,'weight':weight,'clean_source_reviewed':True,
      'recipe':{'bits':bits,'prime':p,'steps':2*bits-1,'family':family,'cleanup':'toffoli'}}
      for family,weight in [('inverse',1),('replay',2),('width',4)]]}


def reward(baselines, gains):
    return sum(b['weight']*gains.get(b['id'],0) for b in baselines)/sum(b['weight'] for b in baselines)


def evaluate(contract, baseline, recipe, root, key, pairs, label, contract_dir, timeout=300, frozen_artifacts=None):
    check_recipe(contract,recipe,baseline)
    root=Path(root)
    target=root/'evaluations'/key
    target.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    if contract['evaluator']['kind']=='division':
        circuit=root/'circuits'/digest(recipe)
        request={'recipe':recipe,'circuit':str(circuit.resolve()),'pairs':pairs,
                 'label':label,'inputs':str((target/'inputs.jsonl.gz').resolve())}
        env={k:v for k,v in os.environ.items() if k in ['PATH','SYSTEMROOT','TMPDIR','LANG']}
        env['PYTHONPATH']=str(Path(__file__).parent.parent)
        proc=subprocess.run([sys.executable,'-m','circuitopt.worker'],input=canonical(request),
                            text=True,capture_output=True,env=env,timeout=timeout,check=False)
        if proc.returncode: raise ValueError('Division worker failed: '+proc.stderr[-300:])
        result=json.loads(proc.stdout)
        result['circuit_directory']=str(circuit.relative_to(root))
        result['inputs_file']=str((target/'inputs.jsonl.gz').relative_to(root))
    else:
        spec=contract['evaluator']
        request={'recipe':recipe,'pairs':pairs,'streams':2,'label':label,
                 'artifact_directory':str(target.resolve()),
                 'frozen_circuit_directory':str(Path(frozen_artifacts).resolve()) if frozen_artifacts else None}
        env={k:v for k,v in os.environ.items() if k in ['PATH','SYSTEMROOT','TMPDIR','LANG']}
        proc=subprocess.run(spec['argv'],input=canonical(request),text=True,capture_output=True,
          cwd=contract_dir,env=env,timeout=timeout,check=False)
        (target/'stdout.txt').write_text(proc.stdout)
        (target/'stderr.txt').write_text(proc.stderr)
        if proc.returncode: raise ValueError(f'Evaluator exited {proc.returncode}')
        result=json.loads(proc.stdout)
        for name in result.get('artifact_files',[]):
            p=(target/name).resolve()
            if not p.is_relative_to(target.resolve()) or not p.is_file():
                raise ValueError('Evaluator returned an absent or external artifact')
        if not result.get('artifact_files'):
            raise ValueError('Evaluator must save the circuit and test evidence inside artifact_directory')
    for key_name in ['Q','T','failed_inputs','pairs','streams','channel_failed_inputs','operation_sha256']:
        if key_name not in result: raise ValueError(f'Missing evaluator field: {key_name}')
    if result['pairs']!=pairs or result['streams']!=2:
        raise ValueError('Evaluator used a different input protocol')
    if type(result['Q']) is not int or result['Q']<1 or not math.isfinite(result['T']) or result['T']<0:
        raise ValueError('Invalid resource count')
    if type(result['failed_inputs']) is not int or not 0<=result['failed_inputs']<=pairs:
        raise ValueError('Invalid failure count')
    ch=result['channel_failed_inputs']
    if set(ch)!={'output','phase','scratch'} or any(type(x) is not int or not 0<=x<=result['failed_inputs'] for x in ch.values()):
        raise ValueError('Incomplete failure channels')
    if max(ch.values())>result['failed_inputs'] or sum(ch.values())<result['failed_inputs']:
        raise ValueError('Inconsistent input failure counts')
    result['evaluation_directory']=str(target.relative_to(root))
    result.update(S=result['Q']*math.floor(result['T']+.5),wall_seconds=time.monotonic()-started,
                  recipe=recipe,recipe_sha256=digest(recipe),label=label,
                  upper_bound=upper(result['failed_inputs'],pairs,contract.get('alpha',.05)))
    source_fingerprint(contract,contract_dir)
    save(target/'receipt.json',result)
    return result


def packet_profile(value):
    pretty=json.dumps(value,indent=2)
    compact=canonical(value)
    typed=copy.deepcopy(value)
    if isinstance(typed.get('parents'),list) and typed['parents']:
        columns=sorted(typed['parents'][0])
        if all(sorted(row)==columns for row in typed['parents']):
            typed['parents']={'columns':columns,'rows':[[row[k] for k in columns] for row in typed['parents']]}
    restored=copy.deepcopy(typed)
    if isinstance(restored.get('parents'),dict) and set(restored['parents'])=={'columns','rows'}:
        table=restored['parents']
        restored['parents']=[dict(zip(table['columns'],row)) for row in table['rows']]
    if restored!=value: raise ValueError('Typed packet did not round-trip')
    counts={}
    try:
        import tiktoken
        previous_cache=os.environ.get('TIKTOKEN_CACHE_DIR')
        os.environ['TIKTOKEN_CACHE_DIR']=str(Path(__file__).parent/'data/tokenizer')
        try:
            enc=tiktoken.get_encoding('o200k_base')
        finally:
            if previous_cache is None: os.environ.pop('TIKTOKEN_CACHE_DIR',None)
            else: os.environ['TIKTOKEN_CACHE_DIR']=previous_cache
        count=lambda s:len(enc.encode(s))
        method='o200k_base_reference_tokens'
    except Exception:
        count=lambda s:math.ceil(len(s)/4)
        method='character_count_divided_by_four_estimate'
    for name,text in [('pretty_json',pretty),('compact_json',compact),('typed_parent_rows',canonical(typed))]:
        counts[name]={'bytes':len(text.encode()),'reference_tokens':count(text)}
    return {'method':method,'formats':counts,'exact_round_trip':True,'schema_scope':'Typed parent column names counted in every packet','provider_billing_usage':None}


def search(contract_path,out,strategy='random',attempts=8,seed=0,provider='offline',model=None,
           batch_size=4,wall_seconds=3600,max_model_calls=4):
    if strategy=='llm' and provider!='offline' and not model:
        raise ValueError('Set --model to freeze the model identity for a reproducible run')
    contract_path=Path(contract_path).resolve()
    contract=validate(load(contract_path))
    out=Path(out)
    out.mkdir(parents=True,exist_ok=False)
    save(out/'contract.json',contract)
    fingerprint=source_fingerprint(contract,contract_path.parent)
    contract_dir=Path('.')
    if contract['evaluator']['kind']=='command':
        contract_dir=out/'evaluator-source'
        contract_dir.mkdir()
        for name in contract['evaluator']['source_sha256']:
            src=(contract_path.parent/name).resolve()
            if not src.is_relative_to(contract_path.parent):
                raise ValueError('Pinned evaluator files must stay within the contract directory')
            dst=contract_dir/name
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(src,dst)
    rng=random.Random(seed)
    if strategy not in ['random','evolution','llm']: raise ValueError('Unknown strategy')
    run={'contract':contract,'contract_directory':'evaluator-source' if contract['evaluator']['kind']=='command' else '.',
         'source_fingerprint':fingerprint,'strategy':strategy,'provider':provider,'model':model,
         'seed':seed,'attempt_budget_per_baseline':attempts,'wall_budget_per_baseline':wall_seconds,
         'max_model_calls_per_baseline':max_model_calls,'baselines':{},'model_calls':[]}
    for baseline in contract['baselines']:
        started=time.monotonic()
        label=contract['development_label']
        bid=baseline['id']
        starting=evaluate(contract,baseline,baseline['recipe'],out,f'{bid}-start',
                          contract['samples']['development'],label,contract_dir,timeout=wall_seconds)
        if starting['failed_inputs']:
            raise ValueError(f'Baseline {bid} fails development checks; complete Task 1 first')
        entries=[{'id':digest(baseline['recipe']),'recipe':baseline['recipe'],'result':starting,
                  'origin':'baseline','parent_id':None}]
        state={'baseline':baseline,'starting':starting,'entries':entries,'attempts':[],'model_calls':0}
        run['baselines'][bid]=state
        seen={entries[0]['id']}
        pending=[]
        for attempt in range(attempts):
            if time.monotonic()-started>=wall_seconds: break
            viable=[e for e in entries if e.get('result') and not e['result']['failed_inputs']]
            if strategy=='llm' and attempt%batch_size==0 and state['model_calls']<max_model_calls:
                packet={'task':contract['interface'],'baseline':bid,'grammar':contract['grammar'],
                  'parents':[{'id':e['id'],'recipe':e['recipe'],'S':e['result']['S']} for e in sorted(viable,key=lambda e:e['result']['S'])[:3]],
                  'diagnostics':state['attempts'][-batch_size:],
                  'instruction':'Propose typed repairs or combinations. Use only parent IDs and legal changes. Return at most four proposals.',
                  'response_schema':providers.SCHEMA}
                prompt=canonical(packet)
                save(out/'model'/f'{bid}-{attempt}-packet.json',packet)
                save(out/'model'/f'{bid}-{attempt}-profile.json',packet_profile(packet))
                response,receipt=providers.generate(provider,model,prompt,
                         timeout=max(.1,min(120,wall_seconds-(time.monotonic()-started))))
                receipt.update(baseline_id=bid,attempt=attempt)
                run['model_calls'].append(receipt)
                state['model_calls']+=1
                save(out/'model'/f'{bid}-{attempt}-response.json',{'response':response,'receipt':receipt})
                pending.extend(response['proposals'][:batch_size])
            proposal=pending.pop(0) if pending else None
            if proposal:
                try:
                    if set(proposal)!={'parent_id','changes_json','hypothesis'}: raise ValueError('Unknown proposal field')
                    parent=next(e for e in entries if e['id']==proposal['parent_id'])
                    changes=json.loads(proposal['changes_json'])
                    if not isinstance(changes,dict) or not set(changes)<=set(contract['grammar']):
                        raise ValueError('Model changed a fixed field')
                    recipe={**parent['recipe'],**changes}
                    origin='model'
                except (ValueError,KeyError,StopIteration,TypeError):
                    state['attempts'].append({'attempt':attempt,'status':'invalid_model_proposal'})
                    save(out/'run.json',run)
                    continue
            else:
                parent=(rng.choice(viable) if strategy=='random' or rng.random()<.2 else
                        min(viable,key=lambda e:e['result']['S']))
                recipe=copy.deepcopy(parent['recipe'])
                field=rng.choice(list(contract['grammar']))
                choices=[v for v in contract['grammar'][field] if v!=recipe[field]]
                if not choices: continue
                recipe[field]=rng.choice(choices)
                origin='mutation'
            cid=digest(recipe)
            record={'attempt':attempt,'id':cid,'parent_id':parent['id'],'origin':origin,
                    'changes':{k:v for k,v in recipe.items() if v!=parent['recipe'][k]}}
            try:
                check_recipe(contract,recipe,baseline)
                if cid in seen:
                    record['status']='duplicate'
                else:
                    seen.add(cid)
                    quick=evaluate(contract,baseline,recipe,out,f'{bid}-{attempt}-quick',
                              contract['samples']['quick'],label,contract_dir,timeout=max(.1,wall_seconds-(time.monotonic()-started)))
                    result=quick
                    if not quick['failed_inputs'] and time.monotonic()-started<wall_seconds:
                        result=evaluate(contract,baseline,recipe,out,f'{bid}-{attempt}-development',
                              contract['samples']['development'],label,contract_dir,timeout=max(.1,wall_seconds-(time.monotonic()-started)))
                    entries.append({'id':cid,'parent_id':parent['id'],'recipe':recipe,
                                    'origin':origin,'hypothesis':proposal.get('hypothesis') if proposal else None,'result':result})
                    record.update(status='evaluated',S=result['S'],failed_inputs=result['failed_inputs'],
                                  first_failures=result.get('first_failures',[])[:2])
            except (ValueError,OSError,subprocess.SubprocessError) as exc:
                record.update(status='evaluation_error',error=type(exc).__name__)
            state['attempts'].append(record)
            save(out/'run.json',run)
        state['wall_seconds']=time.monotonic()-started
        state['selected']=min([e for e in entries if not e['result']['failed_inputs']
                              and e['result']['pairs']==contract['samples']['development']],key=lambda e:e['result']['S'])
        save(out/'run.json',run)
    return run


def freeze(run_dirs,out,pairs=1048576):
    if type(pairs) is not int or pairs<1: raise ValueError('Invalid audit size')
    runs=[load(Path(p)/'run.json') for p in run_dirs]
    if not runs or len({digest(r['contract']) for r in runs})!=1:
        raise ValueError('Freeze all runs under the same baseline and evaluation contract')
    budgets={(r['attempt_budget_per_baseline'],r['wall_budget_per_baseline'],r['max_model_calls_per_baseline']) for r in runs}
    if len(budgets)!=1: raise ValueError('Comparison runs use different declared budgets')
    for rdir,r in zip(run_dirs,runs):
        if set(r['baselines'])!={b['id'] for b in r['contract']['baselines']}:
            raise ValueError('A run skipped part of the shared baseline set')
        if r['source_fingerprint']!=source_fingerprint(r['contract'],Path(rdir)/r['contract_directory']):
            raise ValueError('Source fingerprint changed before freeze')
    out=Path(out)
    out.mkdir(parents=True,exist_ok=False)
    for rid,(rdir,r) in enumerate(zip(run_dirs,runs)):
        if r['contract']['evaluator']['kind']=='command':
            target=out/'evaluators'/str(rid)
            shutil.copytree(Path(rdir)/r['contract_directory'],target)
            r['contract_directory']=str(target.relative_to(out))
    manifest={'pairs':pairs,'runs':runs,'comparison_size':sum(2*len(r['baselines']) for r in runs)}
    # Copy all selected circuit files before any audit input exists.
    for rdir,r in zip(run_dirs,runs):
        if r['contract']['evaluator']['kind']=='division':
            for state in r['baselines'].values():
                for recipe in [state['baseline']['recipe'],state['selected']['recipe']]:
                    src=Path(rdir)/'circuits'/digest(recipe)
                    dst=out/'circuits'/digest(recipe)
                    if not dst.exists(): shutil.copytree(src,dst)
    for rid,(rdir,r) in enumerate(zip(run_dirs,runs)):
        if r['contract']['evaluator']['kind']=='command':
            for bid,state in r['baselines'].items():
                for kind,result in [('baseline',state['starting']),('candidate',state['selected']['result'])]:
                    src=Path(rdir)/result['evaluation_directory']
                    dst=out/'frozen-evidence'/str(rid)/bid/kind
                    shutil.copytree(src,dst)
    manifest['circuit_files']={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
      for folder in ['circuits','evaluators','frozen-evidence']
      for p in sorted((out/folder).rglob('*')) if p.is_file()}
    save(out/'manifest.json',manifest,True)
    (out/'manifest.sha256').write_text(digest(manifest)+'\n')
    return manifest


def audit(out):
    out=Path(out)
    manifest=load(out/'manifest.json')
    for run in manifest['runs']:
        if source_fingerprint(run['contract'],out/run['contract_directory'])!=run['source_fingerprint']:
            raise ValueError('Source fingerprint changed after freeze')
    if digest(manifest)!=(out/'manifest.sha256').read_text().strip():
        raise ValueError('Frozen manifest checksum changed')
    for name,expected in manifest.get('circuit_files',{}).items():
        p=out/name
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            raise ValueError('Frozen circuit file checksum changed')
    label=secrets.token_hex(32)
    save(out/'consumed.json',{'label':label,'status':'consumed_before_evaluation'},True)
    rows=[]
    strategy_rewards=[]
    m=manifest['comparison_size']
    for rid,run in enumerate(manifest['runs']):
        contract=run['contract']
        gains={}
        for bid,state in run['baselines'].items():
            baseline=state['baseline']
            original=evaluate(contract,baseline,baseline['recipe'],out,f'{rid}-{bid}-baseline',
                        manifest['pairs'],label,out/run['contract_directory'],timeout=86400,
                        frozen_artifacts=out/'frozen-evidence'/str(rid)/bid/'baseline')
            candidate=evaluate(contract,baseline,state['selected']['recipe'],out,f'{rid}-{bid}-candidate',
                        manifest['pairs'],label,out/run['contract_directory'],timeout=86400,
                        frozen_artifacts=out/'frozen-evidence'/str(rid)/bid/'candidate')
            if original['operation_sha256']!=state['starting']['operation_sha256'] or candidate['operation_sha256']!=state['selected']['result']['operation_sha256']:
                raise ValueError('Frozen operation hash changed during audit')
            ub=upper(candidate['failed_inputs'],manifest['pairs'],contract.get('alpha',.05)/m)
            ub0=upper(original['failed_inputs'],manifest['pairs'],contract.get('alpha',.05)/m)
            eligible=ub<=contract['epsilon'] and ub0<=contract['epsilon']
            starting_S=state['starting']['S']
            gain=100*max(0,1-candidate['S']/starting_S) if eligible else 0
            gains[bid]=gain
            rows.append({'run':rid,'strategy':run['strategy'],'seed':run['seed'],'baseline':bid,
                 'weight':baseline['weight'],'starting_S':starting_S,'Q':candidate['Q'],'T':candidate['T'],
                 'S':candidate['S'],'failed_inputs':candidate['failed_inputs'],'pairs':manifest['pairs'],
                 'epsilon_upper':ub,'baseline_epsilon_upper':ub0,'eligible':eligible,'gain_percent':gain,
                 'candidate_id':state['selected']['id'],'operation_sha256':candidate['operation_sha256']})
        strategy_rewards.append({'run':rid,'strategy':run['strategy'],'provider':run['provider'],'seed':run['seed'],
                                 'reward':reward(contract['baselines'],gains),
                                 'purpose':contract.get('purpose','study'),
                                 'attempt_budget_per_baseline':run['attempt_budget_per_baseline'],
                                 'wall_budget_per_baseline':run['wall_budget_per_baseline'],
                                 'model_calls':sum(c['provider']!='offline' for c in run['model_calls']),
                                 'unknown_usage_calls':sum(c['usage'] is None for c in run['model_calls']),
                                 'reported_input_tokens':sum(c['usage'].get('input_tokens',0) or 0 for c in run['model_calls'] if c['usage']),
                                 'reported_output_tokens':sum(c['usage'].get('output_tokens',0) or 0 for c in run['model_calls'] if c['usage'])})
    result={'status':'audited','rows':rows,'strategy_rewards':strategy_rewards,'multiplicity':m}
    save(out/'results.json',result)
    report(out,result)
    return result


def report(out,result=None):
    import csv
    out=Path(out)
    result=result or load(out/'results.json')
    rows=result['rows']
    with (out/'results.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines=['# Audited strategy results','',
           'Scope: software example when the contract purpose is `software_example`. Offline feedback makes no model call.',
           'These example results establish no ECDSA benchmark improvement.','',
           '| Strategy / run | Baseline | Q | T | S | Eligible | Gain (%) |',
           '|:--|:--|--:|--:|--:|:--:|--:|']
    for r in rows:
        lines.append(f"| {r['strategy']} / {r['run']} | {r['baseline']} | {r['Q']} | {r['T']:.3f} | {r['S']} | {r['eligible']} | {r['gain_percent']:.3f} |")
    lines+=['','| Strategy / provider / run | Weighted reward | Model calls | Reported input / output tokens |','|:--|--:|--:|--:|']
    for r in result['strategy_rewards']:
        lines.append(f"| {r['strategy']} / {r['provider']} / {r['run']} | {r['reward']:.4f} | {r['model_calls']} | {r['reported_input_tokens']} / {r['reported_output_tokens']} |")
    lines+=['','All baselines remain in the reward denominator. No eligible improvement contributes zero.',
            'The example division interface is separate from complete ECDSA point addition.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
