"""Find source-bound layout settings and propose counted campaign edits."""
from collections import defaultdict
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import subprocess

from ..common import digest, save
from .jobs import normalize_job
from .materialize import NOTICE_NAMES
from .public import best_at_or_below, eligible, select_frontier
from .reliability import rank_parent_reliability


PUBLIC_REPOSITORY = 'https://github.com/Layr-Labs/ecdsafail-challenge.git'
OLD_GUARD = 'let gp = self.gp.clone();'
NEW_GUARD = ('let gp: Vec<QubitId> = Vec::new(); // S5 keeps ordinary MAJ lanes: '
             'its rolling prefix and inverse repair need them')
GUARD_PATH = 'src/point_add/froghop_one.rs'
_COMMIT = re.compile(r'[0-9a-f]{7,40}')
_CONST = re.compile(
    r'(?m)^[ \t]*(?:pub(?:\([^\n)]*\))?\s+)?const\s+(?P<name>[A-Z_][A-Z0-9_]*)'
    r'\s*:\s*(?P<type>usize|isize|u8|u16|u32|u64|u128|i8|i16|i32|i64|i128)'
    r'\s*=\s*(?P<value>0[xX][0-9a-fA-F_]+|[0-9][0-9_]*)\s*;')
_POLICY = re.compile(r'cond_pass::run_with\([^,\n]+,\s*"(?P<policy>[mfsur]*)"\)')
_EXCLUDED = re.compile(r'NONCE|TAIL|PADDING|(?:^|_)SEED(?:_|$)')
_LAYOUT_SETTINGS = frozenset({'POOL_1', 'GPOOL', 'POOL_D', 'PEAK0'})


def _git(repo, arguments, *, runner=subprocess.run, input=None):
    result = runner(['git', '-C', str(repo), *arguments], input=input, capture_output=True, check=False)
    if result.returncode:
        message = result.stderr.decode(errors='replace') if isinstance(result.stderr, bytes) else result.stderr
        raise ValueError(f'Git source read failed: {message.strip()}')
    return result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()


def fetch_commit(git_repo, commit, *, runner=subprocess.run):
    """Fetch one explicit public commit into the caller's source cache."""
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('A fetch requires a full immutable commit ID')
    _git(git_repo, ['fetch', '--depth=1', PUBLIC_REPOSITORY, commit], runner=runner)


def read_source(git_repo, commit, *, runner=subprocess.run, fetch_missing=None):
    """Resolve a commit and read regular point-addition blobs without checkout."""
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise ValueError('Source discovery requires a hexadecimal commit ID')
    try:
        resolved = _git(git_repo, ['rev-parse', '--verify', f'{commit}^{{commit}}'], runner=runner).decode().strip()
    except ValueError:
        if fetch_missing is None:
            raise
        fetch_missing(git_repo, commit)
        resolved = _git(git_repo, ['rev-parse', '--verify', f'{commit}^{{commit}}'], runner=runner).decode().strip()
    if not re.fullmatch(r'[0-9a-f]{40}', resolved) or not resolved.startswith(commit):
        raise ValueError('The resolved commit does not match the requested identity')
    entries = _git(git_repo, ['ls-tree', '-r', '-z', resolved, '--', 'src/point_add', *sorted(NOTICE_NAMES)], runner=runner)
    blobs = []
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        header, path = entry.split(b'\t', 1)
        mode, kind, sha = header.decode().split()
        path = path.decode()
        if mode not in {'100644', '100755'} or kind != 'blob':
            raise ValueError('Point-addition source cannot contain links or submodules')
        blobs.append((path, sha))
    if not any(path == 'src/point_add/mod.rs' for path, _ in blobs):
        raise ValueError('The commit has no point-addition source tree')
    raw = _git(git_repo, ['cat-file', '--batch'], runner=runner,
               input=('\n'.join(sha for _, sha in blobs) + '\n').encode())
    files, offset = {}, 0
    for path, expected in blobs:
        newline = raw.index(b'\n', offset)
        sha, kind, size = raw[offset:newline].decode().split()
        size = int(size)
        if sha != expected or kind != 'blob' or size < 0:
            raise ValueError('Git returned an unexpected source object')
        offset = newline + 1
        files[path] = raw[offset:offset + size]
        if len(files[path]) != size or raw[offset + size:offset + size + 1] != b'\n':
            raise ValueError('Git returned an incomplete source object')
        offset += size + 1
    if offset != len(raw):
        raise ValueError('Git returned extra source objects')
    return resolved, files


