# Native reference sources

The original Kaliski constructor and configuration come from the user's existing circuit-optimisation repository.
The constructor includes its tested 16-bit field profile and canonical reduction repairs.
The fixed circuit types, simulator, and curve oracle come from the retained `quantum_ecc` harness.
`original-NOTICE` preserves the upstream authors, sources, and license links for those harness files.

The operation parser and scalar protocol are the retained `study-check` implementation.
The width-aware parser admits exactly four correctly typed 16-bit or 256-bit registers.
The new package changes its Cargo dependency path to this vendored harness.
The source implementation remains unchanged.

The native library derives its input, artifact, and binomial-bound helpers from the tested scaled environment.
This package adds policy admission, clean child processes, compiler activation receipts, and command-line tools.
No blanket license is assigned to third-party files outside their retained license grants.
