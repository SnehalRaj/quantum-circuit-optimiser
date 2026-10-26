"""Render recorded search trajectories and a live local dashboard."""
import csv
import json
from pathlib import Path
import statistics
from . import engine,study

COLORS={'random':'#9A7D4A','evolution':'#257D70','llm':'#4869B2',
        'text':'#AA5B84','compact_json':'#4869B2','columnar_json':'#257D70'}


def write_dashboard(root):
    root=Path(root)
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Circuit search progress</title>
<style>body{font:16px system-ui,sans-serif;color:#18324b;background:#f5f7f9;margin:0}main{max-width:1120px;margin:32px auto;padding:24px}h1{font-size:32px;margin:0 0 8px}p{line-height:1.5}section{background:white;border:1px solid #dce4e8;border-radius:10px;padding:24px;margin-top:20px}select{padding:8px;border:1px solid #bdcbd3;border-radius:6px;margin:8px}svg{width:100%;height:auto}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:8px;border-bottom:1px solid #dce4e8}.muted{color:#536777}a{color:#257D70}</style>
<main><h1>Reversible circuit search</h1><p>Recorded development scores. Lower S is better. Final independent tests appear in REPORT.md.</p>
<p id="status">Load the study through the local HTTP server.</p><section>
<label>Arithmetic width <select id="bits"><option>5</option><option>16</option></select></label>
<label>Starting circuit <select id="baseline"><option>inverse</option><option>replay</option><option>width</option></select></label>
<svg id="plot" viewBox="0 0 1000 460" role="img" aria-label="Best development cost versus proposal number"></svg>
<p id="legend" class="muted"></p><p>Lines show the best long-tested score so far. Dots show improving proposals. Plateaus remain visible.</p></section>
<section><h2>Runs</h2><table><thead><tr><th>Run</th><th>Status</th><th>Proposals</th><th>Latest best S</th></tr></thead><tbody id="runs"></tbody></table></section>
<p class="muted">Refresh every four seconds. This page makes no model calls. Serve the repository with python -m http.server --bind 127.0.0.1.</p></main>
<script>
const ns='http://www.w3.org/2000/svg';const colors={random:'#9A7D4A',evolution:'#257D70',llm:'#4869B2',text:'#AA5B84',compact_json:'#4869B2',columnar_json:'#257D70'};
function node(tag,attrs,text){let n=document.createElementNS(ns,tag);for(let k in attrs)n.setAttribute(k,attrs[k]);if(text!==undefined)n.textContent=text;return n;}
async function refresh(){try{let index=await(await fetch('progress.json',{cache:'no-store'})).json();document.getElementById('status').textContent=index.stage;
let data=await Promise.all(index.runs.map(async r=>({...r,points:await(await fetch(r.trace,{cache:'no-store'})).json()})));
let bits=+document.getElementById('bits').value,bid=document.getElementById('baseline').value;
let shown=data.filter(r=>r.bits===bits).map(r=>({...r,points:r.points.filter(p=>p.baseline===bid)})).filter(r=>r.points.length);
let svg=document.getElementById('plot');svg.replaceChildren();if(!shown.length){svg.append(node('text',{x:80,y:120},'No completed baseline at this width yet.'));return;}
let maxX=Math.max(1,...shown.flatMap(r=>r.points.map(p=>p.trial))),maxY=Math.max(...shown.flatMap(r=>r.points.map(p=>p.best_S)))*1.04;
let X=x=>80+870*x/maxX,Y=y=>390-340*y/maxY;
svg.append(node('path',{d:'M80 35V390H950',stroke:'#80929d',fill:'none'}));
for(let i=0;i<=5;i++){let x=maxX*i/5,y=maxY*i/5;svg.append(node('text',{x:X(x),y:415,'text-anchor':'middle','font-size':14},Math.round(x)));svg.append(node('text',{x:68,y:Y(y)+5,'text-anchor':'end','font-size':13},Math.round(y).toLocaleString()));}
svg.append(node('text',{x:520,y:448,'text-anchor':'middle'},'Proposal number (trial 0 is the baseline)'));svg.append(node('text',{x:12,y:24},'S = Q × round(T)'));
let labels=new Set();for(let r of shown){let c=colors[r.strategy==='llm'?r.format:r.strategy];let d='';r.points.forEach((p,i)=>{d+=(i?'H'+X(p.trial)+'V'+Y(p.best_S):'M'+X(p.trial)+' '+Y(p.best_S));if(p.status==='improvement')svg.append(node('circle',{cx:X(p.trial),cy:Y(p.best_S),r:4,fill:c}));});svg.append(node('path',{d,stroke:c,fill:'none','stroke-width':2,opacity:.8}));labels.add(r.strategy==='llm'?'LLM / '+r.format:r.strategy);}
document.getElementById('legend').textContent=Array.from(labels).join(' · ')+'; each line is one search seed.';
let table=document.getElementById('runs');table.replaceChildren();for(let r of shown){let tr=document.createElement('tr');for(let v of [r.id,r.status,r.points.at(-1).trial,r.points.at(-1).best_S.toLocaleString()]){let td=document.createElement('td');td.textContent=v;tr.append(td);}table.append(tr);}
}catch(e){document.getElementById('status').textContent='Waiting for saved study records. '+e.message;}}
document.getElementById('bits').onchange=refresh;document.getElementById('baseline').onchange=refresh;refresh();setInterval(refresh,4000);
</script></html>'''
    (root/'dashboard.html').write_text(html)


def _curve(run,bid,budget):
    trace={p['trial']:p['best_S'] for p in run['trace'] if p['baseline']==bid}
    start=trace[0];last=start;out=[]
    for i in range(min(budget,max(trace))+1):last=trace.get(i,last);out.append(last/start)
    return out


def report(root):
    import os
    import tempfile
    os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'circuitopt-mpl-cache'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    root=Path(root);c=study.load(root/'study.json');out=root/'figures';out.mkdir(exist_ok=True)
    runs=[study.load(p) for p in sorted((root/'runs').glob('*/run.json'))]
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,
        'axes.spines.right':False,'axes.labelcolor':'#18324b','text.color':'#18324b','figure.facecolor':'white'})
    figures=[]
    def finish(fig,name):
        fig.savefig(out/(name+'.png'),dpi=180,bbox_inches='tight');fig.savefig(out/(name+'.pdf'),bbox_inches='tight');plt.close(fig);figures.append(name+'.png')
    if c['calibration']:
        fig,axes=plt.subplots(1,len(c['bits']),figsize=(10,3.4),squeeze=False)
        for ax,bits in zip(axes[0],c['bits']):
            cal=c['calibration'][str(bits)];b=list(cal['baselines']);ax.bar(b,[cal['baselines'][x]['S'] for x in b],color=['#A6AFB7','#6CA89E','#257D70']);ax.set_title(f'{bits}-bit starting circuits');ax.set_ylabel('S');ax.ticklabel_format(axis='y',style='sci',scilimits=(0,0))
        finish(fig,'baselines')
        fig,axes=plt.subplots(1,len(c['bits']),figsize=(10,3.5),squeeze=False)
        for ax,bits in zip(axes[0],c['bits']):
            cal=c['calibration'][str(bits)];sizes=list(cal['sizes']);x=np.arange(len(sizes))
            recall=[cal['sizes'][n]['held_out']['useful_recall'] for n in sizes]
            precision=[cal['sizes'][n]['held_out']['long_pass_precision'] for n in sizes]
            ax.plot(x,recall,'o-',label='Useful-candidate recall',color='#257D70');ax.plot(x,precision,'s-',label='Long-pass precision',color='#4869B2')
            ax.set(xticks=x,xticklabels=sizes,ylim=(0,1.05),xlabel='Quick inputs',title=f'{bits} bits; chosen: {cal["policy"]}');ax.legend(fontsize=8)
        finish(fig,'screen-calibration')
    for bits in c['bits']:
        for name,mode in [('offline','offline'),('comparison','comparison'),('formats','formats')]:
            selected=[r for r in runs if r['bits']==bits and ((r['strategy']!='llm' if mode=='offline' else r['strategy']=='llm' if mode=='formats' else r['format']=='compact_json'))]
            if not selected:continue
            fig,axes=plt.subplots(1,3,figsize=(12,3.6),sharey=True)
            labels=sorted({r['format'] if mode=='formats' else r['strategy'] for r in selected})
            for ax,bid in zip(axes,['inverse','replay','width']):
                for label in labels:
                    values=[_curve(r,bid,c['trials_per_baseline']) for r in selected if (r['format'] if mode=='formats' else r['strategy'])==label]
                    common=min(map(len,values));a=np.array([v[:common] for v in values]);x=np.arange(common);color=COLORS[label]
                    ax.step(x,np.median(a,axis=0),where='post',label=label,color=color,lw=2)
                    ax.fill_between(x,a.min(0),a.max(0),alpha=.12,color=color,step='post')
                ax.set(title=bid,xlabel='Proposal number',ylim=(0,1.05));ax.grid(alpha=.16)
            axes[0].set_ylabel('Best development S / starting S');axes[-1].legend(fontsize=8)
            fig.suptitle(f'{bits}-bit division: median and search-seed range; final audit is separate',y=1.04)
            finish(fig,f'{name}-{bits}')
        selected=[r for r in runs if r['bits']==bits and r['format']=='compact_json']
        if selected:
            fig,axes=plt.subplots(1,3,figsize=(12,3.5),sharey=True)
            for ax,bid in zip(axes,['inverse','replay','width']):
                for r in selected:
                    t=[p for p in r['trace'] if p['baseline']==bid];start=t[0]['best_S']
                    ax.step([p['wall_seconds'] for p in t],[p['best_S']/start for p in t],where='post',
                        color=COLORS[r['strategy']],alpha=.6,lw=1.3)
                ax.set(title=bid,xlabel='Elapsed run seconds',ylim=(0,1.05));ax.grid(alpha=.15)
            axes[0].set_ylabel('Best development S / starting S');finish(fig,f'wall-time-{bits}')
    rows=[]
    for r in runs:
        weights=c['calibration'][str(r['bits'])]['weights'];gains={};aucs={}
        for bid,state in r['baselines'].items():
            gains[bid]=100*(1-state['selected']['result']['S']/state['starting']['result']['S'])
            curve=_curve(r,bid,c['trials_per_baseline']);aucs[bid]=100*sum(1-v for v in curve[1:])/max(1,len(curve)-1)
        rows.append({'run':r['id'],'bits':r['bits'],'strategy':r['strategy'],'format':r['format'],'seed':r['seed'],
          'proposals':len(r['trials']),'unique_recipes':sum(len(s['archive']) for s in r['baselines'].values()),
          'development_reward':engine.reward([{'id':b,'weight':w} for b,w in weights.items()],gains),
          'proposal_auc':engine.reward([{'id':b,'weight':w} for b,w in weights.items()],aucs),
          'wall_seconds':r.get('wall_seconds',0),'status':r['status'],'model_calls':len(r['model_calls']),
          'reported_input_tokens':sum(x['usage'].get('input_tokens',0) or 0 for x in r['model_calls'] if x.get('usage')),
          'reported_output_tokens':sum(x['usage'].get('output_tokens',0) or 0 for x in r['model_calls'] if x.get('usage')),
          'reported_cached_input_tokens':sum(x['usage'].get('cached_input_tokens',0) or 0 for x in r['model_calls'] if x.get('usage')),
          'unknown_usage_calls':sum(not x.get('usage') or any(x['usage'].get(k) is None or k in x['usage'].get('incomplete_fields',[]) for k in ['input_tokens','output_tokens']) for x in r['model_calls']),
          'failed_model_calls':sum(x['status']!='completed' for x in r['model_calls'])})
    audit=study.load(root/'final/results.json') if (root/'final/results.json').exists() else None
    if audit:
        for row in rows:
            a=[x for x in audit['rows'] if x['run']==row['run']]
            row['audit_reward']=sum(x['weight']*x['gain_percent'] for x in a)/sum(x['weight'] for x in a)
            row['eligible_baselines']=sum(x['eligible'] for x in a)
        fig,axes=plt.subplots(1,len(c['bits']),figsize=(10,3.8),squeeze=False)
        for ax,bits in zip(axes[0],c['bits']):
            a=[r for r in rows if r['bits']==bits and r['format']=='compact_json'];groups=sorted({r['strategy'] for r in a})
            for i,g in enumerate(groups):
                vals=[r['audit_reward'] for r in a if r['strategy']==g];ax.scatter([i]*len(vals),vals,color=COLORS[g],s=36)
                ax.plot([i-.2,i+.2],[statistics.median(vals)]*2,color=COLORS[g],lw=3)
            ax.set(xticks=range(len(groups)),xticklabels=groups,ylabel='Audited weighted gain (%)',title=f'{bits} bits; epsilon={audit["epsilon"]}')
        finish(fig,'audit-reward')
    controls=[]
    if audit:
        for pair in audit.get('controls',[]):
            before,after=[audit['receipts'][pair[k]] for k in ['before','after']]
            controls.append({**pair,'before_T':before['T'],'after_T':after['T'],'before_Q':before['Q'],'after_Q':after['Q'],
               'before_S':before['S'],'after_S':after['S'],'observed_delta_T':after['T']-before['T'],
               'observed_delta_Q':after['Q']-before['Q'],'eligible':before['eligible'] and after['eligible'],
               'gain_percent':100*(1-after['S']/before['S'])})
        study.save(root/'cleanup-controls.json',controls)
    if rows:
        with (root/'results.csv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    # Show one preselected seed in absolute units, including failed low-cost proposals.
    for bits in c['bits']:
        example=next((r for r in runs if r['bits']==bits and r['strategy']=='evolution' and r['seed']==c['seeds'][0]),None)
        if not example:continue
        trace=[p for p in example['trace'] if p['baseline']=='inverse'];fig,ax=plt.subplots(figsize=(9,3.6))
        for status,color,marker in [('failed','#B76D43','x'),('long_pass','#98A6AE','.'),('improvement','#257D70','o')]:
            pts=[p for p in trace if p['status']==status and p['candidate_S'] is not None]
            ax.scatter([p['trial'] for p in pts],[p['candidate_S'] for p in pts],c=color,marker=marker,s=22,label=status)
        ax.step([p['trial'] for p in trace],[p['best_S'] for p in trace],where='post',lw=2,color='#257D70',label='best long-tested S')
        ax.scatter([0],[trace[0]['best_S']],s=70,marker='s',c='#18324b',label='baseline')
        ax.set(xlabel='Proposal number',ylabel='S = Q × round(T)',title=f'{bits}-bit division; evolution seed {c["seeds"][0]}');ax.legend(fontsize=8,ncol=3);ax.grid(alpha=.15)
        finish(fig,f'trajectory-{bits}')
    if runs:
        example=next((r for r in runs if r['strategy']=='evolution' and r['bits']==c['bits'][0] and r['seed']==c['seeds'][0]),runs[0])
        state=example['baselines']['inverse'];node=state['selected'];chain=[];seen=set()
        while node:
            if node['id'] in seen:raise ValueError('Cycle in the candidate archive')
            seen.add(node['id']);chain.append(node);node=state['archive'].get(node['parent_id'])
        chain.reverse();fig,ax=plt.subplots(figsize=(max(8,len(chain)*2.2),2.4));ax.axis('off')
        for i,node in enumerate(chain):
            label=('baseline' if i==0 else node['rule_history']['rule_id'])+'\n'+node['recipe']['family']+' / '+node['recipe']['cleanup']+f"\nS = {node['result']['S']:,}"
            ax.text(i,.5,label,ha='center',va='center',bbox={'boxstyle':'round,pad=.5','facecolor':'#EDF5F2','edgecolor':'#257D70'},fontsize=9)
            if i:ax.annotate('',xy=(i-.3,.5),xytext=(i-.7,.5),arrowprops={'arrowstyle':'->','color':'#18324b'})
        ax.set(xlim=(-.6,len(chain)-.4),ylim=(0,1));finish(fig,'lineage')
        study.save(root/'graph.json',[{'run':r['id'],'baseline':bid,'nodes':[{
            'id':e['id'],'parent_id':e['parent_id'],'recipe':e['recipe'],'S':e['result']['S'],
            'stage':e['result']['stage'],'failed_inputs':e['result']['failed_inputs'],'rule':e['rule_history']}
            for e in s['archive'].values()]} for r in runs for bid,s in r['baselines'].items()])
    schema=study.load(root/'schema-comparison/results.json') if (root/'schema-comparison/results.json').exists() else []
    schema_summary=[]
    if schema:
        for mode in ['constrained','unconstrained']:
            rs=[r for r in schema if r['mode']==mode];count=sum(r['proposals'] for r in rs)
            schema_summary.append({'mode':mode,'packets':len(rs),'completed':sum(r['call_status']=='completed' for r in rs),
               'proposals':count,'valid':sum(r['valid'] for r in rs),'useful':sum(r['useful'] for r in rs),'repairs':sum(r['repairs'] for r in rs),
               'reported_input_tokens':sum((r['usage'] or {}).get('input_tokens') or 0 for r in rs),
               'reported_output_tokens':sum((r['usage'] or {}).get('output_tokens') or 0 for r in rs),
               'unknown_usage_calls':sum(not r['usage'] or any(r['usage'].get(k) is None for k in ['input_tokens','output_tokens']) for r in rs)})
        fig,axes=plt.subplots(1,2,figsize=(10,3.5));x=np.arange(2)
        axes[0].bar(x-.18,[r['valid'] for r in schema_summary],.36,label='legal proposals',color='#4869B2')
        axes[0].bar(x+.18,[r['useful'] for r in schema_summary],.36,label='useful proposals',color='#257D70');axes[0].legend(fontsize=8)
        axes[1].bar(x,[r['reported_input_tokens'] for r in schema_summary],label='reported input',color='#4869B2')
        axes[1].bar(x,[r['reported_output_tokens'] for r in schema_summary],bottom=[r['reported_input_tokens'] for r in schema_summary],label='reported output',color='#C99856');axes[1].legend(fontsize=8)
        for ax in axes:ax.set(xticks=x,xticklabels=['schema','unconstrained'])
        axes[0].set_title('Matched packets: proposal outcomes');axes[1].set_title('Matched packets: reported tokens');finish(fig,'schema-comparison')
    profiles=[study.load(p) for p in (root/'runs').glob('*/model/*-profile.json')]
    if profiles:
        names=['text','compact_json','columnar_json'];means=[]
        for name in names:
            vals=[p['formats'][name]['reference_tokens'] for p in profiles if p['formats'][name]['reference_tokens'] is not None]
            means.append(statistics.mean(vals) if vals else float('nan'))
        fig,ax=plt.subplots(figsize=(7,3.3));ax.bar(names,means,color=[COLORS[n] for n in names]);ax.set(ylabel='Mean reference tokens',title='Same diagnostic packets in three encodings');finish(fig,'packet-tokens')
    transfer=study.load(root/'transfer/results.json') if (root/'transfer/results.json').exists() else None
    if transfer and 'receipts' in transfer:
        fig,axes=plt.subplots(1,2,figsize=(9,3.4));receipts=transfer['receipts']
        for ax,field in zip(axes,['T','S']):ax.bar(['before','after'],[receipts[k][field] for k in ['before','after']],color=['#9BAAB3','#257D70']);ax.set_title(f'{transfer["plan"]["bits"]}-bit division: {field}');ax.ticklabel_format(axis='y',style='sci',scilimits=(0,0))
        finish(fig,'transfer-256')
    costs=[json.loads(s) for s in (root/'costs.jsonl').read_text().splitlines()] if (root/'costs.jsonl').exists() else []
    cost_totals={kind:{'calls':sum(r['kind']==kind for r in costs),
        'wall_seconds':sum(r.get('wall_seconds',0) or 0 for r in costs if r['kind']==kind),
        'known_cpu_seconds':sum(r.get('cpu_seconds',0) or 0 for r in costs if r['kind']==kind),
        'unknown_cpu_calls':sum(r.get('cpu_seconds') is None for r in costs if r['kind']==kind)} for kind in ['evaluation','model','compilation','presentation']}
    study.save(root/'summary.json',{'runs':rows,'figures':figures,'audit_complete':bool(audit),
       'cleanup_controls':controls,'schema_comparison':schema_summary,'transfer':transfer,'costs':cost_totals,
       'scope':c['claim_limit'],'uncertainty':'Three search seeds are a pilot; ranges are not confidence intervals.'})
    lines=['# Circuit search study','',c['claim_limit'],'',
        'Every point comes from a saved trial. Curves show development results. Independent tests qualify only the frozen endpoints.',
        'The trial cap matches across strategies. Provider spending is unknown under subscription access.','',
        '[Open the live dashboard](dashboard.html). Serve this directory with `python -m http.server --bind 127.0.0.1`.','',
        '| Run | Development reward (%) | Observed proposal AUC | Audit reward (%) | Model calls | Reported input / output tokens | Unknown usage calls |',
        '|:--|--:|--:|--:|--:|--:|--:|']
    for r in rows:lines.append(f"| {r['run']} | {r['development_reward']:.3f} | {r['proposal_auc']:.3f} | {r.get('audit_reward','pending')} | {r['model_calls']} | {r['reported_input_tokens']} / {r['reported_output_tokens']} | {r['unknown_usage_calls']} |")
    lines += ['','## Measured cleanup controls','',
        'Each pair holds all other recipe fields fixed. Predictions come from the emitted cleanup count before fresh audit inputs exist.','',
        '| Bits | Predicted delta T | Observed delta T | Predicted / observed delta Q | Product gain | Eligible |','|--:|--:|--:|:--|--:|:--|']
    for p in controls:lines.append(f"| {p['bits']} | {p['predicted_delta_T']} | {p['observed_delta_T']} | {p['predicted_delta_Q']} / {p['observed_delta_Q']} | {p['gain_percent']:.3f}% | {p['eligible']} |")
    lines += ['','## Structured replies on matched packets','',
       '| Reply mode | Completed packets | Proposals | Legal | Useful | Repairs | Unknown usage calls |','|:--|--:|--:|--:|--:|--:|--:|']
    for r in schema_summary:lines.append(f"| {r['mode']} | {r['completed']}/{r['packets']} | {r['proposals']} | {r['valid']} | {r['useful']} | {r['repairs']} | {r['unknown_usage_calls']} |")
    lines += ['','The constrained replies reuse recorded calls. The unconstrained replies add one call per saved packet.','',
              '## Full-width transfer','']
    if transfer and 'receipts' in transfer:
        lines += [transfer['plan']['scope'],'',
            f"Observed product reduction: {transfer['gain_percent']:.3f}%. Failure upper bound: {transfer['epsilon_upper']:.6g}.",'',
            '| Circuit | T | Q | S | Failed inputs |','|:--|--:|--:|--:|--:|']
        for name,r in transfer['receipts'].items():lines.append(f"| {name} | {r['T']} | {r['Q']} | {r['S']} | {r['failed_inputs']}/{r['pairs']} |")
    elif transfer:lines.append(transfer['scope'])
    else:lines.append('Transfer has not run.')
    for name in figures:lines += ['',f'![{name}](figures/{name})']
    lines += ['','Read study.json for the frozen protocol, calibration/ for screen evidence, and runs/ for each candidate archive.',
              'The costs.jsonl file counts each evaluator and model call. Per-run proposal timings include evaluator time; do not add both totals.',
              'Neutral and expanding valid candidates remain available. Failed candidates and duplicates remain in the proposal count.','']
    (root/'REPORT.md').write_text('\n'.join(lines));write_dashboard(root)
    return rows
