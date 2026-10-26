"""Commands for native frontier reconstruction and search."""
import json
import os
from pathlib import Path
import subprocess
import sys

from . import frontier_campaign as campaign, frontier_grammar as grammar


def _frozen_controller(args):
    """Use a study's preserved controller after the working source changes."""
    if args.frontier_command not in ('resume','qualify','report','replay'):return False
    root=args.directory.resolve()
    config=campaign.integrity.read_sealed(root/'config.json')
    sources=config['source_hashes']
    if all((campaign.PROJECT/name).is_file() and campaign.native.file_hash(campaign.PROJECT/name)==sha for name,sha in sources.items()):return False
    for name,sha in sources.items():campaign.native.assert_hash(root/'source'/name,sha)
    grammar.validate_epsilon(config['epsilon'])
    environment={'PATH':os.environ.get('PATH','/usr/bin:/bin'),
                 'PYTHONPATH':str(root/'source/src'),'PYTHONDONTWRITEBYTECODE':'1'}
    process=subprocess.run([sys.executable,'-m','circuitopt.cli','frontier',args.frontier_command,str(root)],
                           cwd=root/'source',env=environment)
    if process.returncode:raise ValueError('The preserved controller stopped; inspect the saved campaign receipts')
    return True


def register(sub):
    parser=sub.add_parser('frontier',help='Search complete point addition with editable native blocks')
    commands=parser.add_subparsers(dest='frontier_command',required=True)
    export=commands.add_parser('grammar')
    export.add_argument('--output',type=Path)
    run=commands.add_parser('run')
    run.add_argument('directory',type=Path)
    run.add_argument('--attempts',type=int,default=384)
    run.add_argument('--seed',type=int,default=241)
    run.add_argument('--maximum-depth',type=int,default=64)
    run.add_argument('--finalist-limit',type=int,default=12)
    run.add_argument('--audit-pairs',type=int,default=65536)
    run.add_argument('--rust-tools',type=Path)
    run.add_argument('--epsilon',type=float,default=.001)
    for command in ('report','replay','qualify','resume'):
        item=commands.add_parser(command)
        item.add_argument('directory',type=Path)


def run(args):
    if _frozen_controller(args):return
    if args.frontier_command=='grammar':
        text=json.dumps(grammar.definition(),indent=2)+'\n'
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(text)
        else:print(text,end='')
    elif args.frontier_command=='run':
        campaign.initialise(args.directory,**{key:getattr(args,key) for key in
            ('attempts','seed','maximum_depth','finalist_limit','audit_pairs','rust_tools','epsilon')})
        campaign.run(args.directory)
        print(campaign.report(args.directory))
    elif args.frontier_command=='qualify':
        campaign.qualify(args.directory)
        print(campaign.report(args.directory))
    elif args.frontier_command=='resume':
        campaign.run(args.directory)
        print(campaign.report(args.directory))
    elif args.frontier_command=='replay':
        print(json.dumps(campaign.replay(args.directory),indent=2))
    else:print(campaign.report(args.directory))
