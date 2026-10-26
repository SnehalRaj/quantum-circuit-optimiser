"""A fixed native comparison with a shared Claude proposer and fresh audits."""
import json
import copy
import hashlib
import importlib.metadata
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

from . import full_integrity as integrity, full_native as native
from . import swarm_native as swarm
from .openevolve_selection import PopulationPolicy, action_menu, decode_action


UPSTREAM='04a37f191b2eb367daef455057a0470c0ac853d8'
BASE='60d54cb55f29d42b3c6e14903b0674ba7b4db942'
PROJECT=Path(__file__).resolve().parents[2]


class InstrumentError(RuntimeError):
    """Stop both arms when the fixed measurement system fails."""


class CandidateRejected(ValueError):
    """The frozen emitter rejected the requested native recipe."""


def admit_checked(policy,row):
    try:return policy.add(row)
    except Exception as error:raise InstrumentError('The population adapter failed; stop both arms') from error


def emit_checked(root,value):
    root=Path(root);directory=root/'circuits'/swarm.identity(value)
    def verify_rejection():
        try:
            receipt=json.loads((directory/'process.json').read_text())
            if json.loads((directory/'recipe.json').read_text())!=value:raise ValueError('Rejected recipe identity changed')
            binary=root/config_tool(root,'full-point-add-generator')['file']
            expected=[str(binary),*swarm.native_arguments(value),'--out',str(directory/'ops.bin')]
            if (receipt.get('status')!='completed' or receipt.get('returncode') not in (2,101)
                    or receipt.get('command',[])[-len(expected):]!=expected
                    or receipt.get('environment')!={'PATH':'/usr/bin:/bin','TMPDIR':str(directory.resolve())}
                    or not all((directory/name).is_file() for name in ('stdout.txt','stderr.txt'))):
                raise ValueError('The cached emission rejection is incomplete')
        except (ValueError,OSError,KeyError,TypeError) as error:
            raise InstrumentError('The cached emission rejection failed validation') from error
    if directory.exists() and not (directory/'artifact.json').exists():
        verify_rejection()
        raise CandidateRejected('The native generator previously rejected this recipe')
    try:return swarm.emit(root,value)
    except RuntimeError as error:
        verify_rejection()
        raise CandidateRejected('The native generator rejected this recipe') from error
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        raise InstrumentError('The native emission artifact failed its integrity check') from error


def validate_configuration(config):
    required={'current':32,'openevolve':32}
    if (config.get('calls_per_seed')!=required or config.get('seeds')!=[241,509,887]
            or config.get('provider')!='claude-cli' or config.get('model')!='claude-opus-5-5'
            or config.get('effort')!='medium' or config.get('screen_pairs')!=64
            or config.get('development_pairs')!=512 or config.get('selection_pairs')!=32768
            or config.get('audit_pairs')!=1048576 or config.get('streams')!=2
            or config.get('epsilon')!=1e-5 or config.get('family_size')!=3):
        raise ValueError('The pilot differs from the registered comparison')


def reserve_model_slot(root,arm,seed,slot):
    root=Path(root);config=integrity.read_sealed(root/'config.json')
    validate_configuration(config)
    if arm not in config['calls_per_seed'] or seed not in config['seeds'] or type(slot) is not int \
            or not 0<=slot<config['calls_per_seed'][arm]:
        raise ValueError('The model attempt lies outside the registered budget')
    path=root/'model'/f'{seed}-{arm}-{slot:03}'/'reservation.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:
        json.dump({'arm':arm,'seed':seed,'slot':slot,'reserved_unix':time.time(),
                   'status':'consumed before Claude invocation'},stream,indent=2)
    return path


def verify_snapshot(root):
    root=Path(root)
    for relative,wanted in integrity.read_sealed(root/'source-manifest.json').items():
        native.assert_hash(root/relative,wanted)


def freeze_family(baseline,finalists):
    if set(finalists)!={'current','openevolve'}:
        raise ValueError('Freeze one endpoint for each registered arm')
    members={};roles={}
    for role,row in [('baseline',baseline),*finalists.items()]:
        row=baseline if row is None else row
        sha=row['artifact']['sha256'];members.setdefault(sha,row);roles[role]=sha
    if len(members)>3:raise ValueError('The audit family exceeds its registered size')
    return {'frozen_unix':time.time(),'members':members,'roles':roles,
            'stage':'openevolve-pilot-final','circuit_ids':sorted(members),'confidence':.95,
            'alpha':.05/3,'pairs':1048576,'streams':2,'epsilon':1e-5,
            'rule':'One audit per distinct frozen stream. No replacement after audit outcomes.'}