def _mask_noncode(text):
    """Mask Rust comments and strings while keeping character positions."""
    spans, i = [], 0
    token = re.compile(r'//|/\*|(?:br|r)\#*"|"|\'(?:\\.|[^\'\\\r\n])\'')
    while True:
        match = token.search(text, i)
        if match is None:
            break
        start, value = match.start(), match.group()
        if value == '//':
            end = text.find('\n', match.end())
            end = len(text) if end < 0 else end
        elif value == '/*':
            depth, cursor = 1, match.end()
            while depth:
                next_token = re.search(r'/\*|\*/', text[cursor:])
                if next_token is None:
                    cursor = len(text)
                    break
                depth += 1 if next_token.group() == '/*' else -1
                cursor += next_token.end()
            end = cursor
        elif value.startswith(('r', 'br')):
            close = '"' + '#' * value.count('#')
            found = text.find(close, match.end())
            end = len(text) if found < 0 else found + len(close)
        elif value == '"':
            cursor = match.end()
            while cursor < len(text):
                if text[cursor] == '\\':
                    cursor += 2
                elif text[cursor] == '"':
                    cursor += 1
                    break
                else:
                    cursor += 1
            end = cursor
        else:
            end = match.end()
        spans.append((start, end))
        i = end
    parts, cursor = [], 0
    for start, end in spans:
        parts.extend((text[cursor:start], re.sub(r'[^\r\n]', ' ', text[start:end])))
        cursor = end
    parts.append(text[cursor:])
    return ''.join(parts)


def _inspect_file(path, raw):
    sha = hashlib.sha256(raw).hexdigest()
    if not path.endswith('.rs'):
        return {'sha256': sha, 'literal_sha256': sha, 'family_sha256': sha,
                'constants': [], 'policies': [], 'guards': []}
    text = raw.decode('utf-8')
    code = _mask_noncode(text)
    constants, edits, occurrences = [], [], defaultdict(int)
    for match in _CONST.finditer(code):
        name = match['name']
        occurrences[name] += 1
        literal = text[match.start('value'):match.end('value')]
        value = int(literal.replace('_', ''), 16 if literal.lower().startswith('0x') else 10)
        constants.append({'name': name, 'type': match['type'], 'value': value,
                          'literal': literal, 'occurrence': occurrences[name],
                          'line': text.count('\n', 0, match.start()) + 1,
                          'statement': text[match.start():match.end()],
                          'match_count': text.count(text[match.start():match.end()]),
                          'literal_offset': match.start('value') - match.start(),
                          'excluded': bool(_EXCLUDED.search(name))})
        edits.append((match.start('value'), match.end('value'), f'<{name}:integer>'))
    def edited(replacements):
        output = text
        for start, end, replacement in sorted(replacements, reverse=True):
            output = output[:start] + replacement + output[end:]
        return hashlib.sha256(output.encode()).hexdigest()
    literal_sha = edited(edits)
    policies = []
    for match in _POLICY.finditer(text):
        if code[match.start():match.start() + 9] != 'cond_pass':
            continue
        value = match['policy']
        if len(set(value)) != len(value):
            continue
        policies.append({'policy': value, 'statement': match.group(),
                         'line': text.count('\n', 0, match.start()) + 1})
        edits.append((match.start('policy'), match.end('policy'), '<conditional-policy>'))
    guards = []
    if path == GUARD_PATH:
        for name, statement in [('inherited', OLD_GUARD), ('ordinary_maj', NEW_GUARD)]:
            start = text.find(statement)
            if start >= 0 and text.count(statement) == 1 and code[start:start + 3] == 'let':
                guards.append({'name': name, 'statement': statement,
                               'line': text.count('\n', 0, start) + 1})
                edits.append((start, start + len(statement), '<froghop-s5-guard>'))
    return {'sha256': sha, 'literal_sha256': literal_sha, 'family_sha256': edited(edits),
            'constants': constants, 'policies': policies, 'guards': guards}


