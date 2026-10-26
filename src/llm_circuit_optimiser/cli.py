"""Command line interface for composed grammar and staged circuit search."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

from . import __version__
from .common import ROOT, load, save, sha256
from .models import load_config
from .runner import DEFAULT_AUDIT_PAIRS


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('Use a positive integer')
    return number


def emit(value):
    print(json.dumps(value, indent=2, sort_keys=True))


def parser():
    p = argparse.ArgumentParser(prog='lco', description=
        'Compose source-bound grammar, test full16-bit point addition, and build optimized secp256k1 circuits.')
    p.add_argument('--version', action='version', version=__version__)
    sub = p.add_subparsers(dest='command', required=True)
    from .frontier.cli import add_parsers
    add_parsers(sub)
    sub.add_parser('doctor', help='Check tools and source availability without model calls.')
    setup = sub.add_parser('setup', help='Build the native fast emitter and fixed checker.')
    setup.add_argument('--offline', action='store_true', help='Use only cached Rust crates.')
    sub.add_parser('targets', help='List frozen targets and separately marked family controls.')
    family = sub.add_parser('family', help='Inspect or register a source-backed family.').add_subparsers(dest='family_command', required=True)
    family.add_parser('list', help='List built-in families and mapping limits.')
    info = family.add_parser('show', help='Show a built-in or JSON-defined family.')
    info.add_argument('family')
    register = family.add_parser('register', help='Validate a definition and copy it to a local registry.')
    register.add_argument('definition', type=Path)
    register.add_argument('--registry', type=Path, default=Path('work/families'))
    init = family.add_parser('init', help='Create an editable Rust family from a pinned constructor.')
    init.add_argument('--from-target', required=True)
    init.add_argument('--id', required=True)
    init.add_argument('--out', required=True, type=Path)
    refresh = family.add_parser('refresh', help='Freeze deliberate edits to an editable family source.')
    refresh.add_argument('definition', type=Path)
    grammar = sub.add_parser('grammar', help='Compose any number of grammar files.').add_subparsers(dest='grammar_command', required=True)
    compose = grammar.add_parser('compose', help='Validate composition, conflicts, and stage bindings.')
    compose.add_argument('files', nargs='+', type=Path)
    compose.add_argument('--family', default='froghop')
    compose.add_argument('--out', type=Path)
    compose.add_argument('--override', action='append', default=[], metavar='RULE=FILE', help='Choose the source for a conflicting rule.')
    sub.add_parser('verify-data', help='Verify vendored source and cleaned-data hashes.')
    qualify = sub.add_parser('qualify-native', help='Reproduce the four-policy full16-bit qualification study.')
    qualify.add_argument('--pairs', type=positive, default=100000)
    qualify.add_argument('--label', default='lco-native-qualification-20261026-v1')
    qualify.add_argument('--offline', action='store_true')
    qualify.add_argument('--out', type=Path, required=True)
    for name, help_text in [('plan', 'List admitted recipes without circuit generation.'),
                            ('search', 'Run staged model-free or model-assisted search.'),
                            ('reproduce', 'Rebuild frozen targets; this does not claim rediscovery.')]:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument('--family', default='froghop', help='Built-in name or validated family JSON path.')
        cmd.add_argument('--grammar', action='append', type=Path, help='Repeat to compose files. Defaults to base plus the family addendum.')
        cmd.add_argument('--config', type=Path, default=ROOT / 'configs/offline.json', help='Offline, Claude CLI, or Codex CLI JSON configuration.')
        cmd.add_argument('--targets', nargs='+', help='Family targets. Use all for every target in the family.')
        cmd.add_argument('--out', type=Path, required=True, help='Run directory. An unchanged run can resume here.')
        cmd.add_argument('--seed', type=int, default=0)
        cmd.add_argument('--trials', type=positive, default=8, help='Maximum unique recipes. Exhaustion ends the run.')
        cmd.add_argument('--small-pairs', type=positive, default=4096, help='Fast-stage input pairs, with two streams per pair.')
        cmd.add_argument('--large-pairs', type=positive, default=16384, help='Full-width screening pairs, with two streams per pair.')
        cmd.add_argument('--audit-pairs', type=int, default=DEFAULT_AUDIT_PAIRS,
                         help='Fresh final-audit pairs (default: 32768). Zero allows screening only and blocks export.')
        cmd.add_argument('--official', action='store_true', help='Also run the unchanged official local evaluator.')
        cmd.add_argument('--offline-build', action='store_true', help='Use only cached Rust crates.')
        cmd.add_argument('--artifact-root', type=Path, help='Share verified source and operation artifacts across workflows.')
        cmd.add_argument('--strategy', choices=['evolution', 'random'], default='evolution', help='Offline proposal order. LLM mode uses the same menu.')
    export = sub.add_parser('export', help='Package one checked result with source, note, and dependencies.')
    export.add_argument('--run', type=Path, required=True)
    export.add_argument('--slot', type=int, required=True)
    export.add_argument('--out', type=Path, required=True)
    results = sub.add_parser('results', help='Show retained Q/T trade-offs and failed trials.')
    results.add_argument('--run', type=Path, required=True)
    submit = sub.add_parser('submit', help='Show a submission plan, or execute it in a separate benchmark worktree.')
    submit.add_argument('--export', dest='export_dir', type=Path, required=True)
    submit.add_argument('--benchmark', type=Path, required=True)
    submit.add_argument('--execute', action='store_true', help='Run setup, the official local check, and the public submission.')
    return p


def data_verification():
    from .large import verify_sources
    result = {'sources': verify_sources()}
    manifest = ROOT / 'data/cleaned/manifest.json'
    if manifest.is_file():
        files = load(manifest)['files_sha256']
        for name, expected in files.items():
            if sha256(manifest.parent / name) != expected:
                raise ValueError(f'Cleaned-data hash mismatch: {name}')
        result['cleaned_files'] = len(files)
    return result


def resolve(args):
    from .families import load_family
    from .grammar import compose
    from .runner import default_paths
    family = load_family(args.family)
    files = args.grammar or default_paths(family)
    grammar = compose(files)
    target_ids = family['targets'] if not args.targets or args.targets == ['all'] else args.targets
    if len(set(target_ids)) != len(target_ids) or not set(target_ids) <= set(family['targets']):
        raise ValueError('Choose distinct targets from the selected family')
    return family, grammar, load_config(args.config), target_ids


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {'frontier', 'public'}:
            from .frontier.cli import dispatch, exit_status
            result = dispatch(args)
            emit(result)
            return exit_status(args, result)
        elif args.command == 'doctor':
            emit({'python': sys.version.split()[0], 'package': __version__,
                  'cargo': os.environ.get('LCO_CARGO') or shutil.which('cargo'),
                  'claude': shutil.which('claude'), 'codex': shutil.which('codex'),
                  'model_calls': 0, 'repository': str(ROOT),
                  'native_build_helper': (ROOT / 'native/build.sh').is_file(),
                  'rust_environment': 'Set LCO_CARGO and LCO_CARGO_HOME for a cached toolchain.'})
        elif args.command == 'setup':
            from .runner import native_tools
            emit(native_tools(ROOT / 'work/setup', offline=args.offline))
        elif args.command == 'targets':
            from .large import targets
            emit(targets())
        elif args.command == 'verify-data':
            emit(data_verification())
        elif args.command == 'qualify-native':
            from .runner import qualify_native
            result = qualify_native(args.out, pairs=args.pairs, label=args.label, offline=args.offline)
            emit({'qualified': result['qualified'], 'cases': len(result['rows']), 'pairs_per_case': args.pairs})
            return 0 if result['qualified'] else 2
        elif args.command == 'family':
            from .families import builtin_families, load_family, register_family
            if args.family_command == 'list':
                emit(builtin_families())
            elif args.family_command == 'show':
                emit(load_family(args.family))
            elif args.family_command == 'init':
                from .source import init_family
                path = init_family(args.from_target, args.out, family_id=args.id)
                emit({'definition': str(path), 'family': load_family(path)})
            elif args.family_command == 'refresh':
                from .source import refresh_family
                emit(refresh_family(args.definition))
            else:
                path = register_family(args.definition, args.registry)
                emit({'definition': str(path), 'family': load_family(path)})
        elif args.command == 'grammar':
            from .grammar import compose
            overrides = {}
            for item in args.override:
                if '=' not in item:
                    raise ValueError('--override needs RULE=FILE')
                key, value = item.split('=', 1)
                if key in overrides:
                    raise ValueError('An override rule occurs twice')
                overrides[key] = value
            compiled = compose(args.files, overrides=overrides)
            result = compiled.to_dict()
            result['availability'] = compiled.availability(args.family)
            if args.out:
                save(args.out, result)
            emit(result)
        elif args.command in {'plan', 'search', 'reproduce'}:
            from .runner import run_campaign
            family, grammar, config, targets = resolve(args)
            if args.audit_pairs < 0:
                raise ValueError('--audit-pairs must be zero or positive')
            if args.command == 'plan':
                from .grammar import POLICIES
                candidates = []
                for target in targets:
                    for policy in POLICIES:
                        for cancellation in [False, True]:
                            try:
                                candidates.append(grammar.candidate_for_policy(policy, target, family,
                                                                               cancel_adjacent=cancellation))
                            except ValueError:
                                continue
                result = {'family': family, 'grammar_sha256': grammar.sha256,
                          'availability': grammar.availability(family), 'candidates': candidates,
                          'config': config, 'model_calls': 0,
                          'next_stage': 'Run search to propose and evaluate these recipes.'}
                save(args.out / 'plan.json', result)
                emit(result)
            else:
                result = run_campaign(grammar, family, config, args.out, target_ids=targets,
                    mode=args.command, trials=args.trials, seed=args.seed,
                    small_pairs=args.small_pairs, large_pairs=args.large_pairs,
                    audit_pairs=args.audit_pairs, official=args.official,
                    offline_build=args.offline_build, strategy=args.strategy,
                    artifact_root=args.artifact_root)
                emit({k: result[k] for k in ('completed', 'qualified', 'reproduced_exact', 'model_calls', 'menu_exhausted', 'claim')})
                if args.command == 'reproduce':
                    return 0 if result['qualified'] == result['completed'] == len(targets) else 2
                return 0 if result['front'] else 2
        elif args.command == 'export':
            from .submission import export_result
            emit(export_result(args.run, args.slot, args.out))
        elif args.command == 'results':
            result = load(args.run / 'results.json')
            emit({'kind': result['contract']['kind'], 'completed': result['completed'],
                  'qualified': result['qualified'], 'claim': result['claim'],
                  'front': [{k: row[k] for k in ['slot', 'candidate', 'Q', 'T', 'QT', 'ops_sha256']}
                            for row in result['front']],
                  'failed_trials': [{'slot': row['slot'], 'candidate': row['candidate'],
                                     'error': row.get('error'), 'audit': row.get('final_audit', row.get('audit'))}
                                    for row in result['rows'] if not row['qualified']]})
        elif args.command == 'submit':
            from .submission import submit_export
            emit(submit_export(args.export_dir, args.benchmark, execute=args.execute))
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f'lco: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
