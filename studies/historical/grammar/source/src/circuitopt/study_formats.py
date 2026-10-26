"""Compare reply schemas on matched, previously saved diagnostic packets."""
import json
from pathlib import Path
from . import engine,providers,rules,study,study_policy


def resolve(root,packet,proposal):
    if not isinstance(proposal,dict):raise ValueError('A proposal must be an object')
    parents={p['id']:p for p in packet['parents']}
    if proposal.get('parent_id') not in parents:raise ValueError('Parent was not in the packet')
    change=json.loads(proposal['changes_json'])
    if set(change)!={'rule_id','parameters'}:raise ValueError('Invalid rule change')
    config=study.check(root)
    if change['rule_id'] not in config['active_rules']:raise ValueError('Invalid rule change')
    parent=parents[proposal['parent_id']]
    result=rules.apply(Path(root)/'rules',change['rule_id'],parent['recipe'],change['parameters'])
    if not study_policy.admit(result['candidate'],config.get('proposal_policy','historical-v1')):
        raise ValueError('The candidate violates the recorded finite walk bounds')
    return parent,result


def compare_schemas(root,limit=6):
    root=Path(root);c=study.check(root);out=root/'schema-comparison';out.mkdir(exist_ok=False)
    sources=sorted((root/'runs').glob('5-llm-compact_json-*/model/*-packet.json'))[:limit]
    if not sources:raise ValueError('Run the live compact-JSON searches first')
    study.save(out/'plan.json',{'calls':len(sources),'comparison':'Reuse the recorded constrained reply; obtain one unconstrained reply for the same packet.',
       'performance':'Legal proposal fraction, useful proposal fraction, repair fraction, and proposal cost gain.',
       'scope':'A matched packet comparison; it supplies no extra independent search runs.'},True)
    rows=[]
    for i,source in enumerate(sources):
        packet=study.load(source);strict=study.load(source.with_name(source.name.replace('-packet.json','-response.json')))
        prompt=source.with_name(source.name.replace('-packet.json','-prompt.txt')).read_text()
        answer,receipt=providers.generate(c['provider'],c['model'],prompt,timeout=120,
                           reasoning_effort=c['reasoning_effort'],isolated=True,structured=False)
        study.save(out/f'{i}-unconstrained.json',{'answer':answer,'receipt':receipt,'source_packet':str(source.relative_to(root))},True)
        study.append(root/'costs.jsonl',{'kind':'model','stage':'schema_comparison',**receipt})
        for mode,response in [('constrained',strict),('unconstrained',{'answer':answer,'receipt':receipt})]:
            proposals=response['answer']['proposals'][:8];details=[];seen=set()
            for j,p in enumerate(proposals):
                try:
                    parent,change=resolve(root,packet,p);cid=engine.digest(change['candidate'])
                    if cid in seen:details.append({'valid':True,'duplicate':True,'useful':False});continue
                    seen.add(cid)
                    result=study.evaluate(root,change['candidate'],c['calibration']['5']['long_pairs'],
                            'schema-comparison/5',f'schema-{i}-{mode}-{j}','format_development')
                    passed=result['failed_inputs']==0;gain=100*(1-result['S']/parent['S'])
                    details.append({'valid':True,'duplicate':False,'useful':passed and (gain>0 or parent['failed_inputs']>0),
                       'repair':passed and parent['failed_inputs']>0,'gain_percent':gain,'receipt':result['evaluation_directory']})
                except (ValueError,KeyError,TypeError) as exc:details.append({'valid':False,'useful':False,'error':str(exc)})
            row={'packet':str(source.relative_to(root)),'mode':mode,'call_status':response['receipt']['status'],
                 'usage':response['receipt']['usage'],'wall_seconds':response['receipt']['wall_seconds'],
                 'proposals':len(proposals),'valid':sum(d['valid'] for d in details),
                 'useful':sum(d['useful'] for d in details),'repairs':sum(d.get('repair',False) for d in details),
                 'details':details}
            rows.append(row);study.save(out/'partial.json',rows)
    study.save(out/'results.json',rows,True)
    return rows
