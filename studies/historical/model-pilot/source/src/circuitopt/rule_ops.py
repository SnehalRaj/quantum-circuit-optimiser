"""Conservative recipe and gate-region transformations with explicit boundaries."""
import copy
from itertools import product

OPERATORS={'direct_replay','measured_cleanup','proved_width','compact_scratch','jump2',
 'pingpong','leapfrog','boolean_factor','cache_and','commute','cancel','cut_rounds','fitted_width','window_compare','payload_program'}
GATES={'X':1,'Z':1,'CX':2,'CZ':2,'SWAP':2,'CCX':3,'CCZ':3,'HMR':1}


def _gate(name,*qs,condition=None,bit=None):
    out={'gate':name,'qubits':list(qs)}
    if condition is not None:out['condition']=condition
    if bit is not None:out['bit']=bit
    return out


def validate_region(r):
    if r.get('kind')!='gate_region' or type(r.get('qubits')) is not int or r['qubits']<1:raise ValueError('Expected a gate region')
    public,clean=r['public'],r['clean']
    if not isinstance(public,list) or not isinstance(clean,list) or len(set(public+clean))!=len(public+clean):raise ValueError('Public and clean wires must be distinct')
    if any(type(q) is not int or not 0<=q<r['qubits'] for q in public+clean):raise ValueError('Invalid wire')
    available=set();allowed=set(public+clean)
    for op in r['ops']:
        g=op['gate'];qs=op['qubits']
        if g not in GATES or len(qs)!=GATES[g] or len(set(qs))!=len(qs) or not set(qs)<=allowed:raise ValueError('Unsupported gate or alias')
        if any(type(q) is not int for q in qs):raise ValueError('Invalid wire type')
        cond=op.get('condition')
        if cond is not None and (type(cond) is not int or cond not in available):raise ValueError('Measurement condition is unavailable')
        if g=='HMR':
            if cond is not None or type(op.get('bit')) is not int or op['bit']<0 or op['bit'] in available:raise ValueError('Expected a new unconditional measurement bit')
            available.add(op['bit'])
    return r


def _writes(op):
    g=op['gate'];qs=op['qubits']
    if g in ['Z','CZ','CCZ']:return set()
    if g=='SWAP':return set(qs)
    return {qs[-1]}


def _unchanged(ops,qs):return not any(_writes(o)&set(qs) for o in ops)