def inspect_source(files, commit, *, cache=None):
    """Inspect supplied bytes; callers retain their Git or fixture identity."""
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Source inspection requires the resolved commit ID')
    cache = {} if cache is None else cache
    records, constants, policies, guards = {}, [], [], []
    for path, raw in sorted(files.items()):
        if (not (path.startswith('src/point_add/') or path in NOTICE_NAMES)
                or '..' in Path(path).parts or not isinstance(raw, bytes)):
            raise ValueError('Source inspection accepts point-addition files and retained notices only')
        key = (path, hashlib.sha256(raw).hexdigest())
        if key not in cache:
            cache[key] = _inspect_file(path, raw)
        record = cache[key]
        records[path] = record
        for item in record['constants']:
            constants.append({'path': path, 'file_sha256': record['sha256'],
                              'key': f"{path}:{item['name']}:{item['occurrence']}", **item})
        policies.extend({'path': path, **item} for item in record['policies'])
        guards.extend({'path': path, 'file_sha256': record['sha256'], **item} for item in record['guards'])
    file_hashes = {p: r['sha256'] for p, r in records.items()}
    arithmetic = {p: r for p, r in records.items() if p.startswith('src/point_add/')}
    return {'commit': commit, 'files_sha256': file_hashes, 'tree_sha256': digest(file_hashes),
            'point_add_tree_sha256': digest({p: r['sha256'] for p, r in arithmetic.items()}),
            'literal_signature': digest({p: r['literal_sha256'] for p, r in arithmetic.items()}),
            'family_signature': digest({p: r['family_sha256'] for p, r in arithmetic.items()}),
            'constants': constants, 'compiler_policies': policies, 'coupled_guards': guards}


def parse_reliability(note, *, source):
    """Extract stated rates without turning source claims into measurements."""
    if not isinstance(note, str):
        return []
    results, covered = [], []
    number = r'\d+(?:\.\d+)?'
    patterns = [
        ('lambda', re.compile(rf'\b(?:lambda|λ)\s*(?:is|=|:|of|about|~|≈)?\s*({number})(?:\s*[-–]\s*({number}))?', re.I)),
        ('failures_per_packet', re.compile(rf'({number})(?:\s*[-–]\s*({number}))?\s+(?:failing|failed|failures?)(?:\s+shots?|\s+inputs?)?\s*(?:per|/|of|in)\s*9,?024', re.I)),
    ]
    for kind, pattern in patterns:
        for match in pattern.finditer(note):
            if any(a <= match.start() < b for a, b in covered):
                continue
            low, high = float(match[1]), float(match[2] or match[1])
            if high < low:
                continue
            start, end = max(0, match.start()-70), min(len(note), match.end()+90)
            context = note[start:end]
            unit_known = kind == 'failures_per_packet' or bool(re.search(r'9,?024', context))
            limiting = bool(re.search(r'(?:below|above|at most|limit|must|under|<=|<)\s*$', note[start:match.start()], re.I))
            result = {'kind': 'stated_limit' if limiting else 'note_reported_rate', 'source': source,
                      'scope': 'Source note; independently unverified.', 'text': match.group(),
                      'context': context, 'lambda_range': [low, high],
                      'packet_shots': 9024 if unit_known else None,
                      'pass_chance_poisson_approximation': [math.exp(-high), math.exp(-low)] if unit_known else None,
                      'model': 'Poisson approximation exp(-lambda); not a qualification result.'}
            results.append(result)
            covered.append(match.span())
    return results


def fit_cost(points, key):
    """Fit observed resource counts and retain every residual."""
    values = [p for p in points if type(p.get(key)) in (int, float) and math.isfinite(p[key])]
    distinct = sorted({p['value'] for p in values})
    if len(distinct) < 2:
        return {'status': 'insufficient_distinct_settings', 'points': len(values), 'distinct_settings': distinct}
    xmean = math.fsum(p['value'] for p in values)/len(values)
    ymean = math.fsum(p[key] for p in values)/len(values)
    denominator = math.fsum((p['value']-xmean)**2 for p in values)
    slope = math.fsum((p['value']-xmean)*(p[key]-ymean) for p in values)/denominator
    intercept = ymean-slope*xmean
    residuals = [{'commit': p['commit'], 'submission_id': p['submission_id'], 'setting': p['value'],
                  'observed': p[key], 'fitted': intercept+slope*p['value'],
                  'residual': p[key]-(intercept+slope*p['value'])} for p in values]
    return {'status': 'fitted', 'scope': 'Observed association between settings and resource counts. Other source changes can confound this fit.',
            'points': len(values), 'distinct_settings': distinct, 'slope': slope, 'intercept': intercept,
            'maximum_absolute_residual': max(abs(p['residual']) for p in residuals), 'residuals': residuals}


