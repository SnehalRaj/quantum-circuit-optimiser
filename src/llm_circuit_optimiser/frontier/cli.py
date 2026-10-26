"""Run frontier data tools and receipt-driven jobs without a model."""
import argparse
import json
from pathlib import Path


def positive(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError('Use a positive integer')
    return value


def add_parsers(sub):
    public = sub.add_parser('public', help='Sync and inspect the public frontier.').add_subparsers(dest='public_command', required=True)
    sync = public.add_parser('sync', help='Save original public rows and the eligible curve.')
    sync.add_argument('--out', type=Path, required=True)
    sync.add_argument('--owner', default='SnehalRaj')
    sync.add_argument('--executable', default='ecdsafail')
    curve = public.add_parser('curve', help='Compute the curve from saved public rows.')
    curve.add_argument('snapshot', type=Path)
    curve.add_argument('--owner', default='SnehalRaj')
    diff = public.add_parser('diff', help='Compare two saved curve snapshots.')
    diff.add_argument('previous', type=Path)
    diff.add_argument('current', type=Path)
    trees = public.add_parser('trees', help='Discover source trees and typed layout settings.')
    trees.add_argument('snapshot', type=Path)
    trees.add_argument('--git-repo', type=Path, required=True)
    trees.add_argument('--out', type=Path, required=True)
    trees.add_argument('--owner', default='unassigned', help='Worker name for the discovered jobs.')
    trees.add_argument('--limit', type=positive, default=64)

    frontier = sub.add_parser('frontier', help='Plan and run a frontier campaign.').add_subparsers(dest='frontier_command', required=True)
    jobs = frontier.add_parser('jobs', help='Compare historical handoff jobs with a curve; execution remains blocked.')
    jobs.add_argument('snapshot', type=Path)
    jobs.add_argument('--family', choices=['froghop', 'lilypad'], default='froghop')
    jobs.add_argument('--out', type=Path, required=True)
    check = frontier.add_parser('validate-jobs', help='Validate jobs and list blocked requests.')
    check.add_argument('jobs', type=Path)
    check.add_argument('--owner')
    run = frontier.add_parser('run', help='Resume jobs from their checked stage receipts.')
    run.add_argument('jobs', type=Path)
    run.add_argument('--config', type=Path, required=True)
    run.add_argument('--grammar', action='append', type=Path, help='Repeat for the source-bound rule files used by these jobs.')
    run.add_argument('--out', type=Path, required=True)
    run.add_argument('--owner', required=True)
    run.add_argument('--family-id', default='frontier-v1')
    run.add_argument('--registry', type=Path)
    run.add_argument('--parallel', type=positive, default=1)
    run.add_argument('--through', choices=['materialize', 'build', 'emit', 'static_gate', 'develop', 'screen', 'audit', 'export', 'send', 'record'], default='export')
    run.add_argument('--execute-submit', action='store_true', help='Allow public submission after all campaign checks.')
    status = frontier.add_parser('status', help='Read the recorded state of each job.')
    status.add_argument('campaign', type=Path)
    replay = frontier.add_parser('compare-replay', help='Compare saved receipts with Q818/Q819 reference records.')
    replay.add_argument('campaign', type=Path)
    replay.add_argument('--expected', type=Path, required=True)


def _read(path):
    return json.loads(Path(path).read_text())


def dispatch(args):
    from . import public
    if args.command == 'public':
        if args.public_command == 'sync':
            result = public.sync_public(args.out, executable=args.executable, owner=args.owner)
            return result['receipt']
        if args.public_command == 'curve':
            return public.build_snapshot(_read(args.snapshot), owner=args.owner)
        if args.public_command == 'diff':
            return public.diff_snapshots(_read(args.previous), _read(args.current))
        from .discovery import discover_trees
        return discover_trees(_read(args.snapshot), args.git_repo, out_dir=args.out,
                              owner=args.owner, limit=args.limit)
    from .jobs import load_jobs
    if args.frontier_command == 'jobs':
        from .generators import write_jobs
        snapshot = _read(args.snapshot)
        if 'submissions' in snapshot:
            snapshot = public.build_snapshot(snapshot)
        return write_jobs(args.out, snapshot, family=args.family, source=str(args.snapshot))
    if args.frontier_command == 'validate-jobs':
        jobs = load_jobs(args.jobs, owner=args.owner)
        return {'jobs': len(jobs), 'blocked': [{'id': j['id'], 'reasons': j.get('blocked_reasons', [])}
                                             for j in jobs if j.get('blocked_reasons')]}
    if args.frontier_command == 'status':
        return [_read(path) for path in sorted((args.campaign/'jobs').glob('*/status.json'))]
    if args.frontier_command == 'compare-replay':
        from .replay import compare_campaign
        return compare_campaign(args.campaign, args.expected)
    from .campaign import Campaign
    from .runtime import RuntimeAdapter
    config = _read(args.config)
    if args.grammar:
        config['grammar_files'] = [str(path.resolve()) for path in args.grammar]
    if args.through in ('send', 'record') and not args.execute_submit:
        raise ValueError('Use --execute-submit to run the public submission stage')
    jobs = load_jobs(args.jobs, owner=args.owner)
    if not jobs:
        raise ValueError('No jobs match this owner; set the owner when creating the jobs')
    campaign = Campaign(args.out, RuntimeAdapter(config), owner=args.owner,
                        family_id=args.family_id, registry_path=args.registry)
    return campaign.run_jobs(jobs, through=args.through,
                             allow_send=args.execute_submit, parallel=args.parallel)


def exit_status(args, result):
    if args.command == 'frontier' and args.frontier_command == 'compare-replay':
        return 0 if result['status'] == 'matched' else 2
    if args.command == 'frontier' and args.frontier_command == 'run':
        return 0 if result and all(row.get('state') in ('ready', 'complete') for row in result) else 2
    if args.command == 'frontier' and args.frontier_command == 'validate-jobs':
        return 0 if result['jobs'] and not result['blocked'] else 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='lco')
    add_parsers(parser.add_subparsers(dest='command', required=True))
    args = parser.parse_args(argv)
    try:
        result = dispatch(args)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f'lco: {error}\n')
    print(json.dumps(result, indent=2, sort_keys=True))
    return exit_status(args, result)
