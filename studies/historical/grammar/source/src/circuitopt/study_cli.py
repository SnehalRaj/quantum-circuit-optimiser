"""CLI entry points for reproducible search studies."""
from pathlib import Path
from . import study,study_report


def register(sub):
    parser=sub.add_parser('study',help='Run a measured search study and show live progress')
    commands=parser.add_subparsers(dest='study_command',required=True)
    p=commands.add_parser('init');p.add_argument('directory',type=Path)
    p.add_argument('--bits',nargs='+',type=int,default=[5,16]);p.add_argument('--trials',type=int,default=100)
    p.add_argument('--seeds',nargs='+',type=int,default=[11,29,47]);p.add_argument('--provider',default='codex');p.add_argument('--model',required=True)
    for name in ['calibrate','freeze','audit','report','run','schemas','transfer']:
        p=commands.add_parser(name);p.add_argument('directory',type=Path)
    p=commands.add_parser('search');p.add_argument('directory',type=Path);p.add_argument('--bits',type=int,required=True)
    p.add_argument('--strategy',choices=['random','evolution','llm'],required=True);p.add_argument('--seed',type=int,required=True)
    p.add_argument('--format',choices=['text','compact_json','columnar_json'],default='compact_json')


def run(args):
    action=args.study_command
    if action=='init':
        study.initialise(args.directory,bits=args.bits,trials=args.trials,seeds=args.seeds,provider=args.provider,model=args.model)
        study_report.write_dashboard(args.directory)
    elif action=='search':study.search(args.directory,args.bits,args.strategy,args.seed,args.format)
    elif action=='run':
        from .study_pipeline import run
        run(args.directory)
    elif action=='schemas':
        from .study_formats import compare_schemas
        compare_schemas(args.directory)
    elif action=='transfer':
        from .study_transfer import run
        run(args.directory)
    elif action=='report':study_report.report(args.directory)
    else:getattr(study,action)(args.directory)
    print(args.directory/('dashboard.html' if action=='init' else 'REPORT.md' if action=='report' else 'study.json'))
