"""Generate typed arithmetic candidates from the versioned rule library."""
from functools import lru_cache
from . import rules
from .arithmetic import designs

ACTIVE_RULES=['R01','R02','R04','R08','R10','R11','R18','R19','R20']
PRIMES={5:17,16:65521,256:2**256-2**32-977}


@lru_cache(maxsize=16)
def schedule(family,bits):
    if family in ['inverse','replay','width'] or bits>16:return 2*bits-1
    return {'jump2':designs.jump2_length,'pingpong':designs.pingpong_length,
            'leapfrog':designs.leapfrog_length}[family](PRIMES[bits])


def baseline_recipes(bits):
    return {name:{'bits':bits,'prime':PRIMES[bits],'family':name,
                  'steps':2*bits-1,'cleanup':'toffoli'} for name in ['inverse','replay','width']}


def options(recipe,rng,include_blocks=False):
    n,L,f=recipe['bits'],recipe['steps'],recipe['family'];out=[]
    if f=='inverse':out.append(('R01',{}))
    if recipe['cleanup']=='toffoli':out.append(('R02',{}))
    if f=='replay':out.append(('R04',{}))
    for rid,family in [('R08','jump2'),('R10','pingpong'),('R11','leapfrog')]:
        if f!=family or L!=schedule(family,n):
            out.append((rid,{'steps':schedule(family,n)}))
    if L>1:out.append(('R18',{'steps':max(1,L-rng.choice([1,2,max(1,L//4)]))}))
    if f=='width':
        base=recipe.get('widths') or [designs.width_bound(n,i) for i in range(L+1)]
        cut=rng.randint(1,max(1,L));delta=rng.choice([-1,1,2])
        widths=[n]+[max(1,min(n,base[i]-(delta if i>=cut else 0))) for i in range(1,L+1)]
        for i in range(1,len(widths)):widths[i]=min(widths[i-1],widths[i])
        out.append(('R19',{'widths':widths}))
        out.append(('R20',{'compare_bits':max(1,n-rng.choice([1,2,max(1,n//3)]))}))
    if include_blocks and f in ['inverse','replay','width']:
        from . import block_program
        programs=block_program.seeds()
        branch=rng.random()
        if branch<.1:
            programs=[{'ops':[{'op':rng.choice(sorted(block_program.OPS)),
                               'lowering':rng.choice(['legacy','direct'])}
                              for _ in range(rng.randint(1,8))]}]
        elif branch<.35:
            programs=block_program.mutations(recipe.get('block_program',block_program.DEFAULT),exploratory=True)
        out.append(('R21',{'program':rng.choice(programs)}))
    return out


def propose(library,parent,rng,active_rules=None,admit=None):
    choices=[(rid,params) for rid,params in options(parent,rng,include_blocks=bool(active_rules and 'R21' in active_rules))
             if active_rules is None or rid in active_rules]
    priorities={card['id']:card['priority'] for card in rules.cards(library)}
    while choices:
        weights=[priorities[rid] for rid,_ in choices]
        rid,params=rng.choices(choices,weights=weights if sum(weights) else None,k=1)[0]
        try:
            applied=rules.apply(library,rid,parent,params)
            if admit is None or admit(applied['candidate']):return applied
        except ValueError:pass
        choices.remove((rid,params))
    raise ValueError('No applicable active arithmetic rule')