def qualified_winners(family,results):
    answer={}
    for arm in ('current','openevolve'):
        eligible=[]
        for sha in sorted({family['roles']['baseline'],family['roles'][arm]}):
            result=results.get(sha,{})
            if result.get('upper',1)<=family['epsilon'] and result.get('official',{}).get('passed') is True:
                eligible.append({'sha256':sha,'S':result['official']['S'],'result':result})
        answer[arm]=min(eligible,key=lambda r:(r['S'],r['sha256'])) if eligible else None
    return answer


def verify_runtime(root):
    root=Path(root);config=integrity.read_sealed(root/'config.json')
    validate_configuration(config);verify_snapshot(root)
    for relative,wanted in config['project_sources'].items():native.assert_hash(PROJECT/relative,wanted)
    import openevolve
    package=Path(openevolve.__file__).resolve().parent
    for relative,wanted in config['openevolve_sources'].items():native.assert_hash(package/relative,wanted)
    if os.environ.get('PYTHONHASHSEED')!='0':raise ValueError('Run this pilot with PYTHONHASHSEED=0')
    native.assert_hash(config['claude_cli']['path'],config['claude_cli']['sha256'])
    return config


def initialise(root,upstream_source):
    from .openevolve_model import cli_environment
    root=Path(root).resolve();upstream_source=Path(upstream_source).resolve()
    if (root/'config.json').exists():raise FileExistsError('This pilot is already frozen')
    root.mkdir(parents=True,exist_ok=True);(root/'preflight').mkdir(exist_ok=True)
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=upstream_source,capture_output=True,text=True,check=True).stdout.strip()
    if revision!=UPSTREAM:raise ValueError('Use the reviewed OpenEvolve revision')
    executables={}
    for name in ('full-point-add-generator','study-check','official-point-add-check'):
        source=PROJECT/'native/target/release'/name
        target=root/'tools'/(name+'.bin');target.parent.mkdir(exist_ok=True)
        shutil.copy2(source,target)
        executables[name]={'file':str(target.relative_to(root)),'sha256':native.file_hash(target)}
    paths=[p for p in (PROJECT/'src/circuitopt').rglob('*') if p.is_file()
           and '__pycache__' not in p.parts and p.suffix!='.pyc']
    paths += [p for p in (PROJECT/'native').rglob('*') if p.is_file() and
              not any(x in ('target','logs','__pycache__') for x in p.relative_to(PROJECT).parts)]
    paths += [PROJECT/'pyproject.toml',PROJECT/'AGENTS.md',PROJECT/'tools/run_openevolve_pilot.py']
    project_sources={}
    for path in paths:
        relative=path.relative_to(PROJECT);target=root/'source'/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
        project_sources[str(relative)]=native.file_hash(path)
    third=root/'source/upstream';third.mkdir(parents=True)
    for name in ('openevolve','pyproject.toml','setup.py','README.md','LICENSE','MANIFEST.in'):
        source=upstream_source/name
        if source.is_dir():shutil.copytree(source,third/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        elif source.exists():shutil.copy2(source,third/name)
    import openevolve
    package=Path(openevolve.__file__).resolve().parent
    oe_sources={str(p.relative_to(package)):native.file_hash(p) for p in package.rglob('*')
                if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    for relative,wanted in oe_sources.items():native.assert_hash(third/'openevolve'/relative,wanted)
    native.save(root/'tools.json',{'executables':executables,'native_sources':project_sources})
    integrity.seal(root/'tools.json')
    with __import__('tempfile').TemporaryDirectory() as d:
        policy=PopulationPolicy('openevolve',241,Path(d));population=policy.configuration()
        population['db_path']='Supplied per arm and seed under this run.'
    cli_path=Path(shutil.which('claude')).resolve()
    cli={'path':str(cli_path),'sha256':native.file_hash(cli_path)}
    config={'schema':1,'provider':'claude-cli','model':'claude-opus-5-5','effort':'medium',
            'seeds':[241,509,887],'calls_per_seed':{'current':32,'openevolve':32},
            'screen_pairs':64,'development_pairs':512,'selection_pairs':32768,
            'selection_shortlist':4,'audit_pairs':1048576,'audit_chunk_pairs':65536,
            'streams':2,'epsilon':1e-5,'family_size':3,'threads':4,'upstream_revision':revision,
            'native_source_commit':BASE,'python':sys.version,'pythonhashseed':'0',
            'project_sources':project_sources,'openevolve_sources':oe_sources,
            'population_configuration':population,'elapsed_limit':None,'spend_limit':None,
            'per_call_timeout':None,'claude_cli':cli,
            'authorisation':'User permits unlimited time and calls; use Claude CLI with no API key.',
            'budget_unit':'One reserved Claude CLI invocation; record internal turns when reported.',
            'fitness':'Higher combined_score = initial common development S / candidate S.',
            'baseline':swarm.recipe(PP_WALK_MAX_QUBITS=1316,CP_FOLD_CAP=1316),
            'prefix':'openevolve-pilot/'+secrets.token_hex(24),
            'scope':'Population policies, including parent and inspiration selection, with a shared Claude proposer. No automatic rule invention or Google-depth claim.'}
    native.save(root/'config.json',config);integrity.seal(root/'config.json')
    protected=[p for directory in (root/'source',root/'tools') for p in directory.rglob('*') if p.is_file()]
    protected += [root/'config.json',root/'config.json.sha256',root/'tools.json',root/'tools.json.sha256']
    native.save(root/'source-manifest.json',{str(p.relative_to(root)):native.file_hash(p) for p in protected})
    integrity.seal(root/'source-manifest.json')
    packages={d.metadata['Name']:d.version for d in importlib.metadata.distributions() if d.metadata['Name']}
    native.save(root/'dependencies.json',dict(sorted(packages.items())))
    (root/'requirements.lock').write_text('\n'.join(f'{name}=={version}' for name,version in sorted(packages.items())
        if name.lower() not in ('openevolve','reversible-circuit-optimizer'))+'\n')
    for args,name in [([str(cli_path),'--version'],'claude-version'),([str(cli_path),'auth','status','--json'],'claude-auth')]:
        proc=subprocess.run(args,capture_output=True,text=True,check=True,env=cli_environment())
        if name=='claude-auth':
            raw=json.loads(proc.stdout)
            if raw.get('authMethod')!='claude.ai' or raw.get('apiKeySource') is not None:
                raise ValueError('This pilot requires a subscription CLI login without an API key')
            native.save(root/'preflight'/f'{name}.json',{k:raw.get(k) for k in ('loggedIn','authMethod','apiKeySource','subscriptionType')})
        else:(root/'preflight'/f'{name}.txt').write_text(proc.stdout)
    return config


def evaluated(root,artifact,label,pairs,name,config):
    try:
        return swarm.checked_evaluate(root,artifact,label=label,pairs=pairs,name=name,threads=config['threads'])
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as error:
        raise InstrumentError('The native measurement failed; stop the comparison') from error


def preflight(root,config):
    directory=root/'preflight/checker-controls'
    binary=root/config_tool(root,'study-check')['file']
    proc,_=native.command(root,[binary,'--self-test','--out',directory/'result.json'],directory=directory)
    if proc.returncode or not json.loads((directory/'result.json').read_text())['passed']:
        raise ValueError('The fixed checker diagnostic controls failed')
    baseline=emit_checked(root,config['baseline'])
    prior=PROJECT/'runs/grammar-swarm-20261023/full-point-add/preflight.json'
    if prior.exists():
        old=next(r for r in json.loads(prior.read_text()) if r['name']=='prior_frontier_cap1316')
        if baseline['sha256']!=old['artifact']['sha256']:
            raise ValueError('The native baseline did not reproduce the existing stream')
    eligibility=copy.deepcopy(config['baseline'])
    eligibility['settings'].update(CP_FOLD_STREAMED=1,CP_INLET_EARLY=1,CP_INLINE_PLUS2=1)
    active=emit_checked(root,eligibility)
    profile=json.loads((root/active['profile_file']).read_text())
    eligible={'inlet_sites':sorted(r['id'] for r in profile if r.get('retired')),
              'inline_sites':sorted(r['id'] for r in profile if r.get('inline'))}
    native.save(root/'preflight/eligible-sites.json',eligible)
    native.save(root/'preflight/baseline.json',baseline)
    return baseline,eligible


def config_tool(root,name):
    tool=integrity.read_sealed(root/'tools.json')['executables'][name]
    native.assert_hash(root/tool['file'],tool['sha256']);return tool


def prompt_for(parent,inspirations,menu,failures,profile=()):
    def brief(row):return {'recipe':row['recipe'],'measurements':row['screen'],'depth':row['depth']}
    return {'task':'Reduce Q times rounded mean executed Toffoli count for complete secp256k1 point addition.',
            'constraints':'Choose exactly one listed native action. Preserve output, phase, addend and clean scratch. Small development passes do not prove rare-failure reliability.',
            'grammar_notes':{
                'CP_FOLD_SPLIT':'Permit a split carry ladder in correction folds.',
                'CP_FOLD_STREAMED':'Stream selected bits during carry repair to reduce local temporary storage.',
                'CP_INLET_EARLY':'Retire the inverse inlet after its last use, with the fixed phase repair.',
                'CP_INLINE_PLUS2':'Reuse the routed selector wire in the inverse fold.',
                'site_actions':'Apply the corresponding guarded storage rule at one emitted fold occurrence.',
                'width_caps':'PP_WALK_MAX_QUBITS and CP_FOLD_CAP constrain different modules. One unchanged module can retain the old global peak. Lower caps can increase Toffoli count.',
                'comparison_windows':'Smaller replay windows and comparison widths can lower cost and increase rare failures.',
                'neutral_parents':'A valid neutral or larger parent may enable a later saving. Local wire savings need not reduce the global peak.'},
            'parent':brief(parent),'inspirations':[brief(r) for r in inspirations],
            'offered_site_profiles':[r for r in profile if r['id'] in {a['site'] for a in menu if 'site' in a}],
            'recent_failed_attempts':failures[-2:],'actions':menu,
            'reply':{'action_index':'integer index, starting at zero','reason':'brief physical explanation'}}


def run(root):
    from . import openevolve_model
    root=Path(root).resolve();config=verify_runtime(root)
    with (root/'started.json').open('x') as f:json.dump({'unix':time.time()},f)
    baseline,eligible=preflight(root,config)
    policies={};failures={};trials=[]
    for seed in config['seeds']:
        label=f"{config['prefix']}/screen/{seed}"
        screened=evaluated(root,baseline,label,64,f'initial-{seed}-64',config)
        development=evaluated(root,baseline,f"{config['prefix']}/development/{seed}",512,f'initial-{seed}-512',config)
        if screened['failed_inputs'] or development['failed_inputs']:raise ValueError('The common starting circuit failed development')
        row={'id':baseline['id'],'recipe':baseline['recipe'],'artifact':baseline,'screen':development,
             'initial_screen':screened,'depth':0,'changed_stream_depth':0,'history':[]}
        for arm in ('current','openevolve'):
            key=(seed,arm);policies[key]=PopulationPolicy(arm,seed,root/'arms'/str(seed)/arm)
            admit_checked(policies[key],row);failures[key]=[]
    for slot in range(32):
        for si,seed in enumerate(config['seeds']):
            order=('current','openevolve') if (si+slot)%2==0 else ('openevolve','current')
            for arm in order:
                verify_runtime(root);key=(seed,arm);policy=policies[key]
                parent,examples=policy.choose(slot)
                profile=json.loads((root/parent['artifact']['profile_file']).read_text())
                menu_seed=int(hashlib.sha256(f'{seed}:{slot}:{parent["id"]}'.encode()).hexdigest()[:16],16)
                menu=action_menu(parent['recipe'],profile,eligible,menu_seed,96)
                directory=reserve_model_slot(root,arm,seed,slot).parent
                record={'arm':arm,'seed':seed,'slot':slot,'parent':parent['id'],'status':'reserved'}
                try:
                    answer,_=openevolve_model.propose(prompt_for(parent,examples,menu,failures[key],profile),directory,config['model'],
                        executable=config['claude_cli']['path'],executable_sha256=config['claude_cli']['sha256'])
                    action=decode_action(answer,menu);value=swarm.apply(parent['recipe'],action)
                    cid=swarm.identity(value);record.update(action=action,id=cid)
                    if cid in policy.archive:record['status']='duplicate_recipe'
                    else:
                        artifact=emit_checked(root,value)
                        screen=evaluated(root,artifact,f"{config['prefix']}/screen/{seed}",64,f'{seed}-{arm}-{slot:03}-64',config)
                        record.update(artifact=artifact,screen=screen)
                        if screen['failed_inputs']:record['status']='screen_failed'
                        else:
                            development=evaluated(root,artifact,f"{config['prefix']}/development/{seed}",512,f'{seed}-{arm}-{slot:03}-512',config)
                            record['development']=development
                            if development['failed_inputs']:record['status']='development_failed'
                            else:
                                changed=artifact['sha256']!=parent['artifact']['sha256']
                                child={'id':cid,'recipe':value,'artifact':artifact,'screen':development,
                                       'depth':parent['depth']+1,'changed_stream_depth':parent['changed_stream_depth']+int(changed),
                                       'history':parent['history']+[action]}
                                admit_checked(policy,child);record.update(status='accepted',depth=child['depth'],
                                    changed_stream_depth=child['changed_stream_depth'],gate_stream_changed=changed,
                                    activity=swarm.action_activity(action,json.loads((root/artifact['profile_file']).read_text())))
                except PermissionError as error:
                    record.update(status='model_access_stopped',error=str(error))
                    native.save(directory/'trial.json',record)
                    native.save(root/'interrupted.json',record);raise
                except (InstrumentError,openevolve_model.ModelContractError) as error:
                    record.update(status='instrument_failed',error=str(error))
                    native.save(directory/'trial.json',record)
                    native.save(root/'interrupted.json',record);raise
                except ValueError as error:
                    record.update(status='rejected',error=str(error))
                if record['status'] not in ('accepted','duplicate_recipe'):
                    failures[key].append({k:record[k] for k in ('action','status','screen','development','error') if k in record})
                trials.append(record);native.save(directory/'trial.json',record);policy.checkpoint()
                state={'trials':trials,'archives':{f'{s}-{a}':p.archive for (s,a),p in policies.items()}}
                native.save(root/'search-state.json',state)
                native.save(root/'progress.json',{'stage':'search','completed_attempts':len(trials),'planned_attempts':192,
                    'last':record,'best_development':{a:min(r['screen']['S'] for (s,b),p in policies.items() if b==a for r in p.archive.values()) for a in ('current','openevolve')}})
                print(seed,arm,slot,record['status'],record.get('development',record.get('screen',{})).get('S'),flush=True)
    verify_runtime(root)
    initial=next(iter(policies.values())).archive[baseline['id']]
    selected={};selection=[]
    for arm in ('current','openevolve'):
        pool={}
        for (seed,name),policy in policies.items():
            if name==arm:
                for cid,row in policy.archive.items():
                    if cid not in pool or row['screen']['S']<pool[cid]['screen']['S']:pool[cid]=row
        unique={}
        for row in sorted(pool.values(),key=lambda r:(r['screen']['S'],r['id'])):
            unique.setdefault(row['artifact']['sha256'],row)
        shortlist=list(unique.values())[:4]
        if not any(r['id']==initial['id'] for r in shortlist):shortlist.append(initial)
        passed=[]
        for row in shortlist:
            result=evaluated(root,row['artifact'],f"{config['prefix']}/selection",32768,'selection-'+row['id'],config)
            selection.append({'arm':arm,'candidate':row['id'],'result':result})
            if not result['failed_inputs']:passed.append({**row,'selection':result})
        selected[arm]=min(passed,key=lambda r:(r['selection']['S'],r['id'])) if passed else None
    native.save(root/'selection.json',selection)
    family=freeze_family(initial,selected)
    family['chunks']=[{'label':f"{config['prefix']}/audit/{secrets.token_hex(24)}",'pairs':65536} for _ in range(16)]
    native.save(root/'finalists.json',family);integrity.seal(root/'finalists.json')
    native.save(root/'progress.json',{'stage':'finalists_frozen','roles':family['roles']})
    return family


def official(root,row):
    sha=row['artifact']['sha256'];directory=root/'official'/sha
    directory.mkdir(parents=True,exist_ok=True)
    if (directory/'result.json').exists():
        native.assert_hash(directory/'ops.bin',sha)
        return json.loads((directory/'result.json').read_text())
    with (directory/'reservation.json').open('x') as f:json.dump({'sha256':sha,'attempt':1,'unix':time.time()},f)
    ops=root/row['artifact']['operation_file'];native.assert_hash(ops,sha)
    shutil.copy2(ops,directory/'ops.bin')
    binary=root/config_tool(root,'official-point-add-check')['file']
    proc,timing=native.command(root,[binary,'--note','Frozen OpenEvolve pilot finalist'],directory=directory,sandbox=True)
    native.assert_hash(ops,sha);native.assert_hash(directory/'ops.bin',sha)
    text=proc.stdout;passed=proc.returncode==0 and 'all 9024 shots OK' in text and '=== experiment OK ===' in text
    score=None
    if passed:
        total,shots=map(int,re.search(r'total Toffoli \(sum\)\s*:\s*(\d+) over (\d+) shots',text).groups())
        qubits=int(re.search(r'^\s*qubits\s*:\s*(\d+)',text,re.M).group(1))
        if shots!=9024:raise ValueError('The official test size changed')
        score=((2*total+shots)//(2*shots))*qubits
    result={'passed':passed,'S':score,'receipt':str((directory/'stdout.txt').relative_to(root)),
            'process':str((directory/'process.json').relative_to(root)),'wall_seconds':timing['wall_seconds']}
    native.save(directory/'result.json',result);return result


def qualify(root):
    from scipy.stats import beta
    root=Path(root).resolve();config=verify_runtime(root)
    family=integrity.read_sealed(root/'finalists.json');results={}
    integrity.validate_contract(family,pairs=1048576,circuit_ids=list(family['members']),
        stage='openevolve-pilot-final',streams=2,epsilon=1e-5,confidence=.95)
    trials=json.loads((root/'search-state.json').read_text())['trials']
    expected={(s,a,n) for s in config['seeds'] for a in ('current','openevolve') for n in range(32)}
    observed=[(r['seed'],r['arm'],r['slot']) for r in trials]
    if len(observed)!=192 or set(observed)!=expected:raise ValueError('The matched proposal budgets are incomplete')
    if family['alpha']!=.05/3 or len(family['members'])>3 or sum(c['pairs'] for c in family['chunks'])!=1048576:
        raise ValueError('The fixed audit family changed')
    for sha,row in family['members'].items():
        failed=0;chunks=[];shots=0
        for index,chunk in enumerate(family['chunks']):
            verify_runtime(root)
            name=f'audit-{sha}-{index:02}'
            directory=root/'evaluations'/name
            if not (directory/'result.json').exists():
                native.evaluate(root,row['artifact'],label=chunk['label'],pairs=chunk['pairs'],name=name,fresh=True,threads=config['threads'])
            result,proof=integrity.validate_evaluation(root,directory,row['artifact'],label=chunk['label'],pairs=chunk['pairs'],streams=2)
            native.save(directory/'integrity.json',proof);chunks.append(result)
            failed+=result['failed_inputs'];shots+=result['failed_shots']
            native.save(root/'progress.json',{'stage':'audit','candidate':sha,'completed_chunks':index+1,'total_chunks':16,'failed_inputs_so_far':failed})
            print('audit',sha[:12],index+1,failed,flush=True)
        upper=1. if failed==1048576 else float(beta.ppf(1-family['alpha'],failed+1,1048576-failed))
        entry={'sha256':sha,'failed_inputs':failed,'failed_shots':shots,'pairs':1048576,
               'upper':upper,'alpha':family['alpha'],'chunks':chunks}
        if upper<=config['epsilon']:entry['official']=official(root,row)
        results[sha]=entry;native.save(root/'audit-results.json',results)
    winners=qualified_winners(family,results)
    outcome={'status':'complete','finished_unix':time.time(),'winners':winners,'audits':results,
             'primary_metric':'Best qualified individual official S after the same model-attempt budget.',
             'scope':config['scope'],'openevolve_benefit':None,'model_attempts':192}
    if all(winners.values()):outcome['openevolve_benefit']=1-winners['openevolve']['S']/winners['current']['S']
    native.save(root/'result.json',outcome);native.save(root/'progress.json',{'stage':'complete','winners':winners})
    return outcome
