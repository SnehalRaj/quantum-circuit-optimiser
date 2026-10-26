"""Command line for the complete local workflow."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import zlib
from pathlib import Path
import shutil
import sys

from . import engine
from .research_policy import DEFAULT_EPSILON


def inventory(root):
    root=Path(root)
    records={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob('*')) if p.is_file() and p.name!='files.sha256.json'
             and '.git' not in p.parts and '__pycache__' not in p.parts}
    engine.save(root/'files.sha256.json',records)
    return records


def main(argv=None):
    arguments=list(sys.argv[1:] if argv is None else argv)
    if arguments[:2]==['targeted-frontier','skywalk']:
        from .targeted_skywalk import main as skywalk_main
        return skywalk_main(arguments[2:])
    if arguments and arguments[0]=='targeted-frontier':
        from .targeted_frontier import main as targeted_main
        return targeted_main(arguments[1:])
    argv=arguments
    parser=argparse.ArgumentParser(description='Reproducible reversible-circuit search')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('targeted-frontier',help='Emit source-pinned native frontier constructors')
    from . import rules_cli
    rules_cli.register(sub)
    from . import study_cli
    study_cli.register(sub)
    from . import experiment_cli
    experiment_cli.register(sub)
    from . import frontier_cli
    frontier_cli.register(sub)
    from . import point_add
    point_add.register(sub)
    init=sub.add_parser('init',help='Start Task 1, or create an explicit runnable example')
    init.add_argument('directory',type=Path)
    init.add_argument('--example',action='store_true')
    init.add_argument('--bits',type=int,choices=[5,16,256],default=16)
    check=sub.add_parser('validate')
    check.add_argument('contract',type=Path)
    run=sub.add_parser('search')
    run.add_argument('contract',type=Path)
    run.add_argument('output',type=Path)
    run.add_argument('--strategy',choices=['random','evolution','llm'],default='random')
    run.add_argument('--provider',choices=['offline','openai','anthropic','codex','claude'],default='offline')
    run.add_argument('--model')
    run.add_argument('--attempts',type=int,default=8)
    run.add_argument('--seed',type=int,default=0)
    run.add_argument('--batch-size',type=int,default=4)
    run.add_argument('--wall-seconds',type=float,default=3600)
    run.add_argument('--max-model-calls',type=int,default=4)
    freeze=sub.add_parser('freeze')
    freeze.add_argument('output',type=Path)
    freeze.add_argument('--run',type=Path,action='append',required=True)
    freeze.add_argument('--pairs',type=int,default=1048576)
    audit=sub.add_parser('audit')
    audit.add_argument('directory',type=Path)
    report=sub.add_parser('report')
    report.add_argument('directory',type=Path)
    profile=sub.add_parser('profile')
    profile.add_argument('directory',type=Path)
    replay=sub.add_parser('replay')
    replay.add_argument('circuit',type=Path)
    replay.add_argument('inputs',type=Path)
    replay.add_argument('--output',type=Path,required=True)
    verify=sub.add_parser('verify')
    verify.add_argument('directory',type=Path)
    sub.add_parser('doctor')
    args=parser.parse_args(argv)
    try:
        if args.command=='rules':
            rules_cli.run(args)
        elif args.command=='study':
            study_cli.run(args)
        elif args.command=='experiment':
            experiment_cli.run(args)
        elif args.command=='frontier':
            frontier_cli.run(args)
        elif args.command=='point-add':
            point_add.command(args)
        elif args.command=='init':
            args.directory.mkdir(parents=True,exist_ok=False)
            contract=engine.example_contract(args.bits) if args.example else {
              'schema':1,'name':'my-circuit-study','frozen':False,'interface':'complete_point_addition',
              'epsilon':DEFAULT_EPSILON,'alpha':.05,'development_label':'development-v1',
              'samples':{'quick':1024,'development':8192},'baselines':[], 'grammar':{},
              'evaluator':{'kind':'command','argv':['python3','evaluate.py'],'source_sha256':{}}}
            engine.save(args.directory/'contract.json',contract,True)
            print(f"Created {args.directory/'contract.json'}. Complete Task 1 before search.")
        elif args.command=='validate':
            engine.validate(engine.load(args.contract))
            engine.source_fingerprint(engine.load(args.contract),args.contract.parent)
            print('Task contract is ready for search.')
        elif args.command=='search':
            if args.attempts<1 or args.batch_size<1 or args.wall_seconds<=0 or args.max_model_calls<0:
                raise ValueError('Budgets and batch size must be positive; model-call cap must be nonnegative')
            result=engine.search(args.contract,args.output,args.strategy,args.attempts,args.seed,
                    args.provider,args.model,args.batch_size,args.wall_seconds,args.max_model_calls)
            engine.save(args.output/'environment.json',{'python':sys.version,
                        'scipy':importlib.metadata.version('scipy'),'platform':platform.platform(),
                        'zlib':zlib.ZLIB_RUNTIME_VERSION,'source_fingerprint':result['source_fingerprint']})
            inventory(args.output)
            print(f"Saved {len(result['baselines'])} baseline runs to {args.output}")
        elif args.command=='freeze':
            result=engine.freeze(args.run,args.output,args.pairs)
            inventory(args.output)
            print(f"Frozen {result['comparison_size']} baseline/candidate evaluations before audit inputs exist.")
        elif args.command=='audit':
            result=engine.audit(args.directory)
            inventory(args.directory)
            print(json.dumps(result['strategy_rewards'],indent=2))
        elif args.command=='report':
            engine.report(args.directory)
            print(args.directory/'REPORT.md')
        elif args.command=='profile':
            calls=engine.load(args.directory/'run.json')['model_calls']
            completed=[c for c in calls if c.get('usage') is not None]
            actual=[c for c in calls if c['provider']!='offline']
            result={'calls':len(actual),'offline_review_batches':len(calls)-len(actual),'failed_calls':sum(c['status']!='completed' for c in actual),
              'calls_with_unknown_usage':sum(c.get('usage') is None for c in calls),
              'observed_input_tokens':sum(c['usage'].get('input_tokens',0) or 0 for c in completed),
              'observed_output_tokens':sum(c['usage'].get('output_tokens',0) or 0 for c in completed),
              'observed_cached_input_tokens':sum(c['usage'].get('cached_input_tokens',0) or 0 for c in completed),
              'model_wall_seconds':sum(c['wall_seconds'] for c in calls),
              'usage_scope':'All calls with reported usage, including failed calls. Unknown usage is not zero usage.'}
            engine.save(args.directory/'TOKEN-REPORT.json',result)
            inventory(args.directory)
            print(json.dumps(result,indent=2))
        elif args.command=='replay':
            from .division import replay_evidence
            result=replay_evidence(args.circuit,args.inputs)
            engine.save(args.output,result,True)
            print(json.dumps(result,indent=2))
        elif args.command=='verify':
            manifest=engine.load(args.directory/'files.sha256.json')
            for name,expected in manifest.items():
                p=args.directory/name
                if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
                    raise ValueError(f'Artifact changed or missing: {name}')
            print(f'Verified {len(manifest)} saved files.')
        elif args.command=='doctor':
            print(json.dumps({'python':sys.version.split()[0],'scipy':importlib.metadata.version('scipy'),
                   'codex_cli':bool(shutil.which('codex')),'claude_cli':bool(shutil.which('claude')),
                   'live_provider_check':'Not performed; no model calls made.'},indent=2))
    except (ValueError,OSError,KeyError) as exc:
        parser.exit(2,f'{type(exc).__name__}: {exc}\n')

if __name__=='__main__':
    main()
