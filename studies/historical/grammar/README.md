# Grammar component study

This retrospective experiment separates two executable choices and then tests the finite guard.
The study uses the original three modular-division starts over the prime 65,521.
It preserves their original weights: 1, 32.72575137951653, and 100.

| Arm | Payload shape | Constant implementation | Finite guard |
|---|---|---|---|
| G0 | Fixed three-operation payload | Legacy | Off |
| G1 | Two equivalent payload shapes | Legacy | Off |
| G2 | Fixed three-operation payload | Editable legacy/direct | Off |
| G3 | Two equivalent payload shapes | Editable legacy/direct | Off |
| G4 | Two equivalent payload shapes | Editable legacy/direct | On |

The three-operation payload uses swap, controlled subtraction, and modular half.
The second shape uses swap, half of the first register, half of the second register, controlled subtraction, and double of the first register.
Both shapes implement the same field map for odd modulus:

$$
(U,V)\longmapsto\left(U,\frac{V-bU}{2}\right)
$$

The controlled swap precedes the displayed map.
The extended shape computes the second output as half of V minus b times half of U.
Its final operation restores U.
The source arithmetic kernels retain their own phase and scratch obligations.

This study covers those two proved shapes and their constant implementations.
It does not test the full grammar of arbitrary programs with one to eight instructions.

## Frozen protocol

The seeds are 401, 809, and 1601.
Each arm receives 60 proposal slots per baseline and seed.
The full matrix contains 2,700 proposal slots.
The evaluator uses 512 quick inputs and 4,096 long-development inputs, with two measurement streams per input.
All arms share the recorded evolutionary parent policy and four bridge proposals.
Duplicate and rejected proposals consume their slot.

Identical recipes reuse the exact same development evaluation across arms.
This avoids duplicate simulation work.
The comparison concerns circuit quality at equal proposal budgets.
Its wall times cannot establish a controller-speed comparison.

All finalists freeze before the fresh audit label is drawn.
Each distinct baseline or finalist receives 16,384 fresh input pairs.
The family allocation is 0.05 divided by the number of distinct tested recipes.
The failure limit is 0.001.
An endpoint that fails qualification receives its baseline cost in the weighted result.

## Records

- `contract.json` and `contract.sha256`: definitions frozen before any circuit evaluation.
- `source/`: copied evaluator and grammar sources, with their original notices.
- `searches/`: complete proposal records and selected endpoints.
- `evaluation-cache.json`: shared development receipts.
- `finalists.json`: frozen baseline and finalist identities.
- `audit-label.json`: fresh label consumed before the audit begins.
- `audit-receipts.json`: all audit outcomes.
- `../../data/grammar-ablation.json`: manuscript-facing results.

The experiment uses no model calls.
It was designed after the earlier frontier results.
Its findings support a retrospective mechanism test, rather than a claim about the original discovery sequence.

## Completed result

| Arm | Median remaining cost | Median weighted improvement | Qualified endpoints |
|---|---:|---:|---:|
| G0 | 0.509877 | 49.012306% | 9/9 |
| G1 | 0.509871 | 49.012864% | 9/9 |
| G2 | 0.448503 | 55.149690% | 9/9 |
| G3 | 0.497568 | 50.243211% | 9/9 |
| G4 | 0.495683 | 50.431677% | 9/9 |

G2 has the best observed median in this fixed-budget study.
The focused grammar finds direct constant implementations more consistently than the larger program set.
The study does not establish an optimal grammar or a general search advantage.
The production pipeline's shared six-action path is a separate policy from this study's four shared bridges.

The family contains 14 distinct baseline or finalist recipes.
Twelve audit recipes have zero failed inputs; two have two failed inputs.
Their adjusted upper bounds are 0.000343861 and 0.000591215, respectively.
All 45 selected endpoints qualify.
The complete study takes 628.68 seconds, with cached evaluations shared across arms.

G4 rejects 72 proposals before circuit evaluation because they violate its sufficient guard.
A guard rejection does not supply a counterexample for every rejected recipe.
G4 has no observed development failures; G3 has 65.
The adaptive searches diverge, so those counts are descriptive rather than matched rejection outcomes.

`verify_ablation.py` independently checks all 2,700 proposal slots, 45 endpoint records, and 14 statistical bounds.
It inverts the binomial CDF for the bounds and checks 54 frozen source files.
The verification result is in `verification.json`.
