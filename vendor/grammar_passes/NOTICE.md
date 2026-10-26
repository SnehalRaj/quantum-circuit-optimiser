# Generic grammar passes

`adjacent_cancel.rs` is new code for this repository.
It uses the retained `crate::circuit::Op` interface.
It cancels identical adjacent unitary involutions and reports removed pairs.
The caller retains an IR-hash certificate for replay.

The circuit types and gate semantics retain the upstream credits in `vendor/native_reference/original-NOTICE`.
This pass makes no change to the guarded conditional compiler or historical defaults.