def _row_record(row, source):
    metrics = row.get('officialMetrics') or {}
    q, t = row.get('Q', metrics.get('qubits')), row.get('T', metrics.get('toffoli'))
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (q, t)):
        raise ValueError('Discovery rows require measured positive Q and T')
    identifier = row.get('id') or row.get('submission_id')
    if not isinstance(identifier, str) or not identifier:
        raise ValueError('Discovery rows require submission IDs')
    note = row.get('note') or row.get('researchNote') or ''
    return {'submission_id': identifier, 'commit': source['commit'], 'Q': q, 'T': t,
            'user': row.get('solverUsername', row.get('user', '')), 'created': row.get('createdAt', row.get('created', '')),
            'resource_scope': 'Recorded public resource counts.', 'note_sha256': hashlib.sha256(note.encode()).hexdigest(),
            'reliability': parse_reliability(note, source=identifier), 'source': source}


def _dial_rule(tree, dial):
    base = tree['base']
    constant = next(c for c in base['source']['constants'] if c['key'] == dial['key'])
    coupled = [g for g in base['source']['coupled_guards'] if g['path'] == GUARD_PATH]
    evidence = [{'path': constant['path'], 'sha256': constant['file_sha256'], 'commit': base['commit'],
                 'symbol': constant['name'], 'line': constant['line'], 'setting_key': constant['key']}]
    recipe_base = {key: copy.deepcopy(base[key]) for key in ('submission_id', 'commit', 'Q', 'T')}
    if 'parent_reliability' in base:
        recipe_base['parent_reliability'] = copy.deepcopy(base['parent_reliability'])
    recipe_base['source'] = {key: copy.deepcopy(base['source'][key])
                             for key in ('files_sha256', 'tree_sha256', 'coupled_guards')}
    recipe_base['source']['constants'] = [copy.deepcopy(constant)]
    evidence[0]['setting_recipe'] = {'schema': 'lco-discovered-setting-v1', 'tree_id': tree['id'],
                                     'base': recipe_base, 'dial': copy.deepcopy(dial)}
    evidence += [{'path': g['path'], 'sha256': g['file_sha256'], 'commit': base['commit'],
                  'symbol': 'S5 carry-lane guard', 'line': g['line']} for g in coupled if dial['name'] == 'GPOOL']
    return {'id': dial['rule_id'], 'revision': 1, 'title': f"Set {dial['name']} in tree {tree['id']}",
            'kind': 'setting', 'description': 'Apply counted layout edits to the pinned point-addition source.',
            'status': 'executable' if dial['classification'] == 'known_layout' and constant['match_count'] == 1 else 'research',
            'families': ['froghop'],
            'parameters': {'value': {'type': 'integer', 'default': constant['value'],
                                     'minimum': dial['minimum'], 'maximum': dial['maximum']},
                           'guards': {'type': 'boolean', 'default': True, 'enum': [True]}},
            'guards': ['EXACT_BASE_SOURCE_HASH', 'COUNTED_SETTING_EDIT', 'NONCE_AND_TAIL_UNCHANGED',
                       'COUPLED_SOURCE_CONDITIONS', 'PAIRED_DEVELOPMENT_REQUIRED', 'FRESH_LARGE_AUDIT'],
            'bindings': {'fast': None, 'fast_reason': 'secp256k1 layout literal',
                         'large': {'froghop': 'frontier.counted_setting'}},
            'mechanism': {'match': f"{constant['path']}: {constant['statement'].strip()}",
                          'replace': 'Change the integer value and apply every required coupled edit.'},
            'preserves': ['Require the point-addition outputs and classical addend to remain valid.',
                          'Require valid phase and clean scratch under the full-width checks.',
                          'Keep inherited nonce and identity-tail choices.'],
            'cost': 'Fit Q/T only as an inference. Measure Q/T after materialization and emission.',
            'evidence': evidence, 'requires': [],
            'notes': ['Source similarity does not prove circuit equivalence.',
                      'The candidate needs paired development admission and a fresh large audit.',
                      'A known nonliteral guard change remains an explicit source condition.']}


