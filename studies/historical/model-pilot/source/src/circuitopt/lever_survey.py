"""Survey public sources for exact pass levers that the author left off.

A source is a directory tree or a .tar.gz archive of one public row. The survey reads text
files only; it never extracts or runs anything. For each pass family in the pattern file it
reports every fixed lever string (for example `cond_pass::run_with(ops, "mfs")`), every
environment setting (for example `SL_COND=mfsr`), and the documented levers that are off.
It also reports post-pass modules whose files are present but whose entry call never occurs
in a compiled file.

A Rust file counts as compiled (`declared`) when it is a crate root (main.rs, lib.rs, build.rs,
or a file under bin/), when another file declares its module (`mod name;`), or when a
`#[path = "..."]` attribute names it. This is a text heuristic. It does not follow cfg flags.
The patterns live in data/levers/exact-pass-levers.json. Add a family there to survey another pass.
A text match reports a setting. It does not prove that a lever is exact for that source.
"""
import argparse
import hashlib
from importlib import resources
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

MAX_TEXT_BYTES = 4 << 20
MOD_DECLARATION = re.compile(r'\bmod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;')
PATH_ATTRIBUTE = re.compile(r'#\[\s*path\s*=\s*"([^"]+)"\s*\]')
CRATE_ROOTS = {'main.rs', 'lib.rs', 'build.rs'}


def load_patterns(path=None):
    if path:
        return json.loads(Path(path).read_text())
    return json.loads(resources.files('circuitopt').joinpath('data/levers/exact-pass-levers.json').read_text())


def _suffix_match(name, endings):
    return any(name == e or name.endswith('/' + e) for e in endings)


def text_files(source, suffixes):
    """Yield (relative path, text) for each readable text file with a listed suffix."""
    source = Path(source)
    if source.is_dir():
        for path in sorted(source.rglob('*')):
            if path.is_file() and path.suffix in suffixes and path.stat().st_size <= MAX_TEXT_BYTES:
                try:
                    yield path.relative_to(source).as_posix(), path.read_text()
                except UnicodeDecodeError:
                    continue
        return
    with tarfile.open(source) as tree:
        for member in sorted(tree.getmembers(), key=lambda m: m.name):
            if not member.isfile() or PurePosixPath(member.name).suffix not in suffixes or member.size > MAX_TEXT_BYTES:
                continue
            try:
                yield member.name, tree.extractfile(member).read().decode()
            except UnicodeDecodeError:
                continue


def comment_prefixes(name, patterns):
    table = patterns.get('comment_prefixes', {})
    return tuple(table.get(PurePosixPath(name).suffix, table.get('default', ['#'])))


def code_lines(name, text, patterns):
    """Yield (line number, line) for lines that do not start with a comment prefix."""
    prefixes = comment_prefixes(name, patterns)
    for number, line in enumerate(text.splitlines(), 1):
        if not line.lstrip().startswith(prefixes):
            yield number, line


def _path_key(path):
    """Compare a mod.rs by its folder and file name, and any other file by its name."""
    return path.parts[-2:] if path.name == 'mod.rs' else path.parts[-1:]


def compiled_files(files, patterns):
    """Return the names of files that count as compiled, by the heuristic in the module text."""
    names, targets = set(), set()
    for name, text in files:
        if name.endswith('.rs'):
            for _, line in code_lines(name, text, patterns):
                names.update(MOD_DECLARATION.findall(line))
                targets.update(_path_key(PurePosixPath(t)) for t in PATH_ATTRIBUTE.findall(line))
    result = set()
    for name, _ in files:
        path = PurePosixPath(name)
        if path.suffix != '.rs':
            result.add(name)  # scripts and configuration files take effect without a module declaration
            continue
        module = path.parent.name if path.name == 'mod.rs' else path.stem
        if path.name in CRATE_ROOTS or 'bin' in path.parts[:-1] or module in names or _path_key(path) in targets:
            result.add(name)
    return result


def resolve(levers, family):
    return family.get('aliases', {}).get(levers, levers)


def lever_state(levers, family):
    documented = list(family['documented_levers'])
    active = resolve(levers, family)
    return {'levers': levers, 'resolved': active, 'on': [x for x in documented if x in active],
            'off': [x for x in documented if x not in active],
            'unknown': sorted({x for x in active if x not in documented})}


