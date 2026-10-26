# Frontier reconstruction

The [paper snapshot](../paper/data/frontier-summary.json) records 28 of our points among 161 frontier coordinates on 26 October 2026.
This document describes the separate, fixed reconstruction set below.

The frozen 25 October 2026, 15:46 UTC snapshot contains 14 of our entries among 101 Pareto coordinates.
All 14 use inherited Froghop constructors.
Nine use the one-hop variant; five use the double-hop variant.
The [target catalog](../data/targets/catalog.json) records parent commits, source hashes, settings, and expected circuit hashes.

The reconstruction applies one counted source edit.
Six parents change `mfs` to `mfsur`.
Eight parents change `mfsr` to `mfsur`.
The arithmetic, schedule tables, and inherited nonces stay fixed.
The `u` action replaces an inverse AND with measurement and a conditional phase correction.
The `r` action absorbs unread writes before a reset or measured end.
The compiler checks Boolean values, classical conditions, swapped contributions, and phase corrections.
These actions explain our derivative changes.
The underlying Froghop algorithms retain their upstream attribution.

The base grammar contains 17 rules.
Three family addenda contribute 12 more rules.
Six implemented rules expose 64 combinations of guarded cleanup and exact adjacent cancellation.
The other 23 cards retain their conditions and missing bindings.
Composition joins rule definitions; each family constructor implements the arithmetic.
New arithmetic rules require matching Rust implementations at both stages.

The fast constructor computes full point addition over the prime 65,419.
It uses Kaliski arithmetic and the shared gate interface and phase checks.
The current transfer study tests the shared compiler across these constructors.
The four native policies each pass 100,000 fresh input pairs with zero observed failures.
At 214 qubits, T falls from 31,769 for `mfs` to 28,051 for `mfsur`.
The failure limit remains probability 0.001 under the declared two-stream protocol.

The full-width reconstruction of q839 matches its expected operation hash.
Its official local check passes 9,024 shots and reproduces 839 qubits and 8,564,543 Toffolis.
The [reconstruction record](../evidence/reproduction/source-regeneration.json) lists completed emissions for the remaining targets.
The library also includes a source-backed qualified Leapfrog control and an editable Rust family interface.

Model-free and LLM-assisted workflows share the constructor, grammar admission, and checker.
Known-target replay measures reconstruction.
It does not measure model discovery.
Original notices, declared dependencies, failed attempts, and historical audit outcomes remain in the repository.
