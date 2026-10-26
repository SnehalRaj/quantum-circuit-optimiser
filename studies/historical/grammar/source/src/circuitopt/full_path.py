"""Explicit rule applications for complete native point addition."""
import copy
import hashlib
import json

FIELDS = ('measured_cleanup', 'correction_fusion', 'split_walk')
RULES = {
    'PA01': {'name': 'Replace Kaliski with the compact ping-pong implementation',
             'field': 'backend', 'attribution': 'joint implementation replacement'},
    'PA02': {'name': 'Measure and correct temporary carry and AND cleanup',
             'field': 'measured_cleanup', 'library_rule': 'R02'},
    'PA03': {'name': 'Fuse selected constant corrections',
             'field': 'correction_fusion', 'library_rule': 'R03'},
    'PA04': {'name': 'Split the walk carry ladder with boundary repair',
             'field': 'split_walk', 'library_rule': 'R05'},
}


def recipe_id(recipe):
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def starting_recipe():
    return {'backend': 'original', **{field: False for field in FIELDS}}


def validate(recipe):
    if not isinstance(recipe, dict) or set(recipe) != {'backend', *FIELDS}:
        raise ValueError('Provide the backend and all three structural controls')
    if recipe['backend'] not in ['original', 'pingpong']:
        raise ValueError('Unknown native backend')
    if any(type(recipe[field]) is not bool for field in FIELDS):
        raise ValueError('Each structural control must be Boolean')
    if recipe['backend'] == 'original' and any(recipe[field] for field in FIELDS):
        raise ValueError('The original backend has no ping-pong controls')
    return recipe


def legal_actions(recipe):
    validate(recipe)
    if recipe['backend'] == 'original':
        return [{'rule_id': 'PA01', 'parameters': {}}]
    return [{'rule_id': rule, 'parameters': {'enabled': not recipe[info['field']]}}
            for rule, info in RULES.items() if rule != 'PA01']


def apply(recipe, action):
    validate(recipe)
    if action not in legal_actions(recipe):
        raise ValueError('The action does not apply to this parent')
    child = copy.deepcopy(recipe)
    rule = action['rule_id']
    if rule == 'PA01':
        child['backend'] = 'pingpong'
    else:
        child[RULES[rule]['field']] = action['parameters']['enabled']
    return validate(child)


def native_arguments(recipe):
    validate(recipe)
    args = ['--backend', recipe['backend']]
    if recipe['backend'] == 'pingpong':
        settings = {'CP_CARRY_MEASURED': recipe['measured_cleanup'],
                    'CP_AND_MEASURED': recipe['measured_cleanup'],
                    'CP_FOLD_FUSED': recipe['correction_fusion'],
                    'CP_WALK_SPLIT': recipe['split_walk']}
        for key, enabled in settings.items():
            args += ['--set', f'{key}={int(enabled)}']
    return args


def construction_path():
    recipe = starting_recipe()
    rows = [{'edit': 0, 'rule_id': None, 'recipe': recipe}]
    for step, rule in enumerate(RULES, 1):
        action = {'rule_id': rule, 'parameters': {} if rule == 'PA01' else {'enabled': True}}
        child = apply(recipe, action)
        rows.append({'edit': step, 'rule_id': rule, 'parent_id': recipe_id(recipe),
                     'action': action, 'recipe': child})
        recipe = child
    return rows


def candidate_pool():
    output = [starting_recipe()]
    for mask in range(8):
        output.append({'backend': 'pingpong',
                       **{field: bool(mask & (1 << index)) for index, field in enumerate(FIELDS)}})
    return output


def canonical_endpoints(ids, oracle):
    hashes, aliases = {}, {}
    for cid in sorted(set(ids)):
        operation = oracle[cid]['artifact_sha256']
        hashes.setdefault(operation, cid)
        aliases[cid] = hashes[operation]
    return sorted(set(aliases.values())), aliases


def failure_upper(failures, pairs, alpha=0.05):
    from scipy.stats import beta
    if not 0 <= failures <= pairs or pairs <= 0 or not 0 < alpha < 1:
        raise ValueError('Invalid binomial evidence')
    return 1.0 if failures == pairs else float(beta.ppf(1 - alpha, failures + 1, pairs - failures))


def hits(recipe, result, reference_recipe, reference_S):
    passed = result['failed_inputs'] == 0
    return {'recipe': recipe == reference_recipe,
            'cost': passed and result['S'] <= reference_S,
            'google': passed and result['S'] <= 3_000_000_000}


def model_packet(parents, recent, proposal):
    return {
        'task': 'Reduce complete 256-bit secp256k1 point-addition cost S = T * Q.',
        'scope': 'Select one listed legal action for a listed parent. All actions need the same checker.',
        'rules': RULES,
        'proposal_number': proposal,
        'parents': [{**p, 'legal_actions': legal_actions(p['recipe'])} for p in parents],
        'recent': recent,
        'instruction': 'Return at most eight proposals. changes_json must encode one legal action object. '
                       'Use the exact parent_id. Explain the expected cost effect in hypothesis.',
    }