def analyze_sources(records):
    """Group inspected sources and record dial evidence and confounders."""
    grouped = defaultdict(list)
    for record in records:
        grouped[record['source']['family_signature']].append(record)
    trees = []
    for signature, members in sorted(grouped.items()):
        members.sort(key=lambda m: (m['Q'], m['created'], m['commit'], m['submission_id']))
        base = members[0]
        tree = {'id': 'tree_' + signature[:12], 'signature': signature, 'base': base,
                'members': members, 'dials': [], 'excluded_constants': [], 'floor': None,
                'scope': 'Source families permit integer, known compiler-policy, and known guard differences only.'}
        values = defaultdict(list)
        for member in members:
            for c in member['source']['constants']:
                values[c['key']].append((member, c))
        changing = {key for key, rows in values.items() if len({c['value'] for _, c in rows}) > 1}
        for key, rows in sorted(values.items()):
            first = rows[0][1]
            if first['excluded']:
                tree['excluded_constants'].append({'key': key, 'name': first['name'],
                                                   'values': sorted({c['value'] for _, c in rows}),
                                                   'reason': 'Nonce, tail, padding, and seed settings cannot become dials.'})
                continue
            if key not in changing:
                continue
            points = [{'value': c['value'], 'Q': m['Q'], 'T': m['T'], 'commit': m['commit'],
                       'submission_id': m['submission_id']} for m, c in rows]
            policies = {tuple((p['path'], p['policy']) for p in m['source']['compiler_policies']) for m, _ in rows}
            confounds = []
            if len(policies) > 1:
                confounds.append('Compiler policies also differ between the recorded circuits.')
            others = [name for name in changing if name != key and not values[name][0][1]['excluded']]
            if others:
                confounds.append('Other integer settings also vary: ' + ', '.join(others))
            identity_changes = [name for name in changing if values[name][0][1]['excluded']]
            if identity_changes:
                confounds.append('Nonce or tail settings also vary: ' + ', '.join(sorted(identity_changes)))
            coupled = [g for m, _ in rows for g in m['source']['coupled_guards']]
            guard_values = {g['name'] for g in coupled}
            if len(guard_values) > 1:
                confounds.append('The known S5 guard changes with the layout setting; its effect is not isolated.')
            dial = {'key': key, 'name': first['name'], 'path': first['path'], 'type': first['type'],
                    'classification': 'known_layout' if first['name'] in _LAYOUT_SETTINGS else 'unmapped_integer',
                    'minimum': min(p['value'] for p in points), 'maximum': max(p['value'] for p in points),
                    'observed_values': sorted({p['value'] for p in points}), 'points': points,
                    'Q_fit': fit_cost(points, 'Q'), 'T_fit': fit_cost(points, 'T'),
                    'confounders': confounds, 'admission_required': True,
                    'coupled_edits': ([{'path': GUARD_PATH, 'old': OLD_GUARD, 'new': NEW_GUARD, 'count': 1,
                                       'when': 'GPOOL > 0', 'scope': 'Observed source condition; paired admission required.'}]
                                      if first['name'] == 'GPOOL' and 'ordinary_maj' in guard_values else []),
                    'rule_id': f"discovered.{tree['id']}.{first['name'].lower()}_{first['occurrence']}"}
            tree['dials'].append(dial)
        trees.append(tree)
    return trees