def _region_change(name,r,p):
    validate_region(r);ops=r['ops'];i=p.get('index',0)
    if type(i) is not int or i<0:raise ValueError('Invalid gate index')
    if name=='compact_scratch':
        used=set(r['public']);mapping={q:q for q in r['public']}
        for q in sorted(r['clean']):
            low=0
            while low in used:low+=1
            used.add(low);mapping[q]=low
        for op in ops:op['qubits']=[mapping[q] for q in op['qubits']]
        r['clean']=[mapping[q] for q in r['clean']];r['qubits']=max(used,default=-1)+1
        if 'cache' in r:r['cache']={**r['cache'],'wire':mapping[r['cache']['wire']]}
    elif name in ['cancel','commute','boolean_factor']:
        if i+1>=len(ops):raise ValueError('Two adjacent gates are required')
        a,b=ops[i:i+2]
        if name=='cancel':
            if a!=b or a['gate']=='HMR':raise ValueError('Gates are not a matching inverse pair')
            ops[i:i+2]=[]
        elif name=='commute':
            if 'HMR' in [a['gate'],b['gate']] or _writes(a)&set(b['qubits']) or _writes(b)&set(a['qubits']):raise ValueError('Gate dependency prevents commutation')
            ops[i:i+2]=[b,a]
        else:
            if a['gate']!='CCX' or b['gate']!='CCX' or a.get('condition')!=b.get('condition'):raise ValueError('Expected compatible Toffolis')
            u,v=a['qubits'],b['qubits'];cond=a.get('condition')
            if set(u[:2])==set(v[:2]) and u[2]!=v[2] and not set(u[:2])&{u[2],v[2]}:
                ops[i:i+2]=[_gate('CX',u[2],v[2],condition=cond),a,_gate('CX',u[2],v[2],condition=cond)]
            elif u[2]==v[2] and len(set(u[:2])&set(v[:2]))==1 and len(set(u+v))==4:
                x=next(iter(set(u[:2])&set(v[:2])));y=next(q for q in u[:2] if q!=x);z=next(q for q in v[:2] if q!=x)
                ops[i:i+2]=[_gate('CX',z,y,condition=cond),_gate('CCX',x,y,u[2],condition=cond),_gate('CX',z,y,condition=cond)]
            else:raise ValueError('No supported Boolean factor pattern')
    elif name=='cache_and':
        action=p.get('action')
        if action=='introduce':
            controls=p.get('controls')
            if not isinstance(controls,list) or len(controls)!=2 or len(set(controls))!=2 or not set(controls)<=set(r['public']):raise ValueError('Provide two distinct public controls')
            if r.get('cache') or not _unchanged(ops,controls):raise ValueError('Cache controls must remain unchanged')
            a,b=controls;t=r['qubits'];r['qubits']+=1;r['clean'].append(t)
            r['ops']=[_gate('CCX',a,b,t)]+ops+[_gate('CCX',a,b,t)]
            r['cache']={'controls':controls,'wire':t}
        elif action=='reuse':
            cache=r.get('cache')
            if not cache or cache.get('measured'):raise ValueError('No live cached AND')
            a,b=cache['controls'];t=cache['wire'];hits=0
            expected=_gate('CCX',a,b,t)
            if t not in r['clean'] or not ops or ops[0]!=expected or ops[-1]!=expected:raise ValueError('Cache preparation and cleanup do not match')
            if not _unchanged(ops[1:-1],[a,b,t]):raise ValueError('Cache relation no longer holds')
            for j,op in enumerate(ops[1:-1],1):
                if op['gate']=='CCX' and set(op['qubits'][:2])=={a,b} and op['qubits'][2]!=t and op.get('condition') is None:
                    ops[j]=_gate('CX',t,op['qubits'][2]);hits+=1
            if not hits:raise ValueError('No matching uses of the cached AND')
        else:raise ValueError('Cache action is introduce or reuse')
    elif name=='measured_cleanup':
        found=False
        for start,first in enumerate(ops):
            if first['gate']!='CCX' or first.get('condition') is not None:continue
            a,b,t=first['qubits']
            if t not in r['clean'] or not _unchanged(ops[:start],[t]):continue
            for end in range(start+1,len(ops)):
                if ops[end]!=first:continue
                if not _unchanged(ops[start+1:end],[a,b,t]):continue
                bit=max([o['bit'] for o in ops if o['gate']=='HMR'],default=-1)+1
                ops[end:end+1]=[_gate('HMR',t,bit=bit),_gate('CZ',a,b,condition=bit)]
                if r.get('cache',{}).get('wire')==t:r['cache']['measured']=True
                found=True;break
            if found:break
        if not found:raise ValueError('No clean temporary AND with unchanged controls')
    else:raise ValueError('Rule does not apply to a gate region')
    validate_region(r)
    return r


def _validate_recipe(r):
    allowed={'kind','bits','prime','family','steps','cleanup','widths','compare_bits','block_program'}
    if not isinstance(r,dict) or not set(r)<=allowed:raise ValueError('Unknown recipe field')
    n,L,p=r.get('bits'),r.get('steps'),int(r.get('prime',0))
    if type(n) is not int or not 2<=n<=256 or type(L) is not int or L<1 or p.bit_length()!=n or p%2!=1:raise ValueError('Invalid arithmetic recipe')
    if r['cleanup'] not in ['toffoli','measured']:raise ValueError('Invalid cleanup')
    if r['family'] not in ['inverse','replay','width','jump2','pingpong','leapfrog']:raise ValueError('Unknown arithmetic family')
    if 'block_program' in r:
        from .block_program import validate
        validate(r['block_program'])
        if r['family'] not in ['inverse','replay','width']:raise ValueError('This family has no editable plain replay block')
    if r.get('widths') is not None:
        w=r['widths']
        if r['family']!='width' or not isinstance(w,list) or len(w)!=L+1 or w[0]!=n or any(type(x) is not int or not 1<=x<=n for x in w) or any(a<b for a,b in zip(w,w[1:])):raise ValueError('Invalid fitted width schedule')
    if r.get('compare_bits') is not None:
        k=r['compare_bits']
        if r['family']!='width' or type(k) is not int or not 1<=k<=n:raise ValueError('Invalid comparison window')
    return r


