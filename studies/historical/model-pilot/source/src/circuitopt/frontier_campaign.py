"""Run the complete native grammar with the user's exact error contract."""
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import re
import secrets
import shutil
import struct
import subprocess
import sys
import time

from scipy.stats import beta

from . import frontier_grammar as grammar, full_integrity as integrity, full_native as native


PROJECT=Path(__file__).resolve().parents[2]


def audit_bound(failures,pairs,alpha):
    if type(failures) is not int or type(pairs) is not int or not 0<=failures<=pairs or pairs<1 or not 0<alpha<1:
        raise ValueError('Invalid failure count or confidence allowance')
    return 1. if failures==pairs else float(beta.ppf(1-alpha,failures+1,pairs-failures))


def qualifies(failures,pairs,alpha,epsilon=grammar.EPSILON):
    return audit_bound(failures,pairs,alpha)<=grammar.validate_epsilon(epsilon)


def candidate_rank(row):
    if row.get('status')=='constructor_rejected':return 2.,0.,row['id']
    result=row['development'];rate=result['failed_inputs']/result['pairs']
    return max(0.,rate-grammar.EPSILON),result['S'],row['id']


def parse_official(text,returncode):
    passed=returncode==0 and 'all 9024 shots OK' in text and '=== experiment OK ===' in text
    result={'passed':passed,'S':None}
    if passed:
        total,shots=map(int,re.search(r'total Toffoli \(sum\)\s*:\s*(\d+) over (\d+) shots',text).groups())
        qubits=int(re.search(r'^\s*qubits\s*:\s*(\d+)',text,re.M).group(1))
        if shots!=9024:raise ValueError('Official shot count changed')
        rounded=(2*total+shots)//(2*shots)
        result.update(S=qubits*rounded,Q=qubits,T=rounded,total_Toffoli=total,shots=shots,mean_T=total/shots)
    return result


def choose_finalists(rows,controls,limit):
    selected={}
    for row in controls:
        selected.setdefault(row['artifact']['sha256'],row)
    for row in sorted(rows,key=candidate_rank):
        if row['development']['failed_inputs']/row['development']['pairs']>grammar.EPSILON:continue
        if len(selected)>=limit:break
        selected.setdefault(row['artifact']['sha256'],row)
    if len(selected)>limit:raise ValueError('Controls exceed the fixed finalist limit')
    return list(selected.values())


def _build(root,rust_tools):
    spec=importlib.util.spec_from_file_location('native_builder',PROJECT/'native/build.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    cargo,environment=module.build_environment(rust_tools)
    for name,manifest in [('generator','native/generator/Cargo.toml'),('evaluator','native/tools/controlled-study/evaluator/Cargo.toml'),('official','native/official/Cargo.toml')]:
        directory=root/'preflight'/('build-'+name);directory.mkdir(parents=True)
        argv=[cargo,'build','--release','--locked','--offline','--jobs','2','--manifest-path',str(PROJECT/manifest)]
        started=time.time();process=subprocess.run(argv,cwd=PROJECT,env=environment,capture_output=True,text=True)
        (directory/'stdout.txt').write_text(process.stdout);(directory/'stderr.txt').write_text(process.stderr)
        native.save(directory/'process.json',{'command':argv,'environment':environment,'returncode':process.returncode,'wall_seconds':time.time()-started})
        if process.returncode:raise RuntimeError('Native build failed: '+str(directory))


def initialise(root,*,attempts=384,seed=241,maximum_depth=64,finalist_limit=12,audit_pairs=65536,rust_tools=None,epsilon=.001):
    grammar.validate_epsilon(epsilon)
    if attempts<0 or maximum_depth<1 or finalist_limit<4 or audit_pairs<8192:raise ValueError('Invalid campaign size')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=False)
    protected={}
    for directory in ('src/circuitopt','native'):
        for path in (PROJECT/directory).rglob('*'):
            if not path.is_file() or any(p in ('target','logs','__pycache__') for p in path.relative_to(PROJECT).parts) or path.suffix=='.pyc':continue
            relative=path.relative_to(PROJECT);target=root/'source'/relative
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
            protected[str(relative)]=native.file_hash(target)
    for name in ('pyproject.toml','AGENTS.md','agents.yaml'):
        target=root/'source'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(PROJECT/name,target);protected[name]=native.file_hash(target)
    for name,sha in protected.items():native.assert_hash(PROJECT/name,sha)
    _build(root,rust_tools)
    for name,sha in protected.items():native.assert_hash(PROJECT/name,sha)
    executables={}
    for name in ('full-point-add-generator','study-check','official-point-add-check'):
        source=PROJECT/'native/target/release'/name;target=root/'tools'/(name+'.bin')
        target.parent.mkdir(exist_ok=True);shutil.copy2(source,target)
        executables[name]={'file':str(target.relative_to(root)),'sha256':native.file_hash(target)}
    config={'schema':1,'epsilon':grammar.EPSILON,'attempts':attempts,'seed':seed,'maximum_depth':maximum_depth,
            'finalist_limit':finalist_limit,'audit_pairs':audit_pairs,'development_pairs':1024,'selection_pairs':8192,
            'streams':2,'confidence':.95,'threads':4,'prefix':'frontier-grammar/'+secrets.token_hex(24),
            'tools':executables,'source_hashes':protected,'grammar':grammar.definition(),
            'source_binding':'Snapshot before build; source hashes must match before and after all native builds.',
            'metric':'Best individually qualified official S. Initial backend imports and later mutations stay separate.',
            'qualification':'One-sided exact bound <=0.001, then one official local test per distinct frozen stream.',
            'search_admission':'Evaluated circuits and constructor-rejected recipes may be parents. Rejected recipes have no circuit cost. Observed error never qualifies a circuit.',
            'constructor_parent_probability':.2,
            'model_calls':0,'started_unix':time.time()}
    native.save(root/'tools.json',{'executables':executables});integrity.seal(root/'tools.json')
    native.save(root/'config.json',config);integrity.seal(root/'config.json')
    return config


