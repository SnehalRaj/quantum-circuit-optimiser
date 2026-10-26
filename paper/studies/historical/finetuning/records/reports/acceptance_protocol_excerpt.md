# Historical acceptance rule

An edit is ACCEPTABLE if it lowers the score (paired, seeded evaluator, beyond 2 SE and by at least 50k) AND its
per-input failure rate is around 0.001 or below. Failure rate = classical mismatches / 9024 per seed, averaged over
seeds (report SE; ~0.01 percentage points at 8 seeds). Confirmed by Snehal (2026-10-24): the operational cutoff is classical failure rate <= 0.0015 and phase-garbage /
ancilla-garbage batches do NOT count toward acceptance (still reported for information). Nonce grinding is NOT
required under this rule. The earlier strict "dFail <= 0 versus parent" rule is superseded.
