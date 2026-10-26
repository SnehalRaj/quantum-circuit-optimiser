"""Configure and record a complete experiment without a hosted service."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .research_policy import DEFAULT_EPSILON

from . import engine, providers, search_space, study, study_formats, study_report, study_transfer

PRESETS = {
    'smoke': {'bits': [5], 'trials': 32, 'seeds': [11], 'calibration_size': 16,
              'audit_pairs': 8192, 'epsilon': DEFAULT_EPSILON, 'transfer': False},
    'research': {'bits': [16], 'trials': 100, 'seeds': [11, 29, 47], 'calibration_size': 64,
                 'audit_pairs': 16384, 'epsilon': DEFAULT_EPSILON, 'transfer': True},
}
COMMON = {'schema': 1, 'provider': 'claude', 'model': 'claude-opus-5-5',
          'strategies': ['random', 'evolution', 'llm'], 'format_arms': [],
          'review_rounds': [5, 25], 'max_model_calls': 2, 'wall_seconds': 900,
          'reasoning_effort': 'medium', 'model_timeout': 180,
          'transfer_pairs': 32, 'allow_approximate': True, 'grammar':'recipes-v1', 'proposal_policy':'historical-v1'}


def configuration(preset='research', **overrides):
    """Return a validated configuration. Unknown fields are errors."""
    if preset not in PRESETS: raise ValueError('Choose the smoke or research preset')
    config = copy.deepcopy({**COMMON, **PRESETS[preset]})
    unknown = set(overrides) - set(config) - {'preset'}
    if unknown: raise ValueError('Unknown configuration fields: ' + ', '.join(sorted(unknown)))
    config.update(overrides)
    config['preset'] = preset
    from .study_grammar import GRAMMARS
    if config['grammar'] not in GRAMMARS:raise ValueError('Unknown study grammar')
    from .study_policy import POLICIES
    if config['proposal_policy'] not in POLICIES:raise ValueError('Unknown proposal policy')
    if config['proposal_policy']=='guarded-seeds-v1' and config['grammar']!='blocks-v1':raise ValueError('Guarded seeds require the block grammar')
    if type(config['schema']) is not int or config['schema'] != 1: raise ValueError('Unsupported experiment schema')
    widths, seeds = config['bits'], config['seeds']
    if not isinstance(widths, list) or not widths or any(type(b) is not int or b not in [5, 16] for b in widths) or len(set(widths)) != len(widths):
        raise ValueError('Search widths are 5 and 16; 256-bit transfer is a separate check')
    if not isinstance(seeds, list) or not seeds or any(type(s) is not int for s in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError('Give distinct integer search seeds')
    for key in ['trials', 'calibration_size', 'audit_pairs', 'transfer_pairs']:
        if type(config[key]) is not int or config[key] < 1: raise ValueError(key + ' must be a positive integer')
    if config['calibration_size'] < 8 or config['calibration_size'] % 2: raise ValueError('Calibration requires an even pool of at least eight recipes')
    for key in ['wall_seconds', 'model_timeout']:
        if type(config[key]) not in [int, float] or not 0 < config[key] < float('inf'): raise ValueError(key + ' must be positive and finite')
    if type(config['epsilon']) not in [int, float] or not 0 < config['epsilon'] < 1: raise ValueError('epsilon must lie between zero and one')
    if config['provider'] not in ['offline', 'claude', 'codex', 'anthropic', 'openai']: raise ValueError('Unknown provider')
    strategies = config['strategies']
    if config['provider'] == 'offline' and 'strategies' not in overrides: strategies = config['strategies'] = ['random', 'evolution']
    if not isinstance(strategies, list) or not strategies or len(set(strategies)) != len(strategies) or not set(strategies) <= {'random', 'evolution', 'llm'}:
        raise ValueError('Choose distinct supported strategies')
    if 'llm' in strategies and (config['provider'] == 'offline' or not isinstance(config['model'], str) or not config['model'].strip()):
        raise ValueError('Model feedback requires a live provider and explicit model')
    formats = config['format_arms']
    if not isinstance(formats, list) or len(set(formats)) != len(formats) or not set(formats) <= {'text', 'columnar_json'}:
        raise ValueError('Unsupported context comparison')
    if formats and (5 not in widths or 'llm' not in strategies): raise ValueError('Context comparison requires a 5-bit model arm')
    rounds = config['review_rounds']
    if not isinstance(rounds, list) or len(set(rounds)) != len(rounds) or any(type(n) is not int or n < 1 for n in rounds): raise ValueError('Review rounds must be distinct positive integers')
    if type(config['max_model_calls']) is not int or config['max_model_calls'] < 0: raise ValueError('The model-call cap must be nonnegative')
    if 'llm' in strategies and (not config['max_model_calls'] or not any(n <= config['trials'] for n in rounds)):
        raise ValueError('The model arm must have at least one scheduled review')
    if config['reasoning_effort'] not in ['low', 'medium', 'high', 'xhigh', 'max']: raise ValueError('Unknown reasoning effort')
    if any(type(config[k]) is not bool for k in ['transfer', 'allow_approximate']): raise ValueError('Use boolean transfer and approximation settings')
    if not config['allow_approximate']: config['calibration_size'] = min(config['calibration_size'], 12)
    return config


def _save_source(root):
    """Copy only project source and declared dependency records."""
    package = Path(__file__).parent
    project = package.parent.parent
    target = root / 'source'
    shutil.copytree(package, target / 'src/circuitopt', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ['pyproject.toml', 'requirements.lock', 'requirements-study.lock', 'README.md', 'AGENTS.md']:
        if (project / name).is_file(): shutil.copy2(project / name, target / name)
    for name in ['tests', 'docs']:
        if (project / name).is_dir(): shutil.copytree(project / name, target / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    fixture=project/'runs/example-comparison'
    if fixture.is_dir():shutil.copytree(fixture,target/'runs/example-comparison')
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=project, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=project, capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.SubprocessError): commit, dirty = None, None
    study.save(root / 'source-origin.json', {'git_commit': commit, 'tracked_changes_present': dirty,
        'authority': 'The copied source and source-manifest hashes define this experiment implementation.'})
    tool_versions={}
    for tool in ['claude','codex']:
        if not shutil.which(tool):tool_versions[tool]=None;continue
        try:
            result=subprocess.run([tool,'--version'],capture_output=True,text=True,timeout=10,check=False)
            tool_versions[tool]=result.stdout.strip()[:200] if result.returncode==0 else None
        except (OSError,subprocess.SubprocessError):tool_versions[tool]=None
    study.save(root / 'tool-versions.json',tool_versions)
    files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(target.rglob('*')) if p.is_file()}
    study.save(root / 'source-manifest.json', files)


def initialise(root, config):
    root = Path(root)
    config = configuration(config.get('preset', 'research'), **{k: v for k, v in config.items() if k != 'preset'})
    active = search_space.ACTIVE_RULES if config['allow_approximate'] else [r for r in search_space.ACTIVE_RULES if r not in ['R18', 'R19', 'R20']]
    if config['grammar']=='blocks-v1':active=active+['R21']
    study.initialise(root, **{k: config[k] for k in ['bits', 'trials', 'seeds', 'provider', 'model',
        'calibration_size', 'audit_pairs', 'epsilon', 'strategies', 'format_arms', 'review_rounds',
        'max_model_calls', 'wall_seconds', 'reasoning_effort', 'model_timeout', 'grammar', 'proposal_policy']}, active_rules=active)
    study.save(root / 'experiment.json', config, True)
    (root / 'experiment.sha256').write_text(engine.digest(config) + '\n')
    study.save(root / 'experiment-state.json', {'status': 'initialised', 'updated_utc': _now()})
    _save_source(root)
    study.save(root / 'reproduce.json', {'schema': 1, 'config_file': 'experiment.json',
        'initialise': ['circuit-opt', 'experiment', 'init', 'runs/repeated', '--config', 'experiment.json'],
        'execute': ['circuit-opt', 'experiment', 'run', 'runs/repeated'],
        'note': 'Search seeds reproduce proposal randomness. Models and fresh audits remain stochastic. Replay uses the saved gates and inputs.'})
    from .snapshot import write
    write(root)
    return config


def _now(): return datetime.now(timezone.utc).isoformat()


def verify_rules(root):
    """Run the independent quantum checks once before this experiment."""
    root = Path(root)
    tests = root / 'source/tests'
    start = time.monotonic()
    patterns=['test_quantum_semantics.py']
    if study.load(root/'experiment.json').get('grammar')=='blocks-v1':
        patterns+=['test_constant_kernel.py','test_block_program.py']
    missing=[pattern for pattern in patterns if not (tests/pattern).is_file()]
    if missing:raise ValueError('Required verification files are missing: '+', '.join(missing))
    commands=[];outputs=[];returncodes=[]
    for pattern in patterns:
        command=[sys.executable,'-m','unittest','discover','-s',str(tests.resolve()),'-p',pattern,'-v']
        proc=subprocess.run(command,text=True,capture_output=True,timeout=180)
        commands.append(['python','-m','unittest','discover','-s','source/tests','-p',pattern,'-v'])
        outputs.append(proc.stdout+proc.stderr);returncodes.append(proc.returncode)
    target = root / 'verification'; target.mkdir(exist_ok=True)
    (target / 'quantum-tests.txt').write_text('\n'.join(outputs))
    receipt = {'status': 'passed' if not any(returncodes) else 'failed', 'returncode': max(returncodes),
        'wall_seconds': time.monotonic() - start, 'command':commands[0], 'commands':commands,
        'scope': 'Independent small quantum tests. These do not prove every arithmetic parameter or full circuit.'}
    study.save(target / 'receipt.json', receipt)
    if any(returncodes): raise ValueError('Quantum rule checks failed; see verification/quantum-tests.txt')
    return receipt


def preflight(root, config):
    """Check the requested provider and preserve every connection attempt."""
    directory = Path(root) / 'preflight'; directory.mkdir(exist_ok=True)
    attempt = len(list(directory.glob('*-receipt.json'))) + 1
    answer, receipt = providers.generate(config['provider'], config['model'],
        'Connection check only. Return {"proposals":[]}. Do not call tools.',
        timeout=config['model_timeout'], reasoning_effort=config['reasoning_effort'], isolated=True,
        receipt_path=directory/f'{attempt}-receipt.json')
    models = receipt.get('resolved_models', [])
    if models and config['provider'] == 'claude' and config['model'] not in models:
        receipt.update(status='failed', error='RequestedModelMismatch')
    study.save(directory / f'{attempt}.json', {'answer': answer, 'receipt': receipt})
    study.append(Path(root) / 'costs.jsonl', {'kind': 'model', 'stage': 'preflight', **receipt})
    return receipt


def run(root, offline_only=False):
    """Resume completed phases. Preserve incomplete scientific phases for inspection."""
    from .cli import inventory
    from .snapshot import write
    root = Path(root); study.check(root)
    config = study.load(root / 'experiment.json')
    if engine.digest(config) != (root / 'experiment.sha256').read_text().strip():
        raise ValueError('The saved experiment configuration changed; create a new experiment')
    for name, digest in study.load(root / 'source-manifest.json').items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('The copied experiment source changed: ' + name)
    current_phase = 'startup'

    class ProviderBlocked(ValueError): pass

    def model_search(bits, seed, format_name='compact_json'):
        result=study.search(root,bits,'llm',seed,format_name)
        if result['status']=='provider_blocked':
            raise ProviderBlocked('A feedback call lost authentication or used another model. Preserve this run and start a new experiment after the connection is fixed.')
        return result

    def state(status, **extra):
        study.save(root / 'experiment-state.json', {'status': status, 'phase': current_phase, 'updated_utc': _now(), **extra})
        write(root)

    def phase(name, action):
        nonlocal current_phase
        current_phase = name
        target = root / 'phases' / (name + '.json')
        if target.exists():
            old = study.load(target)
            if old['status'] == 'complete': return
            raise ValueError('Inspect the incomplete phase before any retry: ' + name)
        start = time.monotonic()
        study.save(target, {'status': 'running', 'started_utc': _now()})
        state('running')
        print('Start ' + name, flush=True)
        try: action()
        except Exception as error:
            study.save(target, {'status': 'failed', 'error': str(error), 'wall_seconds': time.monotonic() - start})
            raise
        study.save(target, {'status': 'complete', 'wall_seconds': time.monotonic() - start})
        write(root)
        print('Complete ' + name, flush=True)

    try:
        phase('1-rule-verification', lambda: verify_rules(root))
        if not study.check(root)['calibration']: phase('2-calibration', lambda: study.calibrate(root))
        for bits in config['bits']:
            for seed in config['seeds']:
                for strategy in ['random', 'evolution']:
                    if strategy in config['strategies']:
                        phase(f'3-{bits}-{strategy}-{seed}', lambda b=bits, s=strategy, k=seed: study.search(root, b, s, k))
        if offline_only:
            study_report.report(root); state('offline_complete'); inventory(root)
            return study.load(root / 'experiment-state.json')
        if 'llm' in config['strategies']:
            incomplete = [rid for rid in study.planned_runs(study.check(root)) if not (root / 'runs' / rid / 'run.json').exists()]
            if incomplete:
                current_phase = '4-provider-connection'
                receipt = preflight(root, config)
                if receipt['status'] != 'completed':
                    study_report.report(root)
                    state('provider_blocked', error=receipt.get('error'), action='Sign in with the provider CLI, then repeat the experiment run command.')
                    inventory(root)
                    return study.load(root / 'experiment-state.json')
            for bits in config['bits']:
                for seed in config['seeds']:
                    phase(f'4-{bits}-llm-{seed}', lambda b=bits, k=seed: model_search(b,k))
        if config['format_arms']:
            for seed in config['seeds']:
                for fmt in config['format_arms']:
                    phase(f'6-format-{fmt}-{seed}', lambda f=fmt, k=seed: model_search(5,k,f))
            phase('6-schema', lambda: study_formats.compare_schemas(root))
        phase('5-freeze', lambda: study.freeze(root))
        phase('5-audit', lambda: study.audit(root))
        if config['transfer']: phase('5-transfer-256', lambda: study_transfer.run(root, pairs=config['transfer_pairs']))
        study_report.report(root)
        state('complete')
        from .snapshot import render_figures
        render_figures(root)
        inventory(root)
        return study.load(root / 'experiment-state.json')
    except ProviderBlocked as error:
        study_report.report(root)
        state('provider_blocked',error=str(error),action=str(error));inventory(root)
        return study.load(root/'experiment-state.json')
    except Exception as error:
        state('failed', error=str(error)); inventory(root)
        raise