def verify_runtime(root):
    config=integrity.read_sealed(root/'config.json');grammar.validate_epsilon(config['epsilon'])
    for relative,wanted in config['source_hashes'].items():
        native.assert_hash(PROJECT/relative,wanted);native.assert_hash(root/'source'/relative,wanted)
    for tool in config['tools'].values():native.assert_hash(root/tool['file'],tool['sha256'])
    return config


class CandidateRejected(ValueError):
    """The generator rejected this complete recorded proposal."""


def emit(root,value):
    value=grammar.validate(value);cid=grammar.identity(value);directory=root/'circuits'/cid
    tool=integrity.read_sealed(root/'tools.json')['executables']['full-point-add-generator']
    binary=root/tool['file'];native.assert_hash(binary,tool['sha256'])
    if (directory/'artifact.json').exists():
        artifact=json.loads((directory/'artifact.json').read_text())
        if artifact['recipe']!=value or artifact['generator_sha256']!=tool['sha256']:raise ValueError('Cached recipe or generator changed')
        receipt=json.loads((directory/'process.json').read_text())
        if receipt!=artifact['emission'] or receipt.get('returncode')!=0:raise ValueError('Cached emission receipt changed')
        native.assert_hash(root/artifact['operation_file'],artifact['sha256'])
        return artifact
    directory.mkdir(parents=True,exist_ok=True);native.save(directory/'recipe.json',value)
    process,receipt=native.command(root,[binary,*grammar.native_arguments(value),'--out',directory/'ops.bin'],directory=directory,timeout=None,sandbox=True)
    if process.returncode in (2,101):raise CandidateRejected('Native constructor rejected the proposal; inspect '+str(directory))
    if process.returncode:raise RuntimeError('Native generator instrument failed; inspect '+str(directory))
    path=directory/'ops.bin'
    with path.open('rb') as stream:
        if stream.read(8)!=b'QECCOPSZ':raise ValueError('Generator output format changed')
        operations=struct.unpack('<Q',stream.read(8))[0]
    artifact={'id':cid,'recipe':value,'sha256':native.file_hash(path),'operation_file':str(path.relative_to(root)),
              'bytes':path.stat().st_size,'operations':operations,'emission':receipt,'generator_sha256':tool['sha256']}
    native.save(directory/'artifact.json',artifact);return artifact


