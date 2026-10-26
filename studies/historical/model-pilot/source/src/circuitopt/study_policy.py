"""Share validated starting paths and finite walk bounds across strategies."""
from functools import lru_cache

from . import block_program, engine, rules, search_space

POLICIES = ['historical-v1', 'guarded-seeds-v1']


@lru_cache(maxsize=8)
def requirements(bits, prime):
    from .grammar_campaign import walk_requirements
    return walk_requirements(bits, prime)


def minimum_steps(recipe):
    if recipe['family'] in ['inverse', 'replay', 'width']:
        return requirements(recipe['bits'], recipe['prime'])['max_steps']
    return search_space.schedule(recipe['family'], recipe['bits'])


def admit(recipe, policy='historical-v1'):
    if policy not in POLICIES:
        raise ValueError('Unknown proposal policy')
    if policy == 'historical-v1':
        return True
    from .grammar_campaign import exact_guard
    return exact_guard(recipe, requirements(recipe['bits'], recipe['prime']))


def seed_change(library, state, trial, policy='historical-v1', active_rules=None):
    if policy not in POLICIES or trial < 1:
        raise ValueError('Invalid seed policy or proposal round')
    if policy == 'historical-v1' or trial > 6:
        return None
    recipe = state['starting']['recipe']
    actions = [('R02', {})]
    if recipe['family'] == 'inverse':
        actions.append(('R01', {}))
    if recipe['family'] != 'width':
        actions.append(('R04', {}))
    actions += [('R21', {'program':block_program.seeds()[3]}),
                ('R19', {'widths':requirements(recipe['bits'], recipe['prime'])['widths']}),
                ('R20', {'compare_bits':recipe['bits']-1})]
    active = set(active_rules) if active_rules is not None else {c['id'] for c in rules.cards(library) if c['enabled']}
    path = []
    for rid, parameters in actions:
        if rid not in active:
            continue
        try:
            applied = rules.apply(library, rid, recipe, parameters)
        except ValueError:
            continue
        if not admit(applied['candidate'], policy):
            continue
        path.append((recipe, applied))
        recipe = applied['candidate']
    if trial > len(path):
        return None
    parent, applied = path[trial-1]
    return state['archive'][engine.digest(parent)], applied


def description(bits, prime):
    bounds = requirements(bits, prime)
    return {'minimum_plain_rounds':bounds['max_steps'],
            'minimum_widths':bounds['widths'], 'minimum_comparison_bits':bits-1,
            'minimum_backend_rounds':{family:search_space.schedule(family,bits)
                                      for family in ['jump2','pingpong','leapfrog']},
            'scope':'Finite denominator bounds for this modulus. Payload proposals still need the fixed checker.'}
