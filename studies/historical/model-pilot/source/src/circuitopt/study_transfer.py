"""Apply a selected exact cleanup mechanism to full-width modular division."""
import gzip
import hashlib
import json
from pathlib import Path
import secrets
from . import division,engine,search_space,study


def run(root,bits=256,pairs=32):
    root=Path(root);config=study.check(root);out=root/'transfer';out.mkdir(exist_ok=False)
    searches=[study.load(p) for p in sorted((root/'runs').glob('*/run.json'))]
    sources=[{'run':r['id'],'baseline':bid,'candidate':s['selected']['id']} for r in searches
             for bid,s in r['baselines'].items() if s['selected']['recipe']['cleanup']=='measured']
    source=None
    if config.get('grammar')=='blocks-v1':
        from .study_grammar import transfer_source
        source=transfer_source(study.load(root/'final/results.json'))
        sources=source['sources'] if source else []
    if not sources:
        result={'status':'no_supported_transfer','sources':[],'scope':'Search selected no supported cleanup mechanism.'}
        study.save(out/'results.json',result,True)
        return result
    before=search_space.baseline_recipes(bits)['width'];after={**before,'cleanup':'measured'}
    plan={'rule':'R02','bits':bits,'pairs':pairs,'sources':sources,
      'question':'Does the selected measured-cleanup rule preserve its saving at full width?',
      'invariant':'A temporary wire equals the AND of retained controls; correct every X-measurement sign.',
      'prediction':'Remove one unconditional Toffoli per temporary AND cleanup. Preserve Q.',
      'cheapest_disproof':'Wrong output, phase, or scratch on a small full-width sample.',
      'acceptance':'The sample passes and S falls. Reliability qualification remains separate.',
      'scope':'256-bit modular division development; no complete point-addition reliability claim.'}
    label='transfer-256-development-v1'
    if source:
        before={**search_space.baseline_recipes(bits)['width'],'cleanup':'measured'}
        after={**before,'block_program':source['program']}
        label='block-transfer-development/'+secrets.token_hex(32)
        plan.update(rule='R21',before=before,after=after,source_recipe=source['source_recipe'],
            selection=source['selection'],
            question='Does the audited payload program reduce full-width division cost?',
            invariant='The proved seed program preserves the payload map, controls, scratch, and phase correction.',
            prediction='Direct constant carry chains reduce constant-register allocation. Measure global Q and T.',
            width_policy='Keep the original full-width walk bound, round count, and measured cleanup fixed.',
            transferred='The complete audited block program and its constant lowerings.')
    plan['label']=label
    study.save(out/'plan.json',plan,True)
    # Count the emitted instructions before inspecting the sampled outcomes.
    predictions={}
    for name,recipe in [('before',before),('after',after)]:
        path=out/'circuits'/engine.digest(recipe);meta=division.export_circuit(recipe,path)
        counts={'unconditional_Toffolis':0,'conditional_Toffolis':0,'measurements':0};maximum=-1
        with gzip.open(path/'gates.jsonl.gz','rt') as f:
            for line in f:
                gate,qs,condition,operation_label=json.loads(line)
                maximum=max(maximum,*qs[:1] if gate=='HMR' else qs)
                if gate in ['CCX','CCZ']:counts['unconditional_Toffolis' if condition is None else 'conditional_Toffolis']+=1
                if gate=='HMR':counts['measurements']+=1
        predictions[name]={'Q':maximum+1,**counts,'operation_sha256':meta['operation_sha256']}
    study.save(out/'prediction.json',predictions,True)
    receipts={}
    for name,recipe in [('before',before),('after',after)]:
        receipts[name]=study.evaluate(root,recipe,pairs,label,'transfer-'+name,
                       'transfer_development',timeout=1800,circuit_directory=out/'circuits'/engine.digest(recipe))
    result={'plan':plan,'prediction':predictions,'receipts':receipts,
       'gain_percent':100*(1-receipts['after']['S']/receipts['before']['S']),
       'epsilon_upper':engine.upper(receipts['after']['failed_inputs'],pairs,.05)}
    study.save(out/'results.json',result,True)
    return result