def evaluate(root,artifact,label,pairs,name,*,fresh=False):
    index_path=root/'evaluation-index'/(name+'.json')
    contract={'artifact_sha256':artifact['sha256'],'label':label,'pairs':pairs,'streams':2,'fresh':fresh}
    if index_path.exists():
        index=json.loads(index_path.read_text())
        if index['contract']!=contract:raise ValueError('Evaluation recovery changed the fixed cohort')
    else:
        index={'contract':contract,'attempts':[]}
        if (root/'evaluations'/name).exists():index['attempts'].append(name)
    completed=[attempt for attempt in index['attempts'] if (root/'evaluations'/attempt/'result.json').exists()]
    if not completed:
        attempt=name if not (root/'evaluations'/name).exists() else name+'-recovery-'+secrets.token_hex(8)
        index['attempts'].append(attempt);native.save(index_path,index)
        native_fresh=fresh
        if fresh:
            key=hashlib.sha256(f"{artifact['id']}\n{label}\n{pairs}".encode()).hexdigest()
            reservation=root/'fresh-tests'/(key+'.json')
            if reservation.exists():
                reserved=json.loads(reservation.read_text())
                if any(reserved.get(k)!=v for k,v in [('candidate',artifact['id']),('label',label),('pairs',pairs)]):raise ValueError('Fresh-input reservation changed')
                native_fresh=False
        native.evaluate(root,artifact,label=label,pairs=pairs,name=attempt,fresh=native_fresh,threads=4)
        if fresh and not native_fresh:
            path=root/'evaluations'/attempt/'result.json';result=json.loads(path.read_text())
            result.update(fresh=True,recovery='Same reserved label, input pairs, and measurement streams; no new cohort.')
            native.save(path,result)
        completed=[attempt]
    reference=None
    for attempt in completed:
        directory=root/'evaluations'/attempt
        result,proof=integrity.validate_evaluation(root,directory,artifact,label=label,pairs=pairs,streams=2)
        native.save(directory/'integrity.json',proof)
        observed={key:result[key] for key in ('inputs_sha256','packet_sha256','Q','T','S','failed_inputs','failed_shots')}
        if reference is not None and observed!=reference:raise ValueError('Repeated fixed-cohort evaluations disagree')
        reference=observed
    index['completed_attempts']=completed;native.save(index_path,index)
    return result


def official(root,artifact):
    directory=root/'official'/artifact['sha256'];directory.mkdir(parents=True,exist_ok=True)
    tool=integrity.read_sealed(root/'tools.json')['executables']['official-point-add-check']
    native.assert_hash(root/tool['file'],tool['sha256'])
    if (directory/'result.json').exists():
        native.assert_hash(directory/'ops.bin',artifact['sha256'])
        result=json.loads((directory/'result.json').read_text())
        if result.get('status')=='interrupted_unqualified':return result
        receipt=json.loads((directory/'process.json').read_text())
        parsed=parse_official((directory/'stdout.txt').read_text(),receipt['returncode'])
        if any(result.get(key)!=value for key,value in parsed.items()):raise ValueError('Cached official result changed')
        return result
    if (directory/'reservation.json').exists():
        native.assert_hash(directory/'ops.bin',artifact['sha256'])
        reservation=json.loads((directory/'reservation.json').read_text())
        if reservation!={'sha256':artifact['sha256'],'attempt':1}:raise ValueError('Official reservation changed')
        receipt=json.loads((directory/'process.json').read_text()) if (directory/'process.json').exists() else {}
        if receipt.get('status')=='completed' and type(receipt.get('returncode')) is int and (directory/'stdout.txt').exists():
            result=parse_official((directory/'stdout.txt').read_text(),receipt['returncode'])
            result['recovery']='Recovered the completed receipt; no new official execution.'
        else:
            result={'passed':False,'S':None,'status':'interrupted_unqualified',
                    'recovery':'The reserved official attempt has no completion proof. Preserve it and continue other finalists.'}
        native.save(directory/'result.json',result);return result
    with (directory/'reservation.json').open('x') as stream:json.dump({'sha256':artifact['sha256'],'attempt':1},stream)
    source=root/artifact['operation_file'];native.assert_hash(source,artifact['sha256']);shutil.copy2(source,directory/'ops.bin')
    process,receipt=native.command(root,[root/tool['file'],'--note','Frozen native grammar candidate; epsilon=1e-3'],directory=directory,sandbox=True,timeout=None)
    result=parse_official(process.stdout,process.returncode)
    result.update(receipt=str((directory/'stdout.txt').relative_to(root)),process=str((directory/'process.json').relative_to(root)))
    native.assert_hash(directory/'ops.bin',artifact['sha256']);native.save(directory/'result.json',result)
    return result


