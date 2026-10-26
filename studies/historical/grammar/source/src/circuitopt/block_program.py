"""Editable arithmetic programs with explicit lowering and register contracts."""
import copy
from contextlib import contextmanager
from itertools import product
from .arithmetic import arith as A

OPS = {'swap', 'sub', 'add', 'half', 'double', 'half_u', 'double_u'}
INVERSE = {'swap':'swap', 'sub':'add', 'add':'sub', 'half':'double',
           'double':'half', 'half_u':'double_u', 'double_u':'half_u'}
DEFAULT = {'ops':[{'op':op, 'lowering':'legacy'} for op in ['swap','sub','half']]}


def validate(program):
    if not isinstance(program,dict) or set(program)!={'ops'}:
        raise ValueError('A block program contains only its ordered ops')
    ops=program['ops']
    if not isinstance(ops,list) or not 1<=len(ops)<=8:
        raise ValueError('A block program needs one to eight operations')
    out=[]
    for op in ops:
        if not isinstance(op,dict) or not set(op)<={'op','lowering'} or op.get('op') not in OPS:
            raise ValueError('Unsupported block operation')
        lowering=op.get('lowering','legacy')
        if lowering not in ['legacy','direct']:raise ValueError('Unsupported constant lowering')
        out.append({'op':op['op'],'lowering':lowering})
    return {'ops':out}


@contextmanager
def lowering(c,mode):
    previous=getattr(c,'constant_lowering','legacy')
    c.constant_lowering=mode
    try:yield
    finally:c.constant_lowering=previous


def _restore_layout(c,view,boundary):
    view=list(view)
    for i,q in enumerate(boundary):
        if view[i]!=q:
            j=view.index(q)
            c.swap(view[i],view[j]);view[i],view[j]=view[j],view[i]


def emit(c,U,V,b,s,p,program,inverse=False):
    program=validate(program)
    wires=list(U)+list(V)+[b,s]
    if len(U)!=len(V) or not U or len(set(wires))!=len(wires):
        raise ValueError('Block registers and controls must be disjoint')
    if type(p) is not int or not 2<p<1<<len(U) or p%2!=1:
        raise ValueError('The block needs an odd modulus within its width')
    original_u=list(U);U=list(U);V=list(V)
    ops=list(reversed(program['ops'])) if inverse else program['ops']
    for item in ops:
        op=INVERSE[item['op']] if inverse else item['op']
        with lowering(c,item['lowering']):
            if op=='swap':A.cswap(c,s,U,V)
            elif op=='sub':A.ctrl_mod_sub(c,b,U,V,p)
            elif op=='add':A.ctrl_mod_add(c,b,U,V,p)
            elif op=='half':V=A.mod_half(c,V,p)
            elif op=='double':V=A.mod_double(c,V,p)
            elif op=='half_u':U=A.mod_half(c,U,p)
            elif op=='double_u':U=A.mod_double(c,U,p)
    _restore_layout(c,U,original_u)
    return V


def signature(program,p):
    """Group sampled algebraic maps. This nominates candidates, without proving them."""
    program=validate(program);out=[]
    for U,V,b,s in product(range(p),range(p),range(2),range(2)):
        u,v=U,V
        for item in program['ops']:
            op=item['op']
            if op=='swap' and s:u,v=v,u
            elif op=='sub':v=(v-b*u)%p
            elif op=='add':v=(v+b*u)%p
            elif op=='half':v=v*pow(2,-1,p)%p
            elif op=='double':v=2*v%p
            elif op=='half_u':u=u*pow(2,-1,p)%p
            elif op=='double_u':u=2*u%p
        out.append((u,v,b,s))
    return tuple(out)


def mutations(program,exploratory=False):
    program=validate(program);out=[]
    for i,item in enumerate(program['ops']):
        child=copy.deepcopy(program)
        child['ops'][i]['lowering']='direct' if item['lowering']=='legacy' else 'legacy'
        out.append(child)
    ops=program['ops']
    for i in range(len(ops)-1):
        a,b=ops[i:i+2]
        if [a['op'],b['op']]==['sub','half'] and len(ops)+2<=8:
            mode=a['lowering']
            replacement=[{'op':op,'lowering':mode} for op in ['half_u','half','sub','double_u']]
            out.append({'ops':copy.deepcopy(ops[:i])+replacement+copy.deepcopy(ops[i+2:])})
        if exploratory:
            child=copy.deepcopy(program);child['ops'][i:i+2]=[copy.deepcopy(b),copy.deepcopy(a)]
            out.append(child)
    return out


def seeds():
    """Enumerate bounded lowering choices and one proved linear-map expansion."""
    out=[]
    for shape in [['swap','sub','half'],['swap','half_u','half','sub','double_u']]:
        for modes in product(['legacy','direct'],repeat=len(shape)-1):
            out.append({'ops':[{'op':'swap','lowering':'legacy'}]+
                        [{'op':op,'lowering':mode} for op,mode in zip(shape[1:],modes)]})
    return out