def apply_operator(name,parent,parameters):
    r=copy.deepcopy(parent);p=parameters
    allowed={'jump2':{'steps','allow_approximate_schedule'},'pingpong':{'steps','allow_approximate_schedule'},
      'leapfrog':{'steps','allow_approximate_schedule'},'boolean_factor':{'index'},'commute':{'index'},
      'cancel':{'index'},'cache_and':{'action','controls'},'cut_rounds':{'steps'},
      'fitted_width':{'widths'},'window_compare':{'compare_bits'},'payload_program':{'program'}}
    if not isinstance(p,dict) or not set(p)<=allowed.get(name,set()):raise ValueError('Unknown rule parameter')
    if r.get('kind')=='gate_region':return _region_change(name,r,p)
    _validate_recipe(r)
    if name=='payload_program':
        from .block_program import validate
        if r['family'] not in ['inverse','replay','width']:raise ValueError('This family has no editable payload program')
        r['block_program']=validate(p.get('program'))
    elif name=='direct_replay':
        if r['family']!='inverse':raise ValueError('Direct replay requires the inverse baseline')
        r['family']='replay'
    elif name=='measured_cleanup':r['cleanup']='measured'
    elif name=='proved_width':
        if r['family']!='replay':raise ValueError('Proved width requires the plain replay walk')
        r['family']='width'
    elif name in ['jump2','pingpong','leapfrog']:
        from .arithmetic import designs as D
        length={'jump2':D.jump2_length,'pingpong':D.pingpong_length,'leapfrog':D.leapfrog_length}[name]
        if r['bits']>16 and (p.get('allow_approximate_schedule') is not True or 'steps' not in p):raise ValueError('Full-width backend changes need an explicit approximate schedule')
        r['family']=name;r['steps']=p['steps'] if 'steps' in p else length(int(r['prime']))
        r.pop('widths',None);r.pop('compare_bits',None)
        r.pop('block_program',None)
    elif name=='cut_rounds':
        L=p.get('steps')
        if type(L) is not int or not 1<=L<r['steps']:raise ValueError('Round limit must be positive and smaller')
        r['steps']=L
        if r.get('widths') is not None:r['widths']=r['widths'][:L+1]
    elif name=='fitted_width':
        if r['family']!='width':raise ValueError('Fitted widths require the plain trimmed walk')
        r['widths']=copy.deepcopy(p.get('widths'))
        if r['widths'] is None:raise ValueError('Provide explicit fitted widths')
    elif name=='window_compare':
        if r['family']!='width':raise ValueError('Comparison window requires the plain trimmed walk')
        r['compare_bits']=p.get('compare_bits')
        if r['compare_bits'] is None:raise ValueError('Provide a comparison width')
    else:raise ValueError('Rule does not apply to this representation')
    return _validate_recipe(r)


def verify_regions(before,after):
    """Check every public basis input and every X-measurement record in small regions."""
    validate_region(before);validate_region(after)
    if before['public']!=after['public'] or len(before['public'])>8:raise ValueError('Use the same boundary and at most eight public wires')
    def outcomes(r,inputs):
        measured=[o for o in r['ops'] if o['gate']=='HMR']
        if len(measured)>8:raise ValueError('At most eight measurements for exhaustive checking')
        out=[]
        for results in product([0,1],repeat=len(measured)):
            state={q:0 for q in r['clean']};state.update(zip(r['public'],inputs));bits={};phase=0;mi=0
            for op in r['ops']:
                g=op['gate'];q=op['qubits']
                if op.get('condition') is not None and not bits[op['condition']]:continue
                if g=='X':state[q[0]]^=1
                elif g=='CX':state[q[1]]^=state[q[0]]
                elif g=='CCX':state[q[2]]^=state[q[0]]&state[q[1]]
                elif g=='SWAP':state[q[0]],state[q[1]]=state[q[1]],state[q[0]]
                elif g in ['Z','CZ','CCZ']:
                    v=1
                    for wire in q:v&=state[wire]
                    phase^=v
                elif g=='HMR':
                    m=results[mi];mi+=1;bits[op['bit']]=m;phase^=m&state[q[0]];state[q[0]]=0
            if any(state[q] for q in r['clean']):raise ValueError('Dirty scratch at the region boundary')
            out.append((tuple(state[q] for q in r['public']),phase))
        return out
    maximum=1
    for inputs in product([0,1],repeat=len(before['public'])):
        a,b=outcomes(before,inputs),outcomes(after,inputs);maximum=max(maximum,len(a),len(b))
        if len(set(a+b))!=1:raise ValueError('Boundary values or phases differ')
    return {'passed':True,'basis_inputs':2**len(before['public']),'measurement_branches':maximum,
            'scope':'Exact exhaustive boundary check at this small width; all X-measurement records included.'}