def seed_recipes():
    """Keep source-backed routes separate from generated block programs."""
    from .frontier_programs import equivalent_programs
    rows=[]
    path=PROJECT/'native/generator/src/leapfrog/seed-slate.json'
    for item in json.loads(path.read_text())['seeds']:
        value=grammar.recipe('leapfrog')
        for key,setting in item['settings'].items():
            if key=='AB_PROGRAM':
                value['program']=[dict(zip(('op','lowering'),token.split(':'))) for token in setting.split(',')]
            else:value['settings'][key]=setting
        rows.append((item['id'],grammar.validate(value),item['mechanism']))
    for backend,settings in [
        ('skywalk',{'SK_RAIL_PAD':'3','SK_EXTRA_GROUPS':'1','SK_CAP':'1180'}),
        ('skywalk',{'SK_RAIL_PAD':'4','SK_EXTRA_GROUPS':'2','SK_CAP':'1190'}),
        ('skywalk',{'SK_RAIL_PAD':'6','SK_EXTRA_GROUPS':'3','SK_CAP':'1200'}),
        ('skywalk',{'SK_RAIL_PAD':'11','SK_EXTRA_GROUPS':'4','SK_CAP':'1220','SK_FIT_K':'24','SK_ERASE_COMPARE':'28','SK_FOLD_GUARD':'32'}),
        ('skywalk',{'SK_CAP':'1180','SK_FIT_K':'23','SK_ERASE_COMPARE':'24','SK_FOLD_GUARD':'24','SK_TERMINAL_FW':'44'}),
        ('skywalk',{'SK_CAP':'1200','SK_FIT_K':'24','SK_ERASE_COMPARE':'28','SK_FOLD_GUARD':'32','SK_TERMINAL_FW':'48'}),
        ('skywalk',{'SK_PAIR_SHARE':'0'}),
        ('dialog',{'DL_GAP_DELTA':'2','DL_WIDTH_MARGIN':'2','DL_COMPARE_EXTRA':'2','DL_TARGET_Q':'1180'}),
        ('dialog',{'DL_GAP_DELTA':'4','DL_WIDTH_MARGIN':'4','DL_COMPARE_EXTRA':'4','DL_COUT_ERASE_CAP':'0','DL_TARGET_Q':'1220'}),
        ('dialog',{'DL_MINED':'1'}),
    ]:
        value=grammar.recipe(backend);value['settings'].update(settings)
        rows.append((backend+'-source-variant-'+str(len(rows)),grammar.validate(value),'Source-backed structural or approximation controls.'))
    for index,program in enumerate(equivalent_programs(limit=16)):
        value=grammar.recipe('leapfrog');value['program']=program
        rows.append(('synthesized-program-'+str(index),value,'Exact ideal value-map grouping; native phase and approximation remain unverified.'))
    return rows


def _trial(root,config,value,name,*,parent=None,action=None,mechanism=None):
    row={'id':grammar.identity(value),'recipe':grammar.validate(value),'name':name,
         'parent':parent['id'] if parent else None,'depth':parent['depth']+1 if parent else 0,
         'action':action,'mechanism':mechanism,'status':'started',
         'changed_stream_depth':(parent or {}).get('changed_stream_depth',0),
         'last_operation_sha256':(parent or {}).get('last_operation_sha256',(parent or {}).get('artifact',{}).get('sha256'))}
    native.save(root/'trials'/(name+'.json'),row)
    try:
        row['artifact']=emit(root,value)
    except CandidateRejected as error:
        row.update(status='constructor_rejected',error=str(error))
    else:
        row['development']=evaluate(root,row['artifact'],config['prefix']+'/development',config['development_pairs'],name+'-development')
        row['status']='evaluated'
        changed=row['last_operation_sha256'] is not None and row['last_operation_sha256']!=row['artifact']['sha256']
        row['changed_stream_depth']=(parent or {}).get('changed_stream_depth',0)+int(changed)
        row['last_operation_sha256']=row['artifact']['sha256']
        row['observed_rate']=row['development']['failed_inputs']/row['development']['pairs']
    native.save(root/'trials'/(name+'.json'),row)
    return row