def survey_family(files, family, patterns, compiled):
    own = family.get('module_files', [])
    calls, settings, env_calls = [], [], []
    for name, text in files:
        if _suffix_match(name, own):
            continue  # the pass documents its own switches; those lines are not settings
        for number, line in code_lines(name, text, patterns):
            for pattern in family.get('fixed_calls', []):
                for match in re.finditer(pattern['regex'], line):
                    calls.append({'file': name, 'line': number, 'declared': name in compiled, 'kind': 'fixed',
                                  'text': match.group(0), **lever_state(match.group(1), family)})
            for pattern in family.get('setting_calls', []):
                if re.search(pattern['regex'], line):
                    env_calls.append({'file': name, 'line': number, 'declared': name in compiled,
                                      'text': line.strip(), 'setting': pattern['setting']})
            for pattern in family.get('settings', []):
                for match in re.finditer(pattern['regex'], line):
                    settings.append({'file': name, 'line': number, 'declared': name in compiled,
                                     'name': pattern['name'], 'value': match.group(1), 'text': match.group(0)})
    for call in env_calls:
        named = [s for s in settings if s['name'] == call['setting']]
        # Prefer a setting in the calling file; otherwise use the settings in compiled files.
        local = [s for s in named if s['file'] == call['file']]
        chosen = local or [s for s in named if s['declared']]
        values = sorted({s['value'] for s in chosen})
        for value in values or ['']:
            calls.append({**call, 'kind': 'setting', 'levers_source': 'setting' if values else 'unset',
                          **lever_state(value, family)})
    live = [c for c in calls if c['declared']]
    return {'family': family['id'], 'name': family['name'], 'documented_levers': list(family['documented_levers']),
            'calls': calls, 'settings': settings,
            'levers_off_in_every_declared_call': [x for x in family['documented_levers']
                                                  if live and all(x in c['off'] for c in live)]}


def survey_modules(files, modules, patterns, compiled):
    rows = []
    for module in modules:
        own = [name for name, _ in files if _suffix_match(name, module['files'])]
        callers, undeclared = set(), set()
        for name, text in files:
            if name in own:
                continue
            for _, line in code_lines(name, text, patterns):
                if re.search(module['call'], line):
                    (callers if name in compiled else undeclared).add(name)
        rows.append({'module': module['module'], 'files': own, 'present': bool(own),
                     'declared': any(name in compiled for name in own), 'called': bool(callers),
                     'callers': sorted(callers), 'callers_not_compiled': sorted(undeclared),
                     'present_not_called': bool(own) and not callers})
    return rows


def survey_source(source, patterns=None):
    patterns = patterns or load_patterns()
    source = Path(source)
    files = list(text_files(source, set(patterns['scan_suffixes'])))
    compiled = compiled_files(files, patterns)
    result = {'source': source.name, 'kind': 'tree' if source.is_dir() else 'archive', 'files_scanned': len(files),
              'families': [survey_family(files, f, patterns, compiled) for f in patterns['families']],
              'post_pass': survey_modules(files, patterns.get('post_pass_modules', []), patterns, compiled)}
    if not source.is_dir():
        result['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    return result


def survey(sources, patterns_path=None):
    patterns = load_patterns(patterns_path)
    return {'schema': 1, 'patterns_schema': patterns['schema'], 'scope': patterns['scope'],
            'sources': [survey_source(s, patterns) for s in sources]}


def lines(report):
    """Return one readable line per call and per unused post-pass module."""
    out = []
    for item in report['sources']:
        for family in item['families']:
            if not family['calls']:
                out.append(f"{item['source']}: {family['family']}: no call")
            for call in family['calls']:
                place = '' if call['declared'] else ' (file not compiled)'
                out.append(f"{item['source']}: {call['file']}:{call['line']}{place}: {call['text']} "
                           f"on={''.join(call['on']) or '-'} off={''.join(call['off']) or '-'}")
        for module in item['post_pass']:
            if module['present_not_called']:
                out.append(f"{item['source']}: post-pass module {module['module']} is present but never called")
    return out


def add_arguments(parser):
    parser.add_argument('sources', nargs='+', type=Path, help='Source trees or .tar.gz archives of public rows')
    parser.add_argument('--patterns', type=Path, help='Pattern file; default data/levers/exact-pass-levers.json')
    parser.add_argument('--json', action='store_true', help='Print the full JSON report')
    parser.add_argument('--output', type=Path, help='Also write the JSON report to this file')


def run(args):
    for source in args.sources:
        if not source.exists():
            raise ValueError(f'The source does not exist: {source}')
    try:
        report = survey(args.sources, args.patterns)
    except tarfile.TarError as exc:
        raise ValueError(f'An archive could not be read: {exc}') from exc
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2) if args.json else '\n'.join(lines(report)))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_arguments(parser)
    try:
        run(parser.parse_args(argv))
    except (ValueError, OSError, tarfile.TarError) as exc:
        parser.exit(2, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
