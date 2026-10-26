"""Reliability arithmetic for one honest public check and for the campaign audit.

Terms:
- A shot is one measured run of the circuit on one input. The public check runs 9,024 shots.
- p is the per-shot failure rate: failing shots divided by shots.
- lambda is the expected number of failing shots in one public check: lambda = 9024 * p.
- The one-check pass chance is exp(-lambda). This is the Poisson chance of zero failing shots.
- An audit pair is one input with two measurement streams. The audit bound uses failed inputs, not shots.
- The public check reports classical mismatches per shot and phase garbage per batch of 64 shots.
  One failing shot can add to both counts, so compare those counts with per-channel rates, not with lambda.

The bound function copies `failure_bounds` in
runs/frontier-levers-20261025/code/audit_frontier_reliability.py exactly.
The other functions are the arithmetic of runs/frontier-levers-20261025/PLAN.md and of
the evidence kit file RESULTS-UPDATE-20261025.md, section 2.
"""
import argparse
import json
import math

from scipy.stats import beta, binom

PUBLIC_SHOTS = 9024
EPSILON = 0.001


def per_shot_rate(failed_shots, shots):
    """Return p, the observed failing shots per shot."""
    if shots < 1 or not 0 <= failed_shots <= shots:
        raise ValueError('Failed shots must lie between zero and a positive shot count')
    return failed_shots / shots


def expected_failing_shots(p, shots=PUBLIC_SHOTS):
    """Return lambda = shots * p for one public check."""
    if not 0 <= p <= 1:
        raise ValueError('A per-shot rate must lie between 0 and 1')
    return shots * p


def pass_chance(p, shots=PUBLIC_SHOTS):
    """Return exp(-lambda), the chance that one check of `shots` shots has no failing shot."""
    return math.exp(-expected_failing_shots(p, shots))


def at_least_one_passes(chance, variants):
    """Return 1 - (1 - P)^k: the chance that at least one of k independent variants passes."""
    if not 0 <= chance <= 1 or variants < 0:
        raise ValueError('A chance lies between 0 and 1 and the variant count is nonnegative')
    return 1 - (1 - chance) ** variants


def at_least_one_of(chances):
    """Return 1 - prod(1 - P_i) for variants with different pass chances."""
    remaining = 1.0
    for chance in chances:
        if not 0 <= chance <= 1:
            raise ValueError('A chance lies between 0 and 1')
        remaining *= 1 - chance
    return 1 - remaining


def expected_passes(rates, shots=PUBLIC_SHOTS):
    """Return the expected number of passing checks for circuits with per-shot rates `rates`."""
    return sum(pass_chance(p, shots) for p in rates)


def expected_failing_shots_total(rates, shots=PUBLIC_SHOTS):
    """Return the expected number of failing shots summed over the listed checks."""
    return sum(expected_failing_shots(p, shots) for p in rates)


def failure_bounds(failures, pairs, *, family_size):
    """One-sided Clopper-Pearson family bound, as in the campaign controller (byte-equivalent logic)."""
    if not 0 <= failures <= pairs or pairs < 1 or family_size < 1:
        raise ValueError('Invalid failure sample')
    alpha = 0.05 / family_size
    upper = (1.0 if failures == pairs else
             -math.expm1(math.log(alpha) / pairs) if failures == 0 else
             float(beta.isf(alpha, failures + 1, pairs - failures)))
    lower = 0.0 if failures == 0 else float(beta.ppf(0.025, failures, pairs - failures + 1))
    point_upper = 1.0 if failures == pairs else float(beta.isf(0.025, failures + 1, pairs - failures))
    return {'observed_rate': failures / pairs, 'pointwise_two_sided95': [lower, point_upper],
            'family_upper95': upper, 'alpha_per_member': alpha, 'epsilon': EPSILON,
            'decision': 'meets_0.001_bound' if upper <= EPSILON else 'does_not_meet_0.001_bound'}