def _new_proposal(pool,seen,rng,depth):
    from .frontier_programs import preserves_replay_map
    if not pool:return None
    viable=[row for row in pool if row['depth']<depth]
    if not viable:return None
    for _ in range(64):
        family=rng.choice(list(grammar.definition()['backends']))
        group=[row for row in viable if row['recipe']['backend']==family]
        if not group:group=viable
        ranked=sorted(group,key=candidate_rank)
        rejected=[row for row in group if row['status']=='constructor_rejected']
        if rejected and rng.random()<.2:parent=rng.choice(rejected)
        else:parent=rng.choice(ranked[:12]) if rng.random()<.8 else rng.choice(group)
        groups={}
        for action in grammar.actions(parent['recipe']):groups.setdefault(action['rule'],[]).append(action)
        rules=list(groups);rng.shuffle(rules)
        for rule in rules:
            candidates=groups[rule];rng.shuffle(candidates)
            for action in candidates:
                try:value=grammar.apply(parent['recipe'],action)
                except (ValueError,IndexError,KeyError):continue
                cid=grammar.identity(value)
                if cid in seen:continue
                if value['program'] and not preserves_replay_map(value['program']) and rng.random()<.95:continue
                return parent,action,value
    return None


@contextmanager
def _coordinator(root):
    with (Path(root).resolve()/'.controller.lock').open('a') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise ValueError('A controller already runs this campaign') from error
        try:yield
        finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)


def run(root):
    with _coordinator(root):return _run(root)


