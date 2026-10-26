"""Command-line editing, candidate generation, and contribution reports."""
import json
from pathlib import Path
from . import rules


def register(sub):
    parser=sub.add_parser('rules',help='Edit the rule library and inspect contribution evidence')
    commands=parser.add_subparsers(dest='rule_command',required=True)
    for name in ['init','list','scores','report','validate']:
        p=commands.add_parser(name);p.add_argument('library',type=Path)
        if name=='list':p.add_argument('--json',action='store_true')
    p=commands.add_parser('show');p.add_argument('library',type=Path);p.add_argument('rule')
    p=commands.add_parser('edit');p.add_argument('library',type=Path);p.add_argument('rule')
    p.add_argument('--priority',type=int);p.add_argument('--enabled',choices=['yes','no']);p.add_argument('--note');p.add_argument('--patch',type=Path)
    p=commands.add_parser('add');p.add_argument('library',type=Path);p.add_argument('card',type=Path)
    p=commands.add_parser('record');p.add_argument('library',type=Path);p.add_argument('event',type=Path)
    p=commands.add_parser('apply');p.add_argument('library',type=Path);p.add_argument('rule');p.add_argument('parent',type=Path)
    p.add_argument('--parameters',default='{}');p.add_argument('--output',type=Path,required=True)
    p=commands.add_parser('verify');p.add_argument('candidate',type=Path)
    p=commands.add_parser('evaluate');p.add_argument('candidate',type=Path)
    p.add_argument('--pairs',type=int,default=1024);p.add_argument('--label',required=True)
    p=commands.add_parser('import-audit');p.add_argument('library',type=Path);p.add_argument('audit',type=Path)
    p.add_argument('--run',type=int,default=0);p.add_argument('--baseline',required=True);p.add_argument('--rule',required=True);p.add_argument('--parameters',default='{}')


def run(args):
    action=args.rule_command
    if action=='init':
        result=rules.initialise(args.library);rules.report(args.library)
        print(f'Created {len(result)} editable rule cards in {args.library}')
    elif action=='list':
        cards=rules.cards(args.library)
        if args.json:print(json.dumps(cards,indent=2))
        else:
            for c in cards:print(f"{c['id']}  {c['priority']:3d}  {c['status']:11s}  {c['name']}")
    elif action=='show':print(json.dumps(rules._get(args.library,args.rule),indent=2))
    elif action=='edit':
        patch=json.loads(args.patch.read_text()) if args.patch else {}
        if args.priority is not None:patch['priority']=args.priority
        if args.enabled is not None:patch['enabled']=args.enabled=='yes'
        if args.note is not None:patch['note']=args.note
        if not patch:raise ValueError('Provide an edit')
        value=rules.update(args.library,args.rule,patch);rules.report(args.library)
        print(f"Saved {args.rule} version {value['version']}")
    elif action=='add':
        value=rules.add(args.library,json.loads(args.card.read_text()));rules.report(args.library)
        print(f"Added {value['id']}")
    elif action=='record':
        value=rules.record(args.library,json.loads(args.event.read_text()));rules.report(args.library)
        print(json.dumps(value,indent=2))
    elif action in ['scores','report','validate']:
        value=rules.report(args.library)
        if action=='scores':print(json.dumps(value,indent=2))
        else:print(args.library/'RULES.md')
    elif action=='apply':
        parent=json.loads(args.parent.read_text());value=rules.apply(args.library,args.rule,parent,json.loads(args.parameters))
        args.output.mkdir(parents=True,exist_ok=False)
        rules._save(args.output/'parent.json',parent,True)
        for name in ['candidate','history','rule']:rules._save(args.output/f'{name}.json',value[name],True)
        print(args.output/'candidate.json')
    elif action=='verify':
        from .rule_ops import verify_regions
        value=verify_regions(json.loads((args.candidate/'parent.json').read_text()),json.loads((args.candidate/'candidate.json').read_text()))
        rules._save(args.candidate/'verification.json',value)
        print(json.dumps(value,indent=2))
    elif action=='evaluate':
        from .division import export_circuit,evaluate_saved
        from .engine import upper
        if args.pairs<1:raise ValueError('Input count must be positive')
        recipe=json.loads((args.candidate/'candidate.json').read_text())
        if recipe.get('kind')=='gate_region':raise ValueError('Use rules verify for a small gate region')
        export_circuit(recipe,args.candidate/'circuit')
        value=evaluate_saved(args.candidate/'circuit',args.pairs,args.label,args.candidate/'inputs.jsonl.gz')
        value['S']=value['Q']*__import__('math').floor(value['T']+.5)
        value['upper_bound']=upper(value['failed_inputs'],args.pairs,.05)
        value['recipe']=recipe
        value['label']=args.label
        rules._save(args.candidate/'receipt.json',value,True)
        print(json.dumps(value,indent=2))
    elif action=='import-audit':
        value=rules.import_audit(args.library,args.audit,args.run,args.baseline,args.rule,json.loads(args.parameters))
        rules.report(args.library);print(json.dumps(value,indent=2))