def largest_passing_count(pairs, *, family_size, epsilon=EPSILON):
    """Return the largest failed-input count whose family bound meets epsilon, or -1 if none does."""
    def meets(k):
        return failure_bounds(k, pairs, family_size=family_size)['family_upper95'] <= epsilon
    if not meets(0):
        return -1
    low, high = 0, pairs  # The bound grows with k, so a bisection finds the last passing count.
    while low < high:
        middle = (low + high + 1) // 2
        if meets(middle):
            low = middle
        else:
            high = middle - 1
    return low


def audit_power(pairs, rate, *, family_size, epsilon=EPSILON):
    """Return the chance that an audit of `pairs` inputs meets epsilon when the true per-input rate is `rate`."""
    if not 0 <= rate <= 1:
        raise ValueError('A per-input rate must lie between 0 and 1')
    limit = largest_passing_count(pairs, family_size=family_size, epsilon=epsilon)
    power = 0.0 if limit < 0 else float(binom.cdf(limit, pairs, rate))
    return {'pairs': pairs, 'per_input_rate': rate, 'family_size': family_size, 'epsilon': epsilon,
            'largest_passing_failed_inputs': limit, 'power': power}


def summary(rates, shots=PUBLIC_SHOTS, variants=None):
    """Tabulate lambda and pass chance for each rate, with totals."""
    rows = [{'p': p, 'lambda': expected_failing_shots(p, shots), 'pass_chance': pass_chance(p, shots)} for p in rates]
    result = {'shots_per_check': shots, 'checks': rows,
              'expected_passes': expected_passes(rates, shots),
              'expected_failing_shots': expected_failing_shots_total(rates, shots),
              'chance_at_least_one_passes': at_least_one_of(r['pass_chance'] for r in rows)}
    if variants is not None:
        result['variants'] = variants
        result['chance_at_least_one_of_k_passes'] = [at_least_one_passes(r['pass_chance'], variants) for r in rows]
    return result


def counts_rate(text):
    """Parse FAILED/SHOTS into a per-shot rate."""
    failed, _, shots = text.partition('/')
    if not shots:
        raise ValueError(f'Write counts as FAILED/SHOTS: {text}')
    return per_shot_rate(int(failed), int(shots))


def add_arguments(parser):
    parser.add_argument('--p', type=float, action='append', default=[], metavar='RATE',
                        help='Per-shot failure rate of one circuit; repeat for several circuits')
    parser.add_argument('--counts', action='append', default=[], metavar='FAILED/SHOTS',
                        help='Failing shots and shots from an audit, for example 4/40960')
    parser.add_argument('--shots', type=int, default=PUBLIC_SHOTS, help='Shots in one public check')
    parser.add_argument('--variants', type=int, help='Report the chance that at least one of k variants passes')
    parser.add_argument('--bound', nargs=2, type=int, metavar=('FAILED_INPUTS', 'PAIRS'),
                        help='Report the one-sided Clopper-Pearson family bound')
    parser.add_argument('--power', nargs=2, type=float, metavar=('PAIRS', 'PER_INPUT_RATE'),
                        help='Report the chance that the audit meets epsilon for a true per-input rate')
    parser.add_argument('--family-size', type=int, default=32)
    parser.add_argument('--epsilon', type=float, default=EPSILON)


def run(args):
    rates = list(args.p) + [counts_rate(item) for item in args.counts]
    result = {}
    if rates:
        result['pass_chance'] = summary(rates, args.shots, args.variants)
    if args.bound:
        result['bound'] = failure_bounds(args.bound[0], args.bound[1], family_size=args.family_size)
    if args.power:
        result['audit_power'] = audit_power(int(args.power[0]), args.power[1], family_size=args.family_size,
                                            epsilon=args.epsilon)
    if not result:
        raise ValueError('Give --p, --counts, --bound or --power')
    print(json.dumps(result, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_arguments(parser)
    try:
        run(parser.parse_args(argv))
    except ValueError as exc:
        parser.exit(2, f'ValueError: {exc}\n')


if __name__ == '__main__':
    main()
