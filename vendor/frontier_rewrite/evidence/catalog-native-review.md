# Review: native bounded catalog implementation

Reviewed 24 October 2026. Scope: `native/frontier/catalog.rs`, `native/frontier/tests/catalog.rs`, and the CLI route. No source edits or native circuit evaluations occurred.

**No correctness defect found under the valid-operation input contract.** The implementation follows the approved signed-permutation construction. It preserves global phase, sorted wire lifting, and the shared outer classical mask.

The native unit tests use the same signature engine as optimization. They therefore leave an independent-oracle test gap. This review checks the gate engine separately against a matrix oracle.

## Reviewed snapshot

SHA-256 of `catalog.rs`: `808524af9d0c10d70eefa22109844507060ba8b8273dc8e16b995d0ce8fe6664`.

SHA-256 of `tests/catalog.rs`: `a513cbd941d9e493ecad9539a37bbf5d071b0601e53ed4dcd022a21d0271b31d`.

The CLI parses and validates operations before calling the pass. Direct callers of `optimize` must satisfy that same valid-input contract.

## Contract checks

| Item | Finding |
|---|---|
| Signature composition | Correct. Lines 21–33 decode the current image, retain its sign, apply the next gate to that image, and XOR its phase. |
| Global phase | Correct. `Neg` flips every active column's sign. The lookup retains that sign. |
| Padding | Correct. Identity initializes the full array to zero. Updates touch only the active columns. |
| Alphabet | Complete for the listed gates. `CX` includes both directions. `CZ`, `Swap`, and `CCZ` use their operand symmetries. `CCX` includes every target and unordered control pair. |
| Sequence enumeration | Correct. The root inserts the empty identity. Every node extends through length three. The explicit cost bound permits at most one Toffoli. |
| Minimum cost | Correct. Lines 36–37 compare `(Toffoli count, length)` strictly and retain the existing exact tie. |
| Determinism | Correct. Alphabet loops and depth-first traversal have fixed orders. Hash-map iteration never selects an entry. |
| Key equality | Correct. Each key contains the complete 16-byte array. Ordinary hash collisions still require full key equality. |
| Separation by size | Correct. Three- and four-wire catalogs occupy separate tables. |
| Sorted physical roles | Correct. Line 43 deduplicates every quantum field and sorts the physical IDs. `local` and `lifted` use inverse mappings of that same list. |
| Emitted fields | Correct. `lifted` starts from `Op::empty`. It sets only the gate's valid quantum fields. Classical and register fields retain their sentinels. |
| Barriers | Correct. `allowed` accepts only the listed unitaries without an explicit condition. Every other operation blocks a window. |
| Cost and termination | Correct. A replacement requires a strict static Toffoli decrease. Each emitted sequence has at most one Toffoli. |
| Ancillas and width | Correct. Every emitted quantum ID comes from the original window. No new wire or register entry appears. |

The implementation omits the unused two-wire catalog. A valid source window with at least two Toffolis necessarily contains at least three distinct wires. The omission loses no eligible replacement.

## Basis convention

The native engine uses **local role zero as the least-significant basis bit**. Its signature index therefore orders tensor factors from highest local role to lowest local role.

The earlier proof reference used role zero leftmost. Both native source signatures and catalog signatures use the native convention consistently. Reversing local tensor order conjugates both operators by the same basis permutation. Exact equality and the physical role lifting remain valid.

An independent oracle must use the native convention or apply that basis permutation. Comparing its bytes directly with the earlier reference's bytes would otherwise report a false mismatch.

## Outer condition and instrument

Each selected window contains no stack operation or classical write. Every original and replacement gate has no explicit classical condition. The current saved stack mask therefore remains constant across both complete blocks.

When the mask is false, both blocks skip completely. When it is true, their full signatures certify equal matrices. This also handles a conditional `Neg` correctly. It proves equality for arbitrary complex inputs and correlations with unselected systems.

No measurement or reset changes. Under the same saved inputs and reset words, all subsequent classical values, coordinates, phase words, and cleanup results agree. Existing failed input IDs and failure components remain unchanged.

The executed Toffoli reduction equals each static saving times its active shot mask. The logged `native_toffolis_saved` remains a static count. A changed Fiat–Shamir packet cannot replace the paired check.

## Independent numerical check

I translated the inspected `step` function and alphabet literally into a Python reference. I compared that reference with a separate complex-matrix construction using tensor Pauli operators and control projectors.

The matrix oracle constructs controlled `X` from the control projector, and controlled phases from projector products. It constructs `Swap` from three CNOT matrices. It uses the native least-significant-role convention.

All 72 single-gate cases across three and four wires matched exactly. All 2,930 ordered two-gate compositions also matched exactly. Every generator passed unitarity. Padding remained zero. The maximum matrix error was zero.

These checks exercise accumulated signs, post-permutation phase evaluation, every operand role, and both CNOT directions. They supplement the earlier exhaustive grammar verification recorded in [the contract review](catalog-grammar.md).

This numerical check verifies the inspected algorithm through a separate Python port. It does not execute the Rust function. The parent reports passing native unit tests; this review did not rerun them.

## Remaining test and reporting gaps

`tests/catalog.rs` checks the three nonlinear conjugation fixtures and one reset barrier. Its `catalog::equivalent` helper uses the same `signature` and `step` functions as the optimizer. A shared signature error could pass both sides of that assertion.

Add an independent native basis-and-phase oracle. Include `Neg`, `XZ` versus `ZX`, noncontiguous IDs, all operand orientations, explicit conditions, and all barrier kinds. Include true, false, and mixed-shot outer masks. Call `Op::validate` on emitted operations.

The optimizer rewrites output suffixes as it consumes the input. It does not promise a fixed point over every new overlapping interior window. This affects search coverage, rather than correctness. If an original input produces no rewrite, every eligible original window of the stated sizes has been checked.

The log records the window lengths, physical wires, signature, and static gain. It omits the full old and emitted gate sequences. Preserve the input, output, reviewed source hash, and deterministic replay to reconstruct those witnesses.

The declared Q=1,144 positive control belongs in its separate control result. Its outcome must not count toward the fastest-three discovery hypothesis. This review adds no resource measurement and changes no frozen experiment.
