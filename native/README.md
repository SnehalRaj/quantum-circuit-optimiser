# Native full-point stages

`small-emit` constructs complete 16-bit point addition with the retained Kaliski arithmetic.
It applies the same guarded conditional compiler used by the 14 retained Froghop targets.
`circuit-check` contains the fixed simulator and curve oracle.
It accepts 16-bit or 256-bit operation files and imports no candidate constructor.

Put Cargo on PATH or set `LCO_CARGO`.
`native/build.sh` builds with `--locked` and prints the binary paths as JSON.
Set `LCO_CARGO_HOME` for a separate Cargo cache.
Set `LCO_TARGET_DIR` for a separate build directory.
Set `LCO_OFFLINE=1` to require cached dependencies.
The default build directory is `work/native-target`.

The commands below use that default directory:

```sh
native/build.sh
work/native-target/release/small-emit --out work/example.qops.zst \
  --policy mfsur --parent-policy mfs
work/native-target/release/circuit-check --width 16 --ops work/example.qops.zst \
  --pairs 4096 --label example-development --out work/example-check.json \
  --streams 2 --alpha 0.05 --threads 1
```

The checker supports one thread per process.
It tests the target coordinates, phase, quantum scratch, and preserved classical addend.
The input failure event combines both measurement streams.
An exit status of 1 reports observed failures and still supplies a valid audit receipt.
Use the exact upper bound and the frozen family allowance for qualification.
An exit status of 2 reports invalid input or an instrument failure.

The small field is p=65419, with curve order 64921 and generator (3, 16991).
The curve has a=0 and b=7.
The constructor keeps its canonical arithmetic repairs and 32-step Kaliski walks.
It emits real point-addition arithmetic before any compiler rewrite.

`--free-s true|false` selects the inherited `KAL_FREE_S` register-release setting.
`--combined-y true|false` selects the inherited `POINT_ADD_AFFINE_COMBINED_Y` coordinate construction.
Both default to `false`, which preserves the fixed constructor's operation bytes.
The parser accepts only `true` or `false` and rejects duplicate options.
The receipt records both choices as `free_s` and `combined_y`.
These are explicit choices for the 16-bit Kaliski constructor.
They have no declared mapping to the optimized Froghop or Leapfrog constructors.
Each changed circuit requires its own evaluation.

```sh
work/native-target/release/small-emit --out work/free-s.qops.zst \
  --policy mfs --parent-policy mfs --free-s true --combined-y false
```

The policy accepts any unique subset of `m`, `f`, `s`, `u`, and `r`.
It records flags in `mfsur` order.
`none` selects the empty subset.
The default policy is `mfs`.
The fixed constructor's inherited context is declared with `--parent-policy mfs`.
Newly enabled mechanisms must have nonzero rewrite counts.
The caller checks disabled mechanisms against its pinned parent receipt.
Standalone inactive requests fail admission and retain an explanatory receipt.

The inverse-AND action `u` replaces coherent erasure with HMR and conditional CZ.
The unread-write action `r` moves a measurement before an unread write window and applies the phase repair.
Both actions activate on the actual 16-bit arithmetic.
The `s` action is inactive in every tested small policy.
Inherited `s` remains explicit and supplies no evidence for a new `s` transfer claim.
The compiler guards stay active.
The emitter rejects `SL_COND` environment controls and runs the compiler in a clean child process.

`--cancel-adjacent` takes no value and enables exact adjacent gate cancellation after the conditional compiler.
Its receipt includes `adjacent_cancel_pairs` and `adjacent_cancel_certificate`.
The certificate binds the input and output IR hashes to the exact local guard.
The generic module is `vendor/grammar_passes/adjacent_cancel.rs`.
Its API is `run(Vec<crate::circuit::Op>) -> (Vec<Op>, usize)`.
The count is the number of removed pairs.
The same pass can transform an existing artifact:

```sh
work/native-target/release/circuit-transform --width 16 --ops work/example.qops.zst \
  --out work/example-cancel.qops.zst --passes exact-cancel
```

The conditional compiler remains byte-exact at SHA256 `a56f374ad86c7dd3ca13cfd08291f5f9a4f40a54e122959c481656a54b9d987e`.
Its retained credits are in `vendor/conditional/NOTICE.md`.
The harness and constructor credits are in `vendor/native_reference/NOTICE.md`.

The native validation records live in `evidence/native-validation`.
All four historical policies passed 100,000 fresh inputs each under two measurement streams.
Their four-member family uses alpha=0.0125 per policy and epsilon=0.001.
Each observed zero failures; each upper bound is 0.00004381930625289137.
All 64 policy/cancellation compositions also passed 64 development inputs each.
The cancelled `mfsur` artifact passed a separate 4096-input development check.
The four-policy audit qualifies those small circuits.
Full-width qualification remains separate.
Generated artifacts and complete per-input records remain under `work/native-validation`.
