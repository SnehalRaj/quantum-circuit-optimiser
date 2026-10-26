# Verification records

The public snapshot includes the current harness, the manuscript, and frozen experiment records.
Historical evidence identifies the source version used for each measurement.

## Public snapshot checks

The Python suite passes 260 tests, with one skip when native integration fixtures are unavailable.
The Rust suite passes 30 tests with the pinned 1.93.0 toolchain.
The data verifier checks 421 pinned source files, 15 target recipes, and 22 cleaned files.
The paper build verifies 75 source records and recounts 557,056 saved stream verdicts.
The manuscript uses the 26 October 2026, 12:05 UTC public frontier snapshot.

## Retained experiments

- [Point-addition search](../studies/point-addition-search-20261026/README.md): 20 searches, 40 model calls, and 17 fresh audits.
- [Integration proof](../evidence/release-20261026/README.md): one pinned recipe, three proposal modes, and restart checks.
- [Reconstruction records](../evidence/reproduction/): operation hashes and completed checks for pinned recipes.
- [Fine-tuning records](../studies/historical/finetuning/README.md): partial candidate-generator experiments and the offline recount.

Use the commands in the root README to check a new checkout.
Record a new source identity whenever an implementation or protocol changes.