def _run(root):
    root=Path(root).resolve();config=verify_runtime(root)
    if (root/'finalists.json').exists():return _qualify(root)
    if not (root/'started.json').exists():
        with (root/'started.json').open('x') as stream:json.dump({'unix':time.time()},stream)
    checker=config['tools']['study-check'];control_dir=root/'preflight/checker-controls'
    if not (control_dir/'result.json').exists():
        process,_=native.command(root,[root/checker['file'],'--self-test','--out',control_dir/'result.json'],directory=control_dir)
        if process.returncode:raise RuntimeError('Fixed checker controls failed')
    if not json.loads((control_dir/'result.json').read_text())['passed']:raise RuntimeError('Fixed checker controls failed')
    rows=[];controls=[];seen=set();rng=random.Random(config['seed'])
    if (root/'search-state.json').exists():
        state=json.loads((root/'search-state.json').read_text());rows=state['rows']
        controls=[row for row in rows if row['id'] in state['control_ids']]
        seen={row['id'] for row in rows}
        if 'rng_state' in state:
            def tuples(value):return tuple(tuples(item) for item in value) if isinstance(value,list) else value
            rng.setstate(tuples(state['rng_state']))
    for backend in grammar.definition()['backends']:
        if any(row['name']=='baseline-'+backend for row in rows):continue
        value=grammar.recipe(backend);seen.add(grammar.identity(value))
        row=_trial(root,config,value,'baseline-'+backend,mechanism='Inherited backend baseline under the new common contract.')
        rows.append(row)
        if row['status']=='evaluated':controls.append(row)
        native.save(root/'search-state.json',{'rows':rows,'control_ids':[r['id'] for r in controls],'rng_state':rng.getstate()})
        native.save(root/'progress.json',{'stage':'baseline_admission','backend':backend,'status':row['status'],
                    'S':row.get('development',{}).get('S'),'failed_inputs':row.get('development',{}).get('failed_inputs')})
        print('baseline',backend,row['status'],row.get('development',{}).get('S'),row.get('observed_rate'),flush=True)
    pending=seed_recipes();attempt=sum(row['name'].startswith('proposal-') for row in rows)
    while attempt<config['attempts']:
        verify_runtime(root)
        parent=action=None;mechanism=None
        while pending:
            name,value,mechanism=pending.pop(0)
            if grammar.identity(value) not in seen:
                parent=next((r for r in controls if r['recipe']['backend']==value['backend']),None)
                action={'rule':'seed','name':name};break
        else:
            pool=[r for r in rows if r['status'] in ('evaluated','constructor_rejected')]
            proposal=_new_proposal(pool,seen,rng,config['maximum_depth'])
            if proposal is None:break
            parent,action,value=proposal
        cid=grammar.identity(value);seen.add(cid)
        row=_trial(root,config,value,f'proposal-{attempt:04}',parent=parent,action=action,mechanism=mechanism)
        rows.append(row);attempt+=1
        native.save(root/'search-state.json',{'rows':rows,'control_ids':[r['id'] for r in controls],'rng_state':rng.getstate()})
        eligible=[r for r in rows if r['status']=='evaluated' and r['observed_rate']<=grammar.EPSILON]
        native.save(root/'progress.json',{'stage':'search','completed_attempts':attempt,'planned_attempts':config['attempts'],
                    'last_status':row['status'],'last_backend':row['recipe']['backend'],
                    'best_observed_development_S':min((r['development']['S'] for r in eligible),default=None)})
        print('proposal',attempt,row['recipe']['backend'],row['status'],row.get('development',{}).get('S'),row.get('observed_rate'),flush=True)
    evaluated=[r for r in rows if r['status']=='evaluated']
    shortlist=choose_finalists(evaluated,controls,max(config['finalist_limit']*3,len(controls)))
    selection=[]
    for row in shortlist:
        selected=copy.deepcopy(row)
        selected['selection']=evaluate(root,row['artifact'],config['prefix']+'/selection',config['selection_pairs'],'selection-'+row['id'])
        selection.append(selected)
    native.save(root/'selection.json',selection)
    admitted=[r for r in selection if r['selection']['failed_inputs']/r['selection']['pairs']<=grammar.EPSILON]
    admitted.sort(key=lambda r:(r['selection']['S'],r['id']))
    mandatory=[r for r in selection if r['id'] in {c['id'] for c in controls}]
    final={}
    for row in mandatory:final.setdefault(row['artifact']['sha256'],row)
    for row in admitted:
        if len(final)>=config['finalist_limit']:break
        final.setdefault(row['artifact']['sha256'],row)
    if not final:raise RuntimeError('No complete circuit exists for the final family')
    chunks=[];remaining=config['audit_pairs']
    while remaining:
        pairs=min(65536,remaining);remaining-=pairs
        chunks.append({'label':config['prefix']+'/audit/'+secrets.token_hex(24),'pairs':pairs})
    family={'schema':1,'stage':'frontier-grammar-final','epsilon':grammar.EPSILON,'confidence':config['confidence'],
            'alpha':(1-config['confidence'])/len(final),'pairs':config['audit_pairs'],'streams':2,
            'members':final,'circuit_ids':sorted(final),'chunks':chunks,'frozen_unix':time.time(),
            'rule':'Fixed family; no replacements or nonce changes after audit results.'}
    native.save(root/'finalists.json',family);integrity.seal(root/'finalists.json')
    return _qualify(root)


def qualify(root):
    with _coordinator(root):return _qualify(root)


def _qualify(root):
    root=Path(root).resolve();config=verify_runtime(root);family=integrity.read_sealed(root/'finalists.json')
    grammar.validate_epsilon(family['epsilon'])
    integrity.validate_contract(family,pairs=config['audit_pairs'],circuit_ids=list(family['members']),stage='frontier-grammar-final',streams=2,epsilon=grammar.EPSILON,confidence=config['confidence'])
    results={}
    for sha,row in family['members'].items():
        chunks=[]
        for index,chunk in enumerate(family['chunks']):
            verify_runtime(root)
            result=evaluate(root,row['artifact'],chunk['label'],chunk['pairs'],f'audit-{sha}-{index:02}',fresh=True)
            chunks.append(result)
            native.save(root/'progress.json',{'stage':'audit','backend':row['recipe']['backend'],'sha256':sha,
                        'completed_chunks':index+1,'total_chunks':len(family['chunks']),
                        'failed_inputs':sum(c['failed_inputs'] for c in chunks)})
        failures=sum(c['failed_inputs'] for c in chunks)
        bound=audit_bound(failures,config['audit_pairs'],family['alpha'])
        entry={'sha256':sha,'recipe_id':row['id'],'backend':row['recipe']['backend'],'failed_inputs':failures,
               'pairs':config['audit_pairs'],'epsilon':grammar.EPSILON,'upper':bound,'alpha':family['alpha'],'chunks':chunks}
        if bound<=grammar.EPSILON:entry['official']=official(root,row['artifact'])
        results[sha]=entry;native.save(root/'audit-results.json',results)
        print('audit',row['recipe']['backend'],sha[:12],failures,bound,entry.get('official',{}).get('S'),flush=True)
    passed=[{'sha256':sha,'S':item['official']['S'],'backend':item['backend'],'recipe':family['members'][sha]['recipe'],
             'name':family['members'][sha]['name'],'depth':family['members'][sha]['depth'],
             'changed_stream_depth':family['members'][sha].get('changed_stream_depth',0)}
            for sha,item in results.items() if item['upper']<=grammar.EPSILON and item.get('official',{}).get('passed')]
    winner=min(passed,key=lambda r:(r['S'],r['sha256'])) if passed else None
    controls=[row for row in passed if row['name'].startswith('baseline-')]
    outcome={'status':'complete','epsilon':grammar.EPSILON,'winner':winner,'audits':results,'qualified_controls':controls,
             'finished_unix':time.time(),'model_calls':0,'primary_metric':config['metric']}
    native.save(root/'result.json',outcome);native.save(root/'progress.json',{'stage':'complete','winner':winner})
    return outcome


