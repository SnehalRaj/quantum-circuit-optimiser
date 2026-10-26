"""Connect the bounded block grammar to measured study proposals."""
import copy
from collections import Counter

from . import block_program, engine, rules

GRAMMARS = ['recipes-v1', 'blocks-v1']
BLOCK_CARD = {
    'id':'R21', 'name':'Edit the ordered payload program', 'version':1,
    'enabled':True, 'priority':100, 'operator':'payload_program', 'status':'implemented',
    'correctness':'approximate', 'role':'compose',
    'reasoning':'Expose arithmetic order and constant lowerings. Check each complete emitted candidate before selection.',
    'conditions':['The family is inverse, replay, or width.',
                  'The modulus is odd. The payload registers hold canonical residues.',
                  'Restore controls, scratch, and measurement phases.',
                  'Arbitrary compositions require evaluation. Only the proved seed forms have an all-width identity.'],
    'parameters':{'program':{'ops':'One to eight ordered objects with op and lowering fields.'}},
    'pattern':{'operator':'payload_program', 'conditions_required':True},
    'replacement':'Replace the payload program with the supplied ordered operations and constant lowerings.',
    'source_urls':[], 'seed_evidence':[],
    'evidence_note':'See docs/block-grammar.md, docs/constant-kernel.md, and the independent block and kernel tests.'}


def install(directory):
    return rules.add(directory, BLOCK_CARD)


def legal_rules(recipe, active_rules, proposal_policy='historical-v1'):
    family = recipe['family']
    conditions = {'R01':family == 'inverse', 'R02':recipe['cleanup'] == 'toffoli',
                  'R04':family == 'replay', 'R18':recipe['steps'] > 1,
                  'R19':family == 'width', 'R20':family == 'width',
                  'R21':family in ['inverse', 'replay', 'width']}
    if proposal_policy=='guarded-seeds-v1':
        from .study_policy import minimum_steps
        conditions['R18']=recipe['steps']>minimum_steps(recipe)
    return [rid for rid in active_rules if conditions.get(rid, True)]


def packet_description():
    return {'registers':'U and V are residues modulo p. b controls subtraction. s controls the swap.',
            'map':'The target payload maps (U,V) to (U,(V-b*U)/2) after the controlled swap.',
            'max_ops':8, 'lowerings':['legacy', 'direct'],
            'operations':{'swap':'Swap U,V when s=1.', 'sub':'V <- V-b*U mod p.',
                          'add':'V <- V+b*U mod p.', 'half':'V <- V/2 mod p.',
                          'double':'V <- 2*V mod p.', 'half_u':'U <- U/2 mod p.',
                          'double_u':'U <- 2*U mod p.'},
            'seed_programs':[block_program.seeds()[3], block_program.seeds()[-1]],
            'instruction':'For R21 use parameters={"program":{"ops":[...]}}. Preserve the payload map. Use each parent\'s legal_rule_ids.'}


def transfer_source(audit):
    """Choose a proved seed from eligible endpoints before full-width tests."""
    seeds = {engine.digest(p) for p in block_program.seeds()}
    candidates = []
    for row in audit['rows']:
        if not row['eligible']:
            continue
        recipe = audit['receipts'][row['selected']]['recipe']
        program = recipe.get('block_program')
        if program and engine.digest(program) in seeds:
            candidates.append((row, recipe))
    if not candidates:
        return None
    counts = Counter(engine.digest(recipe['block_program']) for _, recipe in candidates)
    selected = min(counts, key=lambda key:(-counts[key], key))
    matches = [(row, recipe) for row, recipe in candidates
               if engine.digest(recipe['block_program']) == selected]
    return {'program':copy.deepcopy(matches[0][1]['block_program']),
            'source_recipe':copy.deepcopy(matches[0][1]),
            'sources':[copy.deepcopy(row) for row, _ in matches],
            'selection':'Most frequent proved seed among eligible frozen endpoints; program hash breaks ties.'}