def propose_setting_jobs(trees, curve=None, *, values=None, owner='unassigned', limit=64):
    """Create counted source edits within observed ranges; keep unknown costs null."""
    if type(limit) is not int or limit < 1:
        raise ValueError('The proposal limit must be positive')
    jobs = []
    for tree in trees:
        base = tree['base']
        constants = {c['key']: c for c in base['source']['constants']}
        for dial in tree['dials']:
            if dial['classification'] != 'known_layout':
                continue
            c = constants[dial['key']]
            if c['match_count'] != 1:
                continue
            proposed = values.get(dial['rule_id'], []) if values is not None else range(dial['minimum'], dial['maximum']+1)
            for value in itertools.islice(proposed, limit):
                if type(value) is not int or not dial['minimum'] <= value <= dial['maximum']:
                    raise ValueError('A setting proposal is outside the observed integer range')
                if value == c['value']:
                    continue
                qfit = dial['Q_fit']
                if qfit['status'] != 'fitted' or qfit['maximum_absolute_residual'] > 1e-8:
                    continue
                q = qfit['intercept'] + qfit['slope']*value
                if abs(q-round(q)) > 1e-8 or q <= 0:
                    continue
                q = round(q)
                offset = c['literal_offset']
                replacement = c['statement'][:offset] + str(value) + c['statement'][offset+len(c['literal']):]
                edits = [{'path': c['path'], 'old': c['statement'], 'new': replacement, 'count': 1}]
                for coupled in dial['coupled_edits']:
                    guards = base['source']['coupled_guards']
                    if value > 0 and any(g['path'] == coupled['path'] and g['name'] == 'inherited' for g in guards):
                        edits.append({k: coupled[k] for k in ('path', 'old', 'new', 'count')})
                tfit = dial['T_fit']
                estimate = (tfit['intercept'] + tfit['slope']*value
                            if tfit['status'] == 'fitted' and not dial['confounders'] else None)
                best = best_at_or_below(curve, q) if curve is not None else None
                dependencies = [{'role': 'parent', 'submission_id': base['submission_id'],
                                 'rationale': 'Pinned public base for the discovered setting.',
                                 'evidence': f"Commit {base['commit']}; tree hash {base['source']['tree_sha256']}"}]
                seen_sources = {base['submission_id']}
                for point in dial['points']:
                    if point['submission_id'] not in seen_sources:
                        dependencies.append({'role': 'setting', 'submission_id': point['submission_id'],
                                             'rationale': 'Source of the observed layout setting and any coupled guard.',
                                             'evidence': f"{dial['path']}: {dial['name']}={point['value']}; commit {point['commit']}"})
                        seen_sources.add(point['submission_id'])
                raw = {'id': f"{tree['id']}-{c['name'].lower()}-{c['occurrence']}-{value}", 'base_commit': base['commit'],
                       'Q': q, 'owner': owner, 'state': 'planned', 'edits': edits, 'file_copies': [],
                       'base_files_sha256': base['source']['files_sha256'],
                       'expected_T_inference': estimate if estimate is None or estimate > 0 else None,
                       'must_beat': best['T'] if best else None,
                       'dependencies': dependencies,
                       'metadata': {'tree': tree['id'], 'kind': 'setting', 'rule_id': dial['rule_id'],
                                    'family': 'froghop',
                                    'actions': [{'rule': dial['rule_id'], 'parameters': {'value': value, 'guards': True}}],
                                    'setting': {'name': c['name'], 'value': value}, 'admission_required': True,
                                    'expected_Q_scope': 'Linear inference; the emitter must confirm Q.',
                                    'expected_T_scope': 'Unknown when recorded source changes confound the setting fit.',
                                    'confounders': dial['confounders'], 'nonce_and_tail': 'Keep every base value unchanged.',
                                    'binding': {'fast': None, 'fast_reason': 'secp256k1 layout literal',
                                                'large': 'frontier.counted_setting'}}}
                if 'parent_reliability' in base:
                    raw['metadata']['parent_reliability'] = copy.deepcopy(base['parent_reliability'])
                jobs.append(normalize_job(raw))
                if len(jobs) == limit:
                    return jobs
    return jobs


