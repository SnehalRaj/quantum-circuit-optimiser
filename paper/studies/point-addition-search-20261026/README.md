# Point-addition grammar and proposer study

This study separates the circuit choices a grammar can express from the speed at which a proposer finds them.
All searches start from one 16-bit Kaliski point-addition circuit after the same `mfs` preparation.
The raw empty-policy circuit records the cost of that shared preparation.

The grammar adds conditional-AND cleanup (`u`, `r`), cofactor register release, and combined coordinate products.
These are inherited constructions with executable bindings. The full grammar contains sixteen recipes.
An exhaustive comparison measures the best qualified circuit in each nested grammar.

Uniform sampling, evolution, Claude, and Codex each receive eight proposal slots for five seeds.
Each policy selects two recipes before either evaluation. Feedback arrives after the pair.
The model receives the rule descriptions and its measured archive. It receives no exhaustive results or historical winning recipe.
All arms use the same input pairs for a given seed. Checkpoints occur after 1, 2, 4, and 8 slots.

The study freezes all finalists before drawing a fresh audit label.
It audits all sixteen recipes and the raw parent on 16,384 input pairs with two measurement streams.
The fixed checker tests coordinates, phase, scratch, and preservation of the classical addend.
The confidence allowance is 0.05/20 and the failure limit is 0.001 per input.
Failed, invalid, or timed-out model batches consume their slots without replacement.

`protocol.json` freezes the design, sources, and instruments before the first evaluation.
`results.json` contains all seeds, checkpoints, paired differences, and audit results.
Raw process and CLI logs stay under the ignored `work/point-addition-search-20261026` directory.
Shared records retain hashes and selected measurement fields without local paths or credentials.

This finite comparison tests one constructor at sixteen bits. It does not establish full-width transfer or discovery of an arithmetic family.

## Measured results

The raw parent uses 214 qubits and 33,728 Toffolis.
Shared `mfs` preparation reduces its product by 5.808%, to 214 qubits and 31,769 Toffolis.
The following gains use this prepared parent as the denominator.

| Grammar | Recipes | Best Q | Best mean T | Best QT | Gain |
|---|---:|---:|---:|---:|---:|
| Cleanup options `u`, `r` | 4 | 214 | 28,051 | 6,002,914 | 11.703% |
| Add cofactor register release | 8 | 198 | 27,949 | 5,533,902 | 18.602% |
| Add combined coordinate products | 16 | 198 | 27,949 | 5,533,902 | 18.602% |

The extra coordinate choice does not lower the attainable minimum in this constructor.
All seventeen circuits pass 16,384 fresh inputs with two streams each.
Each adjusted upper failure bound is 0.000365624 after rounding upward.

| Proposer | Median gain after 1 slot | After 2 | After 4 | After 8 | Optimum by 4 / by 8 |
|---|---:|---:|---:|---:|---:|
| Uniform random | 6.271% | 11.514% | 18.427% | 18.427% | 0/5 / 0/5 |
| Evolution | 7.774% | 17.390% | 17.568% | 18.602% | 0/5 / 4/5 |
| Claude | 17.568% | 17.568% | 18.602% | 18.602% | 5/5 / 5/5 |
| Codex | 11.514% | 17.568% | 18.602% | 18.602% | 4/5 / 5/5 |

Both model workflows find the best recipe sooner in this finite comparison.
Their median final gain ties evolution after eight slots.
The study completes twenty Claude calls and twenty Codex calls without failures.
Claude reports `claude-opus-5-5`; Codex accepts `gpt-6.1-sol` without reporting its resolved model identifier.
The records retain all five seeds and paired differences.

Verify the shared evidence from a checkout, with Python and SciPy installed:

```sh
python studies/point-addition-search-20261026/audit_saved.py
```

This command uses retained circuit bytes, compressed audit outcomes, model prompts, and result records.
It needs no model login, native binary, or `work/` directory.
The full local verifier, `verify.py`, also recounts the development rows and checks the original process receipts.

This paper package includes the frozen source files that the protocol hashes.
Use the full library checkout for a new measurement; this package has no complete native build tree.
The [library](https://github.com/SnehalRaj/quantum-circuit-optimiser) retains the study driver and build instructions.
A new experiment needs new input labels and a separate protocol before execution.

The arithmetic constructors come from the vendored original Kaliski backend.
The repository preserves its original source notices.
