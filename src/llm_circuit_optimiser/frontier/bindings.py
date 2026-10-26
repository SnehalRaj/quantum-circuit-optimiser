"""Bind typed frontier actions to counted edits of pinned source bytes."""
from __future__ import annotations

import copy
import hashlib
import re

from ..common import ROOT, digest, sha256
from ..grammar import CATALOG_RULE
from .discovery import GUARD_PATH, NEW_GUARD, OLD_GUARD, inspect_source, _mask_noncode
from .generators import CATALOG_EDITS, CATALOG_FILES
from .materialize import NOTICE_NAMES, _apply_recipe, _relative, normalize_recipe

CATALOG_COMMIT = '4073f09cfa7fe733fdeb64841193282aaff293d9'
LAYOUT_NAMES = {'POOL_1', 'POOL_D', 'GPOOL', 'PEAK0'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _base_files(files):
    _require(isinstance(files, dict) and bool(files), 'Binding needs the pinned base file bytes')
    for path, raw in files.items():
        _relative(path)
        _require(path.startswith('src/point_add/') or path in NOTICE_NAMES,
                 'The binding base must contain only point-addition source and notices')
        _require(isinstance(raw, bytes), 'Binding reads exact source bytes')
    _require('src/point_add/mod.rs' in files, 'The base lacks its point-addition entry point')
    return {path: hashlib.sha256(raw).hexdigest() for path, raw in files.items()}


def _references(grammar, metadata, actions):
    references = metadata.get('grammar_refs')
    _require(isinstance(references, list) and bool(references), 'Typed actions need pinned grammar_refs')
    available = {(d['id'], d['revision'], d['sha256']) for d in grammar.documents}
    selected = set()
    for reference in references:
        _require(isinstance(reference, dict) and set(reference) == {'id', 'revision', 'sha256'},
                 'A grammar reference needs its ID, revision and SHA-256')
        identity = (reference['id'], reference['revision'], reference['sha256'])
        _require(identity in available and identity not in selected, 'A grammar reference changed or repeats')
        selected.add(identity)
    covered = {(name, sha) for name, _, sha in selected}
    for action in actions:
        origins = grammar.source_map[action['rule']]
        _require(any((r['document'], r['sha256']) in covered for r in origins),
                 f"Rule {action['rule']} lacks its pinned grammar reference")
    return copy.deepcopy(references)


def _setting(rule, parameters, recipe, files):
    entries = [entry for entry in rule['evidence'] if 'setting_recipe' in entry]
    _require(len(entries) == 1, 'A setting needs one source-bound recipe')
    entry = entries[0]
    program = entry['setting_recipe']
    _require(program.get('schema') == 'lco-discovered-setting-v1', 'Unknown setting recipe schema')
    base, dial = program['base'], program['dial']
    commit = base['commit']
    _require(isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{40}', commit)
             and commit.startswith(recipe['base_commit']), 'The setting belongs to a different base commit')
    source = inspect_source(files, commit)
    declared = base['source']
    for key in ('files_sha256', 'tree_sha256', 'coupled_guards'):
        _require(declared.get(key) == source[key], f'The setting base {key} changed')
    constants = [c for c in source['constants'] if c['key'] == dial['key']]
    _require(len(constants) == 1 and declared.get('constants') == constants,
             'The setting constant differs from its source recipe')
    constant = constants[0]
    _require(constant['name'] in LAYOUT_NAMES and not constant['excluded']
             and constant['match_count'] == 1 and dial['classification'] == 'known_layout',
             'The setting has no admitted layout literal')
    _require(dial['rule_id'] == rule['id'] and dial['name'] == constant['name']
             and dial['path'] == constant['path'], 'The setting rule and literal identity differ')
    for evidence in rule['evidence']:
        _require(evidence.get('commit') == commit
                 and source['files_sha256'].get(evidence.get('path')) == evidence.get('sha256'),
                 'Setting evidence does not match the pinned source')
    _require(entry.get('path') == constant['path'] and entry.get('setting_key') == constant['key'],
             'Setting evidence names a different constant')
    schema = rule['parameters']['value']
    _require(schema.get('type') == 'integer' and schema.get('minimum') == dial['minimum']
             and schema.get('maximum') == dial['maximum'] and schema.get('default') == constant['value'],
             'The setting recipe and parameter bounds differ')
    value = parameters['value']
    _require(value != constant['value'], 'A setting action must change the layout literal')
    start = constant['literal_offset']
    replacement = constant['statement'][:start] + str(value) + constant['statement'][start + len(constant['literal']):]
    edits = [{'path': constant['path'], 'old': constant['statement'], 'new': replacement, 'count': 1}]
    coupled = dial['coupled_edits']
    allowed = {'path': GUARD_PATH, 'old': OLD_GUARD, 'new': NEW_GUARD, 'count': 1}
    _require(isinstance(coupled, list) and len(coupled) <= 1, 'Unsupported coupled setting edits')
    for edit in coupled:
        _require(constant['name'] == 'GPOOL'
                 and {k: edit.get(k) for k in allowed} == allowed
                 and edit.get('when', 'GPOOL > 0') == 'GPOOL > 0'
                 and not set(edit) - set(allowed) - {'reason', 'when', 'scope'}, 'Unsupported coupled setting edit')
    if constant['name'] == 'GPOOL' and value > 0:
        guards = source['coupled_guards']
        inherited = [g for g in guards if g['path'] == GUARD_PATH and g['name'] == 'inherited']
        ordinary = [g for g in guards if g['path'] == GUARD_PATH and g['name'] == 'ordinary_maj']
        _require(bool(inherited) != bool(ordinary), 'The GPOOL setting needs one known S5 guard')
        if inherited:
            _require(len(coupled) == 1, 'The GPOOL setting omitted its coupled S5 guard')
            edits.append(allowed)
    return edits, commit


def _catalog(rule, files):
    evidence = {entry['path']: entry.get('sha256') for entry in rule['evidence']}
    data = {}
    for name, expected in CATALOG_FILES.items():
        vendor_path = 'vendor/frontier_rewrite/' + name
        _require(evidence.get(vendor_path) == expected and sha256(ROOT / vendor_path) == expected,
                 'The catalog rule or vendored implementation changed')
        data['src/point_add/frontier_rewrite/' + name] = (ROOT / vendor_path).read_bytes()
    for path, expected in evidence.items():
        _require(path.startswith('vendor/frontier_rewrite/') and sha256(ROOT / _relative(path)) == expected,
                 'Catalog evidence changed')
    text = files['src/point_add/mod.rs'].decode('utf-8')
    code = _mask_noncode(text)
    if 'frontier_rewrite' in code:
        _require(code.count('mod frontier_rewrite;') == 1
                 and code.count('frontier_rewrite::apply(ops)') == 1
                 and re.search(r'    ops = frontier_rewrite::apply\(ops\);\r?\n    ops.extend\(tail\);', text)
                 and all(files.get(path) == raw for path, raw in data.items()),
                 'The inherited catalog has different source or integration')
        return [], [], {}, True
    _require(not any(path in files for path in data), 'The base contains a partial catalog integration')
    for _, old, _ in CATALOG_EDITS:
        _require(text.count(old) == 1 and code.count(old) == 1,
                 'The catalog insertion needs one exact module and tail anchor')
    edits = [dict(zip(('path', 'old', 'new'), edit), count=1) for edit in CATALOG_EDITS]
    copies = [{'path': path, 'source_path': path, 'source_commit': CATALOG_COMMIT,
               'sha256': hashlib.sha256(raw).hexdigest()} for path, raw in data.items()]
    return edits, copies, data, False


def bind_actions(job, grammar=None, family=None, *, base_files):
    """Return the bound job and a source-bound receipt, without executing source."""
    if hasattr(job, 'to_dict'):
        job = job.to_dict()
    job = copy.deepcopy(job)
    recipe = normalize_recipe(job)
    pins = _base_files(base_files)
    point_pins = {path: value for path, value in pins.items() if path.startswith('src/point_add/')}
    _require(recipe['base_files_sha256'] in (None, pins, point_pins), 'The job base source pins changed')
    metadata = job.get('metadata', {})
    _require(isinstance(metadata, dict), 'Job metadata must be an object')
    actions = metadata.get('actions', [])
    _require(isinstance(actions, list), 'Typed actions must be a list')
    rule_records, references, copy_data = [], [], {}
    if actions:
        _require(grammar is not None, 'Typed actions require a compiled grammar')
        family = family or metadata.get('family')
        _require(family is not None, 'Typed actions need a family')
        if isinstance(family, str) and metadata.get('family') is not None:
            _require(family == metadata['family'], 'The requested and recorded families differ')
        actions = grammar.validate_frontier_actions(actions, family)
        references = _references(grammar, metadata, actions)
        edits, copies = [], []
        for action in actions:
            rule = grammar.rules[action['rule']]
            inherited = False
            if action['kind'] == 'setting':
                delta, resolved = _setting(rule, action['parameters'], recipe, base_files)
                edits.extend(delta)
                job['base_commit'] = resolved
            elif action['rule'] == CATALOG_RULE:
                delta, additions, data, inherited = _catalog(rule, base_files)
                edits.extend(delta)
                copies.extend(additions)
                copy_data.update(data)
            else:
                raise ValueError(f"Rule {action['rule']} has no counted source binding")
            rule_records.append({**action, 'revision': rule['revision'], 'rule_sha256': digest(rule),
                                 'inherited': inherited})
        _require(not recipe['edits'] or recipe['edits'] == edits,
                 'The counted job edits differ from its typed actions')
        if recipe['file_copies']:
            supplied = copy.deepcopy(recipe['file_copies'])
            for item in supplied:
                if isinstance(item['source_commit'], str) and CATALOG_COMMIT.startswith(item['source_commit']):
                    item['source_commit'] = CATALOG_COMMIT
            _require(supplied == copies, 'The copied files differ from the typed catalog rule')
        job['edits'], job['file_copies'] = edits, copies
        recipe = normalize_recipe(job)
    else:
        _require(not metadata.get('grammar_refs'), 'Grammar references require typed actions')
    job['base_files_sha256'] = pins
    payload = [(item, copy_data[item['path']], item['source_commit'])
               for item in recipe['file_copies'] if item['path'] in copy_data]
    _, edit_receipts, _ = _apply_recipe(base_files, recipe, payload)
    receipt = {'schema': 'frontier-grammar-binding-v1', 'typed': bool(actions),
               'grammar_sha256': grammar.sha256 if actions else None, 'grammar_refs': references,
               'family': family or metadata.get('family'), 'actions': rule_records,
               'base_commit': job['base_commit'], 'base_files_sha256': pins,
               'base_tree_sha256': digest(pins), 'edits': edit_receipts,
               'file_copies': recipe['file_copies'],
               'bound_recipe_sha256': digest(normalize_recipe(job)),
               'binding_source_sha256': sha256(__file__),
               'scope': ('Typed rules expanded into checked source edits. Full-circuit admission remains separate.'
                         if actions else 'Manual counted source edits. This receipt makes no typed-rule admission claim.')}
    return {'job': job, 'receipt': receipt}
