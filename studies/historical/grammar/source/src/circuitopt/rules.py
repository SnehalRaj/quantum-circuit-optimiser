"""Editable rules, immutable evidence, and context-specific contribution scores."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import statistics
from datetime import datetime, timezone

DATA=Path(__file__).parent/'data/rules'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def card_hash(card):
    return hashlib.sha256(canonical(card).encode()).hexdigest()


def _save(path,value,exclusive=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x' if exclusive else 'w') as f:f.write(json.dumps(value,indent=2,allow_nan=False)+'\n')


def _identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'R[0-9]{2,4}',value):
        raise ValueError('Rule IDs use R followed by two to four digits')


def _validate(card):
    from .rule_ops import OPERATORS
    _identifier(card['id'])
    if type(card['version']) is not int or card['version']<1:raise ValueError('Invalid rule version')
    if type(card['priority']) is not int or not 0<=card['priority']<=100:raise ValueError('Priority must be 0 through 100')
    if type(card['enabled']) is not bool:raise ValueError('enabled must be Boolean')
    if card['status'] not in ['implemented','proposal']:raise ValueError('Unknown implementation status')
    if card.get('operator') is not None and card['operator'] not in OPERATORS:raise ValueError('Unknown rule implementation')
    if card['status']=='implemented' and card.get('operator') is None:raise ValueError('Missing rule implementation')
    for key in ['name','reasoning']:
        if not isinstance(card[key],str) or not card[key].strip():raise ValueError(f'Empty {key}')
    for key in ['conditions','source_urls','seed_evidence']:
        if not isinstance(card[key],list):raise ValueError(f'{key} must be a list')
    if card['correctness'] not in ['exact_identity','exact_under_conditions','approximate']:raise ValueError('Invalid correctness label')
    return card


def semantic_hash(card):
    return card_hash({k:card.get(k) for k in ['operator','correctness','conditions','parameters','pattern','replacement']})


def initialise(directory):
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    if (root/'cards').exists():raise FileExistsError('Rule library already exists')
    shutil.copytree(DATA/'evidence',root/'evidence')
    shutil.copy2(DATA/'evidence-manifest.json',root/'evidence-manifest.json')
    for card in json.loads((DATA/'cards.json').read_text()):
        _validate(card);_save(root/'cards'/f"{card['id']}.json",card,True)
        _save(root/'revisions'/card['id']/'1.json',card,True)
    _save(root/'library.json',{'schema':1,'rule_count':20,'stage':'task_2','initial_priority':'Editable judgement; separate from measured contribution.'},True)
    (root/'events.jsonl').touch(exist_ok=False)
    return cards(root)


def cards(directory):
    result=[_validate(json.loads(p.read_text())) for p in sorted((Path(directory)/'cards').glob('*.json'))]
    if not result:raise ValueError('No rule cards found; initialise a library first')
    if len({r['id'] for r in result})!=len(result):raise ValueError('Duplicate rule ID')
    return sorted(result,key=lambda r:(-r['priority'],r['id']))


def _get(directory,rid):
    _identifier(rid)
    return next((r for r in cards(directory) if r['id']==rid),None) or _missing(rid)


def _missing(rid):raise ValueError(f'Unknown rule: {rid}')


def update(directory,rid,patch):
    root=Path(directory);old=_get(root,rid)
    allowed=set(old)-{'id','version'}
    if not isinstance(patch,dict) or not set(patch)<=allowed|{'note'}:raise ValueError('Unknown or immutable rule field')
    revised=_validate({**old,**copy.deepcopy(patch),'version':old['version']+1})
    _save(root/'revisions'/rid/f"{revised['version']}.json",revised,True)
    _save(root/'cards'/f'{rid}.json',revised)
    return revised


def add(directory,card):
    root=Path(directory);card=_validate(copy.deepcopy(card))
    if any(r['id']==card['id'] for r in cards(root)):raise ValueError('Duplicate rule ID')
    if card['version']!=1:raise ValueError('A new card starts at version 1')
    _save(root/'revisions'/card['id']/'1.json',card,True)
    _save(root/'cards'/f"{card['id']}.json",card,True)
    return card


def apply(directory,rid,parent,parameters=None):
    from .rule_ops import apply_operator
    card=_get(directory,rid)
    if not card['enabled']:raise ValueError('Rule is disabled')
    if card['status']!='implemented' or card.get('operator') is None:raise ValueError('Rule implementation is not available')
    parameters=copy.deepcopy(parameters or {})
    candidate=apply_operator(card['operator'],copy.deepcopy(parent),parameters)
    if canonical(candidate)==canonical(parent):raise ValueError('Rule produced no change')
    return {'candidate':candidate,'history':{'rule_id':rid,'rule_version':card['version'],
        'rule_sha256':card_hash(card),'parameters':parameters,'parent_sha256':card_hash(parent),
        'candidate_sha256':card_hash(candidate),'correctness':card['correctness'],
        'evidence_status':'Candidate needs its boundary checks and evaluation'},'rule':card}


def _receipt(r):
    if not isinstance(r,dict):raise ValueError('Receipt must be an object')
    for k in ['Q','pairs','streams','failed_inputs']:
        if type(r.get(k)) is not int:raise ValueError(f'Invalid {k}')
    if r['Q']<1 or r['pairs']<1 or r['streams']!=2 or not 0<=r['failed_inputs']<=r['pairs']:raise ValueError('Invalid counts')
    if isinstance(r.get('T'),bool) or not isinstance(r.get('T'),(int,float)) or not math.isfinite(r['T']) or r['T']<0:raise ValueError('Invalid T')
    ch=r.get('channel_failed_inputs',{})
    if set(ch)!={'output','phase','scratch'} or any(type(x) is not int or not 0<=x<=r['failed_inputs'] for x in ch.values()):raise ValueError('Invalid failure channels')
    if sum(ch.values())<r['failed_inputs']:raise ValueError('Inconsistent failure channels')
    for k in ['input_sha256','operation_sha256']:
        if not isinstance(r.get(k),str) or not re.fullmatch('[0-9a-f]{64}',r[k]):raise ValueError(f'Invalid {k}')
    if not isinstance(r.get('label'),str) or not r['label']:raise ValueError('Missing input label')
    expected=r['Q']*math.floor(r['T']+.5)
    if 'S' in r and r['S']!=expected:raise ValueError('Receipt S differs from rounded T times Q')
    return expected


def _append(directory,event):
    root=Path(directory);e=copy.deepcopy(event)
    if not isinstance(e.get('id'),str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*',e['id']):raise ValueError('Invalid event ID')
    if (root/'events.jsonl').exists():
        if any(json.loads(s)['id']==e['id'] for s in (root/'events.jsonl').read_text().splitlines() if s):raise ValueError('Duplicate event ID')
    if e.get('kind') not in ['paired','joint','enabling']:raise ValueError('Unknown attribution kind')
    ids=e.get('rule_ids',[])
    if not ids or len(set(ids))!=len(ids):raise ValueError('Provide unique rule IDs')
    if len(ids)>1 and e['kind']=='paired':raise ValueError('Multiple rules require joint or enabling attribution')
    before,after=_receipt(e['before']),_receipt(e['after'])
    if before<=0:raise ValueError('Starting cost must be positive')
    for key in ['label','input_sha256','pairs','streams']:
        if e['before'][key]!=e['after'][key]:raise ValueError(f'Unmatched input protocol: {key}')
    if type(e.get('bits')) is not int or e['bits']<1 or not e.get('interface') or not e.get('baseline_id'):raise ValueError('Missing comparison context')
    weight=e.get('weight',1)
    if isinstance(weight,bool) or not isinstance(weight,(int,float)) or not math.isfinite(weight) or weight<=0:raise ValueError('Invalid baseline weight')
    e['raw_gain_percent']=round(100*(1-after/before),12)
    e['checks_passed']=not(e['before']['failed_inputs'] or e['after']['failed_inputs'])
    e['credited_gain_percent']=e['raw_gain_percent'] if e['checks_passed'] else 0.0
    e['recorded_utc']=datetime.now(timezone.utc).isoformat()
    e['rule_snapshots']=[]
    for rid in ids:
        card=_get(root,rid);sha=card_hash(card)
        target=root/'snapshots'/f'{sha}.json'
        if not target.exists():_save(target,card,True)
        e['rule_snapshots'].append({'id':rid,'version':card['version'],'card_sha256':sha,'semantic_sha256':semantic_hash(card)})
    _save(root/'observations'/f"{e['id']}.json",e,True)
    with (root/'events.jsonl').open('a') as f:f.write(canonical(e)+'\n')
    return e


def record(directory,event):
    if event.get('stage')!='development':raise ValueError('Use import-audit with a verified audit bundle for audit evidence')
    return _append(directory,event)


def _audit_bundle(audit_directory):
    """Verify the retained bundle against its inventory and frozen circuit map."""
    import gzip
    from . import engine
    root=Path(audit_directory).resolve()
    hashes=json.loads((root/'files.sha256.json').read_text())
    def checked(name,expected=None):
        p=(root/name).resolve()
        if not p.is_relative_to(root) or not p.is_file():raise ValueError('Missing audit artifact')
        actual=hashlib.sha256(p.read_bytes()).hexdigest()
        if name not in hashes or actual!=hashes[name] or (expected is not None and actual!=expected):raise ValueError('Audit artifact checksum differs')
        return p
    for name,expected in hashes.items():
        checked(name,expected)
    for name in ['manifest.json','manifest.sha256','consumed.json','results.json']:
        if name not in hashes:raise ValueError('Incomplete audit evidence')
    manifest=json.loads((root/'manifest.json').read_text())
    if engine.digest(manifest)!=(root/'manifest.sha256').read_text().strip():raise ValueError('Audit manifest checksum differs')
    if not manifest.get('circuit_files'):raise ValueError('Missing frozen circuit inventory')
    for name,expected in manifest['circuit_files'].items():checked(name,expected)
    results=json.loads((root/'results.json').read_text())
    if results.get('status')!='audited':raise ValueError('Audit is incomplete')
    consumed=json.loads((root/'consumed.json').read_text())
    if consumed.get('status')!='consumed_before_evaluation' or not consumed.get('label'):raise ValueError('Missing consumed audit label')
    for index,run in enumerate(manifest['runs']):
        engine.validate(run['contract'])
        for bid,state in run['baselines'].items():
            for kind,frozen in [('baseline',state['baseline']['recipe']),('candidate',state['selected']['recipe'])]:
                receipt=json.loads(checked(f'evaluations/{index}-{bid}-{kind}/receipt.json').read_text())
                _receipt(receipt)
                if receipt['recipe']!=frozen:raise ValueError('Receipt recipe differs from the frozen recipe')
                if receipt['label']!=consumed['label'] or receipt['pairs']!=manifest['pairs']:raise ValueError('Receipt differs from the frozen input protocol')
                expected=state['starting'] if kind=='baseline' else state['selected']['result']
                if receipt['operation_sha256']!=expected['operation_sha256']:raise ValueError('Frozen circuit differs')
                inputs=checked(receipt['inputs_file']);digest=hashlib.sha256()
                opener=gzip.open if inputs.suffix=='.gz' else open
                with opener(inputs,'rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
                if digest.hexdigest()!=receipt['input_sha256']:raise ValueError('Saved audit inputs differ')
                prefix=receipt['circuit_directory']
                meta_name=f'{prefix}/circuit.json';gate_name=f'{prefix}/gates.jsonl.gz'
                if meta_name not in manifest['circuit_files'] or gate_name not in manifest['circuit_files']:raise ValueError('Receipt circuit is outside the frozen circuit map')
                meta=json.loads(checked(meta_name).read_text())
                checked(gate_name,meta['file_sha256'])
                if meta['recipe']!=frozen or meta['operation_sha256']!=receipt['operation_sha256']:raise ValueError('Saved circuit metadata differs')
    return root,hashes,manifest


def import_audit(directory,audit_directory,run_index,baseline_id,rid,parameters=None):
    """Bind a single-rule ablation to the existing frozen audit and saved files."""
    from . import engine
    root,hashes,manifest=_audit_bundle(audit_directory)
    if type(run_index) is not int or not 0<=run_index<len(manifest['runs']):raise ValueError('Invalid audit run')
    run=manifest['runs'][run_index];contract=run['contract']
    engine.validate(contract)
    baseline=next(b for b in contract['baselines'] if b['id']==baseline_id)
    before=json.loads((root/f'evaluations/{run_index}-{baseline_id}-baseline/receipt.json').read_text())
    after=json.loads((root/f'evaluations/{run_index}-{baseline_id}-candidate/receipt.json').read_text())
    for kind in ['baseline','candidate']:
        if f'evaluations/{run_index}-{baseline_id}-{kind}/receipt.json' not in hashes:raise ValueError('Audit receipt is not covered by its manifest')
    changed=apply(directory,rid,before['recipe'],parameters)['candidate']
    if changed!=after['recipe']:raise ValueError('Audit candidate is not this single-rule change')
    selected=run['baselines'][baseline_id]
    if before['operation_sha256']!=selected['starting']['operation_sha256'] or after['operation_sha256']!=selected['selected']['result']['operation_sha256']:raise ValueError('Frozen circuit differs')
    alpha=contract.get('alpha',.05)/manifest['comparison_size']
    eligible=all(engine.upper(r['failed_inputs'],r['pairs'],alpha)<=contract['epsilon'] for r in [before,after])
    event={'id':'audit-'+card_hash([hashes,run_index,baseline_id,rid])[:24],
       'kind':'paired','stage':'audit','rule_ids':[rid],'parameters':parameters or {},
       'interface':contract['interface'],'bits':before['recipe']['bits'],'baseline_id':baseline_id,
       'before':before,'after':after,'weight':baseline['weight'],'eligible':eligible,
       'epsilon':contract['epsilon'],'contract_sha256':engine.digest(contract),
       'baseline_weights':{b['id']:b['weight'] for b in contract['baselines']},
       'audit_run':run_index,
       'audit_manifest_sha256':hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()}
    # Keep the frozen inputs, circuits, and audit bindings with the library.
    target=Path(directory)/'audit-evidence'/event['id']
    if target.exists():raise ValueError('Duplicate audit import')
    shutil.copytree(root,target)
    try:return _append(directory,event)
    except Exception:
        shutil.rmtree(target)
        raise


def _gain(e):
    if e['metric']=='TQ':
        before=e['before']['Q']*math.floor(e['before']['T']+.5)
        after=e['after']['Q']*math.floor(e['after']['T']+.5)
    else:before,after=e['before_value'],e['after_value']
    return 100*(1-after/before)


def scores(directory):
    root=Path(directory);out=[]
    events=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines() if x]
    for e in events:
        if e['stage']=='audit':
            bundle=root/'audit-evidence'/e['id'];_audit_bundle(bundle)
            if hashlib.sha256((bundle/'manifest.json').read_bytes()).hexdigest()!=e['audit_manifest_sha256']:raise ValueError('Retained audit manifest differs')
            for side,kind in [('before','baseline'),('after','candidate')]:
                saved=json.loads((bundle/f"evaluations/{e['audit_run']}-{e['baseline_id']}-{kind}/receipt.json").read_text())
                if saved!=e[side]:raise ValueError('Audit event differs from retained receipt')
    joint=[]
    for card in cards(root):
        seeds={}
        original=json.loads((root/'revisions'/card['id']/'1.json').read_text())
        original_semantics=semantic_hash(original)
        for e in card['seed_evidence']:
            if 'file' in e:
                path=(root/e['file']).resolve()
                if not path.is_relative_to(root.resolve()):raise ValueError('Seed evidence must stay inside the library')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=e['file_sha256']:raise ValueError('Seed evidence checksum differs')
                source=json.loads(path.read_text())
                for side in ['before','after']:
                    node=source
                    for component in e[side+'_path']:node=node[component]
                    if e[side]!={'T':node[e[side+'_T_field']],'Q':node['Q']}:raise ValueError('Seed values differ from saved evidence')
                    if side=='after':
                        failure=node.get('failed_inputs',node.get('failing_inputs',node.get('failures')))
                        if failure!=e['failed_inputs']:raise ValueError('Seed failure count differs')
            key=f"{e['scope']}/{e['bits']}/{e['metric']}"
            seed_semantics=e.get('rule_semantic_sha256',original_semantics)
            seeds.setdefault(seed_semantics,{}).setdefault(key,[]).append(e)
        all_initial={sem:{key:{'median_cost_gain_percent':statistics.median(_gain(e) for e in es),
            'comparisons':len(es),'comparisons_with_observed_failures':sum(bool(e.get('failed_inputs')) for e in es),
            'qualification':'Descriptive seed evidence; retains its source scope.'} for key,es in contexts.items()} for sem,contexts in seeds.items()}
        initial=all_initial.get(semantic_hash(card),{})
        historical=[e for e in events if e['rule_ids']==[card['id']] and e['kind']=='paired' and e['stage']=='development']
        version_scores={}
        for e in historical:
            key=f"{e['interface']}/{e['bits']}/development"
            version_scores.setdefault(str(e['rule_snapshots'][0]['version']),{}).setdefault(key,[]).append(e['credited_gain_percent'])
        version_scores={v:{k:{'mean_gain_percent':sum(gs)/len(gs),'trials':len(gs)} for k,gs in cs.items()} for v,cs in version_scores.items()}
        observed=[e for e in historical if e['rule_snapshots'][0]['semantic_sha256']==semantic_hash(card)]
        contexts={}
        for e in observed:
            key=f"{e['interface']}/{e['bits']}/development"
            contexts.setdefault(key,[]).append(e)
        means={k:sum(e['credited_gain_percent'] for e in es)/len(es) for k,es in contexts.items()}
        audits=[e for e in events if e['rule_ids']==[card['id']] and e['kind']=='paired' and e['stage']=='audit'
                and e['rule_snapshots'][0]['semantic_sha256']==semantic_hash(card)]
        contracts={}
        for e in audits:contracts.setdefault(e['contract_sha256'],[]).append(e)
        audit_scores={}
        for sha,es in contracts.items():
            weights=es[0]['baseline_weights'];gains={}
            for bid in weights:
                trials=[e['raw_gain_percent'] if e['eligible'] else 0 for e in es if e['baseline_id']==bid]
                gains[bid]=sum(trials)/len(trials) if trials else 0
            audit_scores[sha]={'score_percent':sum(weights[b]*gains[b] for b in weights)/sum(weights.values()),
                'baselines_measured':len({e['baseline_id'] for e in es}),'baselines_total':len(weights),
                'epsilon':es[0]['epsilon'],'per_baseline_gain_percent':gains}
        out.append({'id':card['id'],'name':card['name'],'version':card['version'],'priority':card['priority'],
           'enabled':card['enabled'],'status':card['status'],'initial':initial,'initial_by_semantic':all_initial,'context_scores':means,
           'observed_gain_percent':next(iter(means.values())) if len(means)==1 else None,
           'version_scores':version_scores,'trials':len(observed),'failed_trials':sum(not e['checks_passed'] for e in observed),
           'audit_scores':audit_scores,'enabling_paths':sum(e['kind']=='enabling' and e['checks_passed'] and e['raw_gain_percent']>0
               and any(s['id']==card['id'] and s['semantic_sha256']==semantic_hash(card) for s in e['rule_snapshots']) for e in events)})
    groups={}
    for e in events:
        semantics=tuple(sorted((s['id'],s['semantic_sha256']) for s in e['rule_snapshots']))
        if e['kind']=='joint':groups.setdefault((semantics,e['interface'],e['bits'],e['stage']),[]).append(e)
    current={c['id']:semantic_hash(c) for c in cards(root)}
    for (semantics,interface,bits,stage),es in groups.items():
        joint.append({'rule_ids':[rid for rid,_ in semantics],'rule_semantic_sha256':dict(semantics),
            'current_rules':all(current[rid]==sha for rid,sha in semantics),'interface':interface,'bits':bits,'stage':stage,
            'trials':len(es),'mean_gain_percent':sum(e['credited_gain_percent'] for e in es)/len(es)})
    return {'rules':out,'joint':joint,'note':'Enabling paths are associations. Joint gains receive no individual credit. Contexts retain separate scores.'}


def report(directory):
    root=Path(directory);result=scores(root)
    lines=['# Rule library','', 'Priority is an editable judgement. Initial gains retain the evidence scope.','',
       '| Rule | Priority | Support | Initial 16-bit division gain | Initial 256-bit division gain | Development trials | Enabling paths |',
       '|:--|--:|:--|--:|--:|--:|--:|']
    for r in result['rules']:
        def initial(bits):
            x=r['initial'].get(f'modular_division/{bits}/TQ')
            return f"{x['median_cost_gain_percent']:.3f}%" if x else 'unknown'
        lines.append(f"| {r['id']} {r['name']} | {r['priority']} | {r['status']} | {initial(16)} | {initial(256)} | {r['trials']} | {r['enabling_paths']} |")
    lines.extend(['','Read scores.json for point-addition and cell evidence, failures, contexts, and qualified audit scores.',
        'The sixteen-bit and full-width division figures stay separate. Proposed rules cannot be applied.',
        'A cell-level gate saving supplies no full-circuit product estimate.','',
        '| Rule | Other seed scope | Metric | Median cost gain | Comparisons with observed failures |',
        '|:--|:--|:--|--:|--:|'])
    for r in result['rules']:
        for context,x in r['initial'].items():
            scope,bits,metric=context.split('/')
            if scope!='modular_division':
                lines.append(f"| {r['id']} | {scope}, {bits} bits | {metric} | {x['median_cost_gain_percent']:.3f}% | {x['comparisons_with_observed_failures']}/{x['comparisons']} |")
    lines.extend(['','## Failure evidence','',
        'A raw cost reduction can accompany failures. Such a reduction supplies no eligible gain.',''])
    for r in result['rules']:
        for context,x in r['initial'].items():
            if x['comparisons_with_observed_failures']:
                lines.append(f"- {r['id']}, {context}: {x['comparisons_with_observed_failures']}/{x['comparisons']} seed comparisons have observed failures.")
    lines.append('')
    _save(root/'scores.json',result);(root/'RULES.md').write_text('\n'.join(lines))
    return result