def report(root):
    root=Path(root).resolve();config=verify_runtime(root)
    result=json.loads((root/'result.json').read_text());state=json.loads((root/'search-state.json').read_text())
    winner=result['winner'];rows=state['rows']
    lines=['# Native frontier campaign','',f"The cutoff is exactly {config['epsilon']}. The campaign evaluated complete 256-bit point addition.",'',
           f"The search recorded {len(rows)} proposals and baselines. It used no model calls.",
           f"The largest recorded edit depth was {max((row['depth'] for row in rows),default=0)}.",'']
    if winner:
        item=result['audits'][winner['sha256']];official_result=item['official']
        lines += [f"The best qualified circuit uses {winner['backend']}. Its official Q × T score is {winner['S']:,}.",
                  f"Q = {official_result['Q']}; rounded T = {official_result['T']:,}. All 9,024 official shots passed.",
                  f"The fresh audit found {item['failed_inputs']} failed inputs among {item['pairs']:,} pairs.",
                  f"The simultaneous one-sided bound is {item['upper']:.8g}, against the 0.001 cutoff.",'',
                  f"Operation SHA-256: `{winner['sha256']}`."]
        controls=result['qualified_controls']
        if controls:
            strongest=min(controls,key=lambda row:row['S'])
            gain=100*(1-winner['S']/strongest['S'])
            lines += ['',f"The gain over the strongest qualified imported baseline is {gain:.3f}% ({strongest['backend']}, S = {strongest['S']:,})."]
        previous=next((row for row in controls if row['backend']=='pingpong'),None)
        if previous:
            gain=100*(1-winner['S']/previous['S'])
            lines += [f"The gain over the previous PingPong control is {gain:.3f}% (S = {previous['S']:,})."]
        lines += [f"The winner's recorded edit depth is {winner['depth']}; its changed-stream depth is {winner['changed_stream_depth']}."]
    else:lines += ['No candidate passed both the qualification rule and the official local test.']
    lines += ['', 'See result.json, finalists.json, trials/, and official/ for the full evidence.',
              'Published source scores and imported baseline scores remain separate from search gains.','']
    path=root/'REPORT.md';path.write_text('\n'.join(lines));return path


def replay(root):
    root=Path(root).resolve();verify_runtime(root)
    result=json.loads((root/'result.json').read_text());winner=result['winner']
    if not winner:raise ValueError('This campaign has no qualified winner')
    # A fresh emission checks that the recipe reproduces the winning stream.
    directory=root/'replay';directory.mkdir(exist_ok=False)
    tool=integrity.read_sealed(root/'tools.json')['executables']['full-point-add-generator']
    process,receipt=native.command(root,[root/tool['file'],*grammar.native_arguments(winner['recipe']),'--out',directory/'ops.bin'],directory=directory,sandbox=True,timeout=None)
    if process.returncode:raise RuntimeError('Winning recipe did not emit')
    native.assert_hash(directory/'ops.bin',winner['sha256'])
    proof={'reproduced':True,'sha256':winner['sha256'],'recipe':winner['recipe'],'process':receipt}
    native.save(directory/'result.json',proof);return proof