def propose_addendum_jobs(addenda, curve=None, *, values=None, owner='unassigned', limit=64):
    """Propose jobs from the source-bound recipes carried by grammar addenda."""
    trees, references = [], {}
    for document in addenda:
        for rule in document['rules']:
            if rule.get('kind') != 'setting' or rule.get('status') != 'executable':
                continue
            if rule['id'] in references:
                raise ValueError('A discovered rule must have one addendum definition')
            document_hash = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                                      ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            references[rule['id']] = {'id': document['id'], 'revision': document['revision'], 'sha256': document_hash}
            entries = [entry for entry in rule['evidence'] if 'setting_recipe' in entry]
            if len(entries) != 1:
                raise ValueError('A discovered setting needs one source-bound recipe')
            entry = entries[0]
            recipe = copy.deepcopy(entry['setting_recipe'])
            if recipe.get('schema') != 'lco-discovered-setting-v1':
                raise ValueError('Unknown discovered-setting recipe schema')
            base, dial = recipe['base'], recipe['dial']
            source = base['source']
            constant = source['constants'][0]
            if (entry['commit'] != base['commit'] or entry['path'] != constant['path']
                    or entry['sha256'] != constant['file_sha256']
                    or source['files_sha256'].get(entry['path']) != entry['sha256']
                    or source['tree_sha256'] != digest(source['files_sha256'])
                    or dial['rule_id'] != rule['id'] or dial['key'] != constant['key']):
                raise ValueError('The setting recipe and source evidence disagree')
            parameter = rule['parameters']['value']
            if (parameter.get('type') != 'integer' or parameter.get('minimum') != dial['minimum']
                    or parameter.get('maximum') != dial['maximum'] or parameter.get('default') != constant['value']
                    or rule['parameters'].get('guards', {}).get('enum') != [True]):
                raise ValueError('The setting recipe and parameter contract disagree')
            trees.append({'id': recipe['tree_id'], 'base': base, 'dials': [dial]})
    jobs = propose_setting_jobs(trees, curve, values=values, owner=owner, limit=limit)
    for job in jobs:
        job['metadata']['grammar_refs'] = [references[job['metadata']['rule_id']]]
    return jobs


def discover_trees(rows, git_repo, out_dir=None, *, runner=subprocess.run, fetch_missing=None, owner='unassigned', limit=64,
                   audit_evidence=None):
    """Read immutable public sources and emit family addenda and proposed jobs."""
    if isinstance(rows, dict) and 'submissions' in rows:
        public_rows = select_frontier(rows['submissions'])[2]
    else:
        public_rows = rows.get('curve', []) if isinstance(rows, dict) else rows
    if not isinstance(public_rows, list):
        raise ValueError('Discovery requires a row list or public snapshot')
    records, cache, sources = [], {}, {}
    for row in public_rows:
        if 'status' in row and not eligible(row):
            continue
        commit = row.get('submissionCommitSha') or row.get('commit') or row.get('base_commit')
        if commit not in sources:
            resolved, files = read_source(git_repo, commit, runner=runner, fetch_missing=fetch_missing)
            sources[commit] = inspect_source(files, resolved, cache=cache)
        records.append(_row_record(row, sources[commit]))
    ranking = rank_parent_reliability(records, audit_evidence=audit_evidence)
    by_parent = {entry['submission_id']: entry for entry in ranking}
    for record in records:
        record['parent_reliability'] = by_parent[record['submission_id']]
    trees = analyze_sources(records)
    addenda = [{'schema_version': 1, 'id': f"family.{tree['id']}", 'revision': 1,
                'title': f"Discovered settings for {tree['id']}",
                'description': 'Source-bound layout settings with full-width evaluation and explicit admission requirements.',
                'definitions': {'setting': 'A counted integer edit with source conditions and unchanged nonce choices.'},
                'evidence': [{'path': 'src/point_add', 'sha256': tree['base']['source']['tree_sha256'],
                              'commit': tree['base']['commit'], 'symbol': 'Point-addition file-hash manifest'}],
                'rules': [_dial_rule(tree, dial) for dial in tree['dials']]} for tree in trees]
    curve = [{'Q': r['Q'], 'T': r['T'], 'id': r['submission_id']} for r in records]
    jobs = propose_addendum_jobs(addenda, curve, owner=owner, limit=limit)
    result = {'schema': 'lco-public-trees-v1', 'trees': trees, 'family_addenda': addenda, 'jobs': jobs,
              'parent_reliability_ranking': ranking,
              'scope': 'Source discovery and proposed edits. No circuit equivalence or qualification is claimed.'}
    if out_dir is not None:
        root = Path(out_dir)
        root.mkdir(parents=True, exist_ok=True)
        save(root/'trees.json', result)
        save(root/'jobs.json', {'schema': 'lco-discovered-jobs-v1', 'jobs': jobs})
        for addendum in addenda:
            save(root/'families'/f"{addendum['id']}.json", addendum)
    return result
