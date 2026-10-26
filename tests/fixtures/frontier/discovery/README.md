# Carry-lane source fixture

These fixtures retain the two changed point-addition files from these public commits:

- [b618b19c](https://github.com/Layr-Labs/ecdsafail-challenge/commit/b618b19cf76f0ffe3829b53a8af6653c95dc7e4e), public row `d2583a60-f676-44b6-9e25-399dbdd273d7`, Q818.
- [7e8082d3](https://github.com/Layr-Labs/ecdsafail-challenge/commit/7e8082d3e4b7e1855643b0b379ac99640ac1ecc5), public row `e2194147-ebea-4e1c-b185-244173a5e6b5`, Q828.

Each manifest records the full commit and the SHA-256 hash of each source file. The files preserve their original bytes, including line endings.
The row records come from `paper/data/public-submissions.json.gz`.

The pair changes `GPOOL` from 0 to 10, the S5 carry-lane guard, `NONCE`, and the conditional-AND compiler policy.
The tests require discovery to retain these confounders. They do not infer an isolated Toffoli benefit from `GPOOL`.
Proposed jobs change `GPOOL` and its guard. They preserve the base nonce and compiler policy.

These retained files cover the changed-file scope. Each full public point-addition tree contains 208 files.
The discovery API reads the full tree from immutable Git objects. A separate local check ran that API on both full commits.
The fixture tests make no full-circuit equivalence claim.
