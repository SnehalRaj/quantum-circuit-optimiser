"""Commands for exact Pareto moves: `circuit-opt pareto-moves <step>`."""
import argparse
import json
from pathlib import Path
import sys

from . import audit_slot, explore, levers, reference, screen, submit
from .layout import Campaign, load, require, sha
from .. import lever_survey, pass_chance

DEFAULT_ROOT = Path('runs/frontier-levers-20261025')


def _paths():
    common = argparse.ArgumentParser(add_help=False)
    group = common.add_argument_group('campaign paths (defaults reproduce runs/frontier-levers-20261025/code)')
    group.add_argument('--campaign-root', type=Path, default=DEFAULT_ROOT,
                       help='Campaign folder; default runs/frontier-levers-20261025 under the current folder')
    group.add_argument('--repo', type=Path, help='Repository root; default two levels above the campaign')
    group.add_argument('--scratch', type=Path, help='Scratch base; default $PARETO_MOVES_SCRATCH or the temp folder')
    group.add_argument('--build-scratch', type=Path, help='Candidate build folder; default $LEVERS_SCRATCH or SCRATCH/levers-build')
    group.add_argument('--screen-dir', type=Path, help='Evaluator harness folder; default SCRATCH/screen-harness')
    group.add_argument('--toolchain', type=Path, help='Folder with rustup/ and cargo/; default REPO/../ecdsa-fail/.tools')
    group.add_argument('--rust', help='Rust toolchain name; default 1.93.0-aarch64-apple-darwin')
    group.add_argument('--harness', type=Path, help='Fixed harness archive; default the 6b0327d6 snapshot archive')
    group.add_argument('--checker', type=Path, help='Development checker; default CAMPAIGN/tools/study-check.bin')
    group.add_argument('--large-checker', type=Path, help='Checker above 50M operations; default CAMPAIGN/tools/study-check-400m.bin')
    group.add_argument('--track', help='Track folder under tracks/; default froghop-levers')
    group.add_argument('--no-time-wrapper', action='store_true', help='Run build_circuit without /usr/bin/time -l')
    return common


def campaign_from(args):
    settings = {'rust': args.rust, 'track': args.track}
    if args.no_time_wrapper:
        settings['time_wrapper'] = ()
    return Campaign.at(args.campaign_root, repo=args.repo, harness=args.harness, toolchain=args.toolchain,
                       scratch=args.scratch, build_scratch=args.build_scratch, screen_dir=args.screen_dir,
                       checker=args.checker, large_checker=args.large_checker, **settings)


