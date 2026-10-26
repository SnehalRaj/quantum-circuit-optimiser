"""Commands for a configured experiment and its portable dashboard."""
import json
from pathlib import Path

from . import experiment, snapshot, study


def register(sub):
    parser = sub.add_parser('experiment', help='Configure, run, and save a complete experiment')
    commands = parser.add_subparsers(dest='experiment_command', required=True)
    init = commands.add_parser('init')
    init.add_argument('directory', type=Path)
    init.add_argument('--config', type=Path)
    init.add_argument('--preset', choices=list(experiment.PRESETS))
    init.add_argument('--provider', choices=['offline', 'claude', 'codex', 'anthropic', 'openai'])
    init.add_argument('--model')
    init.add_argument('--bits', nargs='+', type=int)
    init.add_argument('--seeds', nargs='+', type=int)
    init.add_argument('--trials', type=int)
    init.add_argument('--audit-pairs', type=int)
    init.add_argument('--no-transfer', action='store_true')
    run = commands.add_parser('run')
    run.add_argument('directory', type=Path)
    run.add_argument('--offline-only', action='store_true')
    for name in ['snapshot', 'status']:
        action = commands.add_parser(name)
        action.add_argument('directory', type=Path)


def run(args):
    if args.experiment_command == 'init':
        values = study.load(args.config) if args.config else {}
        preset = args.preset or values.pop('preset', 'research')
        values.pop('preset', None)
        for name in ['provider', 'model', 'bits', 'seeds', 'trials', 'audit_pairs']:
            value = getattr(args, name)
            if value is not None: values[name] = value
        if args.no_transfer: values['transfer'] = False
        experiment.initialise(args.directory, experiment.configuration(preset, **values))
        print(args.directory / 'experiment.json')
    elif args.experiment_command == 'run':
        result = experiment.run(args.directory, args.offline_only)
        print(json.dumps(result, indent=2))
        print(args.directory / 'dashboard.html')
        if result['status'] == 'provider_blocked':
            raise ValueError('Provider connection failed. Sign in locally, then repeat the run command. Offline results and the failed receipt are saved.')
    elif args.experiment_command == 'snapshot':
        from .cli import inventory
        snapshot.write(args.directory)
        snapshot.render_figures(args.directory)
        inventory(args.directory)
        print(args.directory / 'dashboard.html')
    else:
        print(json.dumps(study.load(args.directory / 'experiment-state.json'), indent=2))
