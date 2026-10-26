# Propose and prove compiler rules

The local-rule interface accepts gate programs from an LLM or another proposer.
It proves each rule before application. The rule then becomes a deterministic circuit transformation.
See the [showcase protocol](../studies/harness-showcase-20261026/README.md) for the controlled experiments.

## Rule format

A rule names existing wires in first-occurrence order.
Gate arguments put the target first, followed by controls.
The allowed gates are `NEG`, `X`, `Z`, `CX`, `CZ`, `SWAP`, `CCX`, and `CCZ`.
Each program has at most seven gates and uses at most six wires.

```json
[
  {
    "wires": 3,
    "before": [["CCX", 0, 1, 2], ["X", 1], ["CCX", 0, 1, 2]],
    "after": [["CX", 0, 2], ["X", 1]]
  }
]
```

This example removes two Toffoli gates through an exact Boolean identity.
It is a documented example, not a recorded model discovery.

## Prove and apply

`rewrite_rules.certify` compares the output state and sign for every input basis state.
This proves the complete local unitary because the admitted gates form signed permutations.
The native adapter repeats the proof with the fixed simulator.
It rejects a replacement unless it reduces the number of CCX/CCZ gates.
The adapter preserves the parent's other operations and uses no new wires.

```sh
./lco setup
work/native-target/release/rewrite-apply \
  --width 16 --ops /path/to/parent.qops.zst \
  --rules /path/to/rules.json --out work/rewritten.qops.zst
```

Use `--width 256` for a full-width artifact.
The same rule language applies at both widths.
If `LCO_TARGET_DIR` selects another build cache, use its `release/rewrite-apply` binary.

The receipt records parent, rule, and result hashes, rewrite count, and static Toffoli savings.
A static saving counts gate records. Conditional execution can change the measured saving.
The rule proof establishes equivalence to the parent.
Check the parent's point-addition behavior and the transformed implementation with the fixed checker before qualification.

The adapter stops a match at measurements, resets, classical fields, and register records.
It also preserves condition-stack boundaries.
The current interface is separate from the standard recipe-selection menu.
Call the native adapter when a workflow needs learned rules.

## Mine source patterns

```sh
work/native-target/release/rewrite-mine \
  --width 256 --ops /path/to/parent.ops.bin \
  --max-wires 4 --out work/patterns.json
```

The miner records frequencies and the source operation hash.
Frequency does not establish an opportunity to reduce cost.
Use `rewrite_rules.nonlinear_rank` to reject windows whose nonlinear output rank already reaches their Toffoli cost.
This bound covers the admitted unitary language without extra wires or measurements.
Record all remaining tasks before asking a model for proposals.