def register(sub):
    parser = sub.add_parser('pareto-moves', help='Exact left or down moves on the public Q/T curve, with screens and audits')
    commands = parser.add_subparsers(dest='pareto_command', required=True)
    common = _paths()

    p = commands.add_parser('prepare', parents=[common], help='Overlay a parent source on the harness and apply counted edits')
    p.add_argument('id')
    p.add_argument('--parent', required=True, help='Parent commit prefix of an archive in CAMPAIGN/sources')
    p.add_argument('--replace', nargs=3, action='append', default=[], metavar=('OLD', 'NEW', 'FILE'),
                   help='Replace OLD by NEW in FILE; OLD must occur exactly once')
    p.add_argument('--take', nargs=2, action='append', default=[], metavar=('FILE', 'DONOR_COMMIT'),
                   help='Copy FILE byte for byte from the donor archive')
    for name, text in (('build', 'Build build_circuit in a fresh target folder'),
                       ('emit', 'Emit ops.bin once with a minimal environment'),
                       ('develop', 'Run the development check on the fixed development label')):
        commands.add_parser(name, parents=[common], help=text).add_argument('id')
    p = commands.add_parser('compare', parents=[common], help='Paired development A/B of a child and its parent')
    p.add_argument('id')
    p.add_argument('parent_id')
    commands.add_parser('screen-setup', parents=[common], help='Build the trusted evaluator once from the harness')
    p = commands.add_parser('screen', parents=[common], help='Screen one variant once with the trusted evaluator')
    p.add_argument('id')
    p = commands.add_parser('audit-slot', parents=[common], help='Reserve an audit slot and freeze its protocol')
    p.add_argument('id')
    p.add_argument('--ops', type=Path, help='Operation file; default the location in emission.json')
    p.add_argument('--audit-checker', default='tools/study-check.bin', help='Checker copied into the slot')
    p.add_argument('--first-slot', type=int, default=1)
    p.add_argument('--last-slot', type=int, default=20)
    p.add_argument('--track-label', default='A')
    p.add_argument('--pairs', type=int, default=20480)
    p.add_argument('--family-size', type=int, default=32)
    p = commands.add_parser('audit-run', parents=[common], help="Run a frozen slot's controller copy")
    p.add_argument('slot', type=int)
    p.add_argument('--stage', choices=['timing', 'calibration'], default='calibration')
    p.add_argument('--python', type=Path, default=Path(sys.executable))
    p = commands.add_parser('reference', parents=[common], help='Derive and seal the public-reference folder')
    p.add_argument('id')
    p.add_argument('--source-dir', type=Path, help='Candidate tree; default BUILD_SCRATCH/ID/source')
    p.add_argument('--parent-commit', required=True)
    p.add_argument('--audit-slot', type=int, required=True)
    p.add_argument('--memory-gib', type=int, default=reference.DEFAULT_MEMORY_GIB)
    p.add_argument('--python', type=Path, help='Interpreter for the derived scripts; default REPO/.venv/bin/python')
    p = commands.add_parser('submit-prepare', parents=[common], help='Gate the candidate and write its note')
    p.add_argument('id')
    p.add_argument('--parent-id', required=True)
    p.add_argument('--lever-id', action='append', default=[])
    p.add_argument('--title', required=True)
    p.add_argument('--change', required=True)
    p.add_argument('--region', required=True, help='Candidate-id prefix of every variant screened for this region')
    p.add_argument('--credit', action='append', default=[])
    p.add_argument('--snapshot', type=Path, help='Public submissions snapshot; default the 18:31Z corners snapshot')
    p.add_argument('--model', default=submit.MODEL)
    p.add_argument('--harness-name', default=submit.HARNESS)
    p = commands.add_parser('submit-send', parents=[common], help='Send the prepared submission once')
    p.add_argument('id')
    p.add_argument('--command', default='ecdsafail', help='Submit command; default ecdsafail')
    p = commands.add_parser('explore', parents=[common], help='Measure a settings grid with the exploration binary')
    p.add_argument('grid', type=Path)
    p.add_argument('--binary', type=Path)
    p.add_argument('--build-receipt', type=Path)
    p.add_argument('--output', type=Path)
    p = commands.add_parser('pass-chance', help='One-check pass chance, family bound and audit power')
    pass_chance.add_arguments(p)
    p = commands.add_parser('survey-levers', help='Report pass levers that public sources leave off')
    lever_survey.add_arguments(p)


def _emitted_ops(campaign, cid):
    emission = load(campaign.candidate(cid) / 'emission.json')
    ops = Path(emission['ops_location'])
    require(sha(ops) == emission['ops_sha256'], 'The operation file differs from its emission receipt.')
    return ops


def run(args):
    action = args.pareto_command
    if action == 'pass-chance':
        return pass_chance.run(args)
    if action == 'survey-levers':
        return lever_survey.run(args)
    campaign = campaign_from(args)
    if action == 'prepare':
        result = levers.prepare(campaign, args.id, args.parent, args.replace, args.take)
    elif action in ('build', 'emit', 'develop'):
        result = getattr(levers, action)(campaign, args.id)
    elif action == 'compare':
        result = levers.compare(campaign, args.id, args.parent_id)
    elif action == 'screen-setup':
        result = screen.setup(campaign)
    elif action == 'screen':
        result = screen.run(campaign, args.id)
    elif action == 'audit-slot':
        ops = args.ops or _emitted_ops(campaign, args.id)
        result = audit_slot.reserve(campaign, args.id, ops, checker=args.audit_checker,
                                    slots=range(args.first_slot, args.last_slot + 1), track_label=args.track_label,
                                    pairs=args.pairs, family_size=args.family_size)
    elif action == 'audit-run':
        result = audit_slot.run_slot(campaign, args.slot, stage=args.stage, python=args.python)
    elif action == 'reference':
        result = reference.prepare(campaign, args.id, args.source_dir or campaign.work(args.id) / 'source',
                                   args.parent_commit, args.audit_slot, memory_gib=args.memory_gib, python=args.python)
    elif action == 'submit-prepare':
        result = submit.prepare(campaign, args.id, parent_id=args.parent_id, lever_ids=args.lever_id, title=args.title,
                                change=args.change, region=args.region, credits=args.credit, snapshot=args.snapshot,
                                model=args.model, harness=args.harness_name)
    elif action == 'submit-send':
        result = submit.send(campaign, args.id, command=args.command)
    elif action == 'explore':
        result = explore.run_grid(campaign, args.grid, binary=args.binary, build_receipt=args.build_receipt, out=args.output)
    else:
        raise ValueError(f'Unknown pareto-moves step: {action}')
    print(json.dumps(result, indent=2))
    return result
