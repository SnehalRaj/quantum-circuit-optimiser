# Matched full point addition at two widths

The native study uses Rust for construction, evaluation, and campaign control.
It shares the original affine/Kaliski backend between 16 and 256 bits.
The 16-bit curve uses p = 65419, group order 64921, and generator (3, 16991).
Both curves have a = 0 and b = 7; the large profile uses secp256k1.
The gate format, oracle, simulator, and failure checks remain shared.
Both profiles preserve the classical addend and check outputs, phase, and scratch.
Field constants and applicable multiplication kernels differ.

The four development recipes cross coordinate composition with early register release.
The small Q/T frontier selects register release; the baseline remains a control.
This selection freezes before the first large-field observation.
The selected option is inherited; this experiment measures its transfer.
The two small audits each use 100,000 fresh inputs and two measurement streams.
The separate transfer contract then fixes 16,384 fresh large inputs for each retained recipe.
Its family allowance covers four bounds with alpha = 0.05/4.
All four observe zero failures and meet epsilon = 0.001.
The independent recount covers 232,768 inputs and 465,536 stream verdicts.

| Width | Q control | Q early release | T unchanged | Inputs per circuit | Upper bound |
|---|---:|---:|---:|---:|---:|
| 16 | 214 | 198 | 33,728 | 100,000 | 0.0000438193 |
| 256 | 3,592 | 3,336 | 6,710,828 | 16,384 | 0.0002674219 |

These conservative controls differ from the public frontier candidates.
The separate frontier backend families remain unscaled.
The earlier model pilot continues to measure division; it supplies no full-point-addition model comparison.

Run the native pipeline inside the optimizer repository:

```sh
tools/scaled-point-add campaign --out runs/scaled-point-add --label scaled-first
```

This command needs an installed Rust toolchain and no Python or model calls.
The full workspace retains exact source, operations, binaries, input records, and fault-test receipts.
The source bundle keeps the summary receipts needed to rebuild the paper.
Its default paper build reruns no circuits.
Wall times reflect workstation contention and supply no matched performance claim.
