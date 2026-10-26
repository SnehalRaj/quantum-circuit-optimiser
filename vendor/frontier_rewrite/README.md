# Exact local catalog

These files match the catalog hashes in the Q818 campaign source at commit `4073f09cfa7fe733fdeb64841193282aaff293d9`.
The campaign belongs to Snehal Raj's reversible-circuit optimization work.
The copied upstream NOTICE retains the fixed circuit and simulator credits.

`mod.rs` exports `apply(Vec<quantum_ecc::circuit::Op>)`.
`catalog.rs` replaces bounded gate windows with the same complete signed permutation.
It acts on three or four existing wires and keeps value and phase.
Measurements, resets, explicit conditions, and non-unitary operations stop a matching window.

The certificate test is embedded in `catalog.rs` under `cfg(test)`.
It requires the fixed `quantum_ecc` simulator and `study_check` test dependency.
The separate tests cover role lifting and shared outer classical masks.
`certificate.json` identifies these tests and the archived verification log.

The source files are copied without edits. `manifest.json` records their hashes and original locations.
The library's native integration must run these tests before it reports fresh certificate verification.
