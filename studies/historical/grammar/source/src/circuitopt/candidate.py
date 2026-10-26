"""Candidate emission wiring, separate from arithmetic and phase checks."""
from . import block_program


def build_recipe(circuit,x,y,recipe):
    from .rule_ops import _validate_recipe
    from .division import FAMILIES
    _validate_recipe(recipe)
    circuit.payload_program=recipe.get('block_program')
    kwargs={}
    if recipe['family']=='width':
        for key in ['widths','compare_bits']:
            if recipe.get(key) is not None:kwargs[key]=recipe[key]
    return FAMILIES[recipe['family']][1](circuit,x,y,int(recipe['prime']),recipe['steps'],**kwargs)
