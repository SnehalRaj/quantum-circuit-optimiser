"""Check source identity, budgets, bounds, and paper summaries from saved records."""
import math
import statistics
from scipy.optimize import brentq
from scipy.stats import binom
import run_pilot as pilot


contract = pilot.verify_contract()
data = pilot.load(pilot.PAPER / 'data/model-pilot.json')
manifest = pilot.load(pilot.HERE / 'audit/frozen.json')
audits = pilot.load(pilot.HERE / 'audit/results.json')
checks = []


def require(name, value):
    if not value:
        raise AssertionError(name)
    checks.append(name)


require('Six executable cells', len(data['arms']) == 6)
require('Fixed family reserve', len(audits) <= 128)
require('Common audit packet', len({r['input_sha256'] for r in audits.values()}) == 1)
alpha = 0.05 / 128
max_bound_error = 0.0
for cid, receipt in audits.items():
    n, k = receipt['pairs'], receipt['failed_inputs']
    require('Audit input count ' + cid[:12], n == 16384)
    require('Audit source identity ' + cid[:12],
            receipt['operation_sha256'] == manifest['circuits'][cid]['operation_sha256'])
    upper = -math.expm1(math.log(alpha) / n) if k == 0 else brentq(
        lambda p: binom.cdf(k, n, p) - alpha, k / n, 1.0, xtol=1e-14)
    error = abs(upper - receipt['epsilon_upper'])
    max_bound_error = max(max_bound_error, error)
    require('Independent bound ' + cid[:12], error < 1e-9)
    require('Qualification decision ' + cid[:12], receipt['eligible'] == (upper <= 0.001))
    require('Integer cost ' + cid[:12], receipt['S'] == receipt['Q'] * math.floor(receipt['T'] + 0.5))
for cell in data['arms']:
    condition, arm = cell['condition'], cell['arm']
    label = condition + '/' + arm
    require('Complete ' + label, cell['status'] == 'complete')
    require('Proposal budget ' + label, cell['proposal_slots'] == 216)
    require('Endpoint count ' + label, cell['total_endpoints'] == 9)
    require('Feedback budget ' + label, cell['feedback_calls']['attempted'] == (0 if arm == 'offline' else 6))
    root = pilot.HERE / 'runs' / condition / arm
    c = pilot.load(root / 'study.json')
    require('Fixed grammar ' + label, c['grammar'] == 'blocks-v1')
    require('Fixed seeds ' + label, c['seeds'] == contract['seeds'])
    require('Fixed development weights ' + label, c['calibration']['16']['weights'] == contract['weights'])
    require('Fixed guards ' + label, c['proposal_policy'] == 'guarded-seeds-v1')
    for score in cell['seed_scores']:
        selections = [s for s in manifest['selections'] if s['condition'] == condition and
                      s['arm'] == arm and s['seed'] == score['seed']]
        require('Three starts ' + label + '/' + str(score['seed']), len(selections) == 3)
        weighted = 0.0
        for s in selections:
            before, after = audits[s['starting']], audits[s['selected']]
            retained = after['S'] if before['eligible'] and after['eligible'] else before['S']
            weighted += s['weight'] * retained / before['S']
        remaining = weighted / sum(s['weight'] for s in selections)
        require('Weighted statistic ' + label + '/' + str(score['seed']),
                abs(remaining - score['remaining']) < 1e-12)
    require('Median ' + label, abs(statistics.median(s['remaining'] for s in cell['seed_scores']) - cell['median_remaining']) < 1e-12)
require('Total proposal slots', sum(c['proposal_slots'] for c in data['arms']) == 1296)
require('Total feedback calls', sum(c['feedback_calls']['attempted'] for c in data['arms']) == 24)
require('Provider calls include preflights', len(data['all_provider_calls']) == 27)
pilot.save(pilot.HERE / 'verification.json', {
    'status': 'passed', 'checks': checks,
    'independent_bound_method': 'Closed form for zero failures; binomial CDF inversion otherwise',
    'maximum_bound_difference': max_bound_error,
    'scope': 'Saved experiment accounting and numerical summaries; no universal circuit proof',
    'verification_source_sha256': pilot.sha(__file__)})
print('Verified', len(checks), 'checks; maximum bound difference', max_bound_error)
