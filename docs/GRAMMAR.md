# Composable circuit grammar

The grammar records mechanisms, parameters, source premises, and executable stage bindings.
The base file defines shared gates, arithmetic, and cleanup contracts.
Family addenda record source-specific schedules and arithmetic rules.

## Files and scope

| File | Rules | Scope |
|---|---:|---|
| `grammars/base.json` | 17 | Gate identities, arithmetic blocks, storage, and measured cleanup |
| `grammars/families/froghop.json` | 4 | History schedule, limb geometry, replay, and inherited approximation |
| `grammars/families/qualified-leapfrog.json` | 4 | Signed updates, replay programs, walk budgets, and constant kernels |
| `grammars/families/skywalk-cof.json` | 4 | Retained products, rail geometry, walk groups, and cofactor frames |
| `grammars/frontier/catalog.json` | 1 | Exact local gate replacement in the frontier runner |

Each rule has one of three states:

- `executable`: a trusted binding exists for the rule's declared runner and scope.
- `specification`: the rule defines a contract or identity without a separate mutation binding.
- `research`: the source describes a mechanism whose required stage mappings remain incomplete.

The staged search has six independent actions.
Five guarded conditional actions form 32 policies, including the unchanged `none` policy.
An independent cancellation action gives 64 combinations.
The 14 Froghop parents supply 896 source-and-action combinations before operation deduplication.
This count describes the candidate space. It supplies no performance claim.

Adding a family file keeps its blocked mechanisms visible.
The composer gives a reason for each missing binding.
With the base and three family files, six rules have executable bindings and 23 remain blocked.
A JSON capability claim cannot activate an unknown implementation.

The optional catalog file adds one executable rewrite for the frontier runner.
The staged policy runner rejects that rewrite because it cannot apply the required source integration.
The catalog has an exhaustive local certificate for its supported gate windows.
Its tested 16-bit circuit has no matching windows, so that test supplies no transfer gain.

Rules also declare a `kind`: `rewrite` or `setting`.
A shared rewrite needs real fast and large bindings.
A family setting can use a large binding alone when `bindings.fast_reason` explains the missing fast equivalent.
The frontier runner then evaluates that setting at full width.
Its receipts do not claim evidence from the 16-bit stage.

## Compose files

The command accepts any number of grammar files:

```bash
lco grammar compose \
  grammars/base.json \
  grammars/families/froghop.json \
  grammars/families/qualified-leapfrog.json \
  --family froghop --out work/composed.json
```

The output is a portable compiled bundle.
Use that bundle directly in the evolutionary search:

```bash
lco search --family froghop --grammar work/composed.json \
  --config configs/offline.json --targets q839 \
  --trials 16 --out work/composed-search
```

Bundles can also combine with raw addenda or other bundles.
The loader checks the bundle hash, rule schema, and origin references before search.
Loading an unchanged bundle preserves its canonical hash and source map.
The loader recomputes family availability from the trusted bindings.
The saved `availability` field remains a derived report and supplies no execution permission.

The file order does not set mutation order.
The composer sorts definitions and records a canonical SHA-256 hash.
Identical definitions deduplicate, even when separate files contain them.
Conflicting rule definitions stop composition.

An explicit override selects the definition file for one conflicting rule:

```python
from llm_circuit_optimiser.grammar import compose

grammar = compose(
    ["grammars/base.json", "grammars/my-cleanup-revision.json"],
    overrides={"cleanup.inverse_and_measure": "grammars/my-cleanup-revision.json"},
)
print(grammar.sha256)
print(grammar.availability("froghop"))
```

The override file must occur among the input files.
Unused overrides fail validation.
Each rule keeps its document IDs and document hashes in the source map.

## Rule format

Each JSON document has a schema version, stable ID, revision, definitions, and rule list.
Each rule carries these fields:

| Field | Meaning |
|---|---|
| `id`, `revision` | Stable mechanism name and positive integer revision |
| `title`, `description` | The mechanism and its intended use |
| `status`, `families`, `kind` | Admission state, applicable families, and rewrite or setting scope |
| `parameters` | Typed defaults, admitted values, and numeric bounds |
| `guards` | The source premises that the implementation must check |
| `bindings.fast`, `bindings.large` | Named implementations at each stage |
| `bindings.fast_reason` | Required explanation for a setting with no fast binding |
| `mechanism.match`, `mechanism.replace` | The input pattern and replacement |
| `preserves` | Value, phase, scratch, and addend requirements |
| `cost` | The expected resource effect and its measurement scope |
| `evidence` | Source paths, symbols, and available source hashes |
| `requires` | Required backend capabilities |

Boolean parameters use strict Boolean types.
The validator rejects `1` as a substitute for `true`.
Unknown fields and parameters fail validation.
The exact cutoff remains failure probability `0.001`.
A candidate cannot change this cutoff through its action parameters.

## Executable compiler rules

All five actions use the byte-identical conditional compiler at both stages:

```text
vendor/conditional/cond_pass.rs
a56f374ad86c7dd3ca13cfd08291f5f9a4f40a54e122959c481656a54b9d987e
```

The source uses hash-consed Boolean values and captured classical contexts.
It treats the quantum ABI inputs as unknown values and clean scratch as zero.
It tracks affine forms, swaps, measurements, and resets.

| Flag | Rule ID | Mechanism |
|---|---|---|
| `m` | `base.known_control_reduce` | Delete a zero AND contribution or replace a proven AND with CX |
| `f` | `base.phase_only_and` | Replace a temporary AND used only by Z reads with operand phases |
| `s` | `base.classical_guard_sink` | Build an AND only under the classical bit that guards its uses |
| `u` | `cleanup.inverse_and_measure` | Replace an inverse CCX with X-basis measurement and conditional CZ |
| `r` | `cleanup.unread_write_measure` | Measure before unread X-type writes and convert the writes into outcome-controlled phases |

The source plans `m`, `u`, and `f` from one analysis.
It then runs `s` on the resulting stream and runs `r` last.
Candidate action order does not reorder these compiler stages.
Canonical policy strings use the order `mfsur` to preserve the recorded historical recipes.
The emitter converts `none` into an empty option string.

The shared guard checks include equal end contexts and unchanged contribution labels.
They reject a measured target used as a phase operand.
They also require phase-only corrections when the compiler moves an AND under a classical guard.
The public emitter keeps the guards active and excludes diagnostic fault and alignment controls.

### Measurement and phase

Let a temporary target contain the Boolean value `a AND b`.
Its X-basis measurement outcome `k` contributes this relative phase:

$$
(-1)^{kab}.
$$

A `CZ(a,b)` under `k` removes that phase.
The inverse-AND rule therefore requires the operand values at the erase point.
It also requires a fresh classical outcome and an unconditional inverse operation.

For unread writes, let the accumulated XOR update be `f`.
Moving the measurement before the writes requires the phase contribution

$$
(-1)^{k f}.
$$

The compiler emits a sign for an X write, Z for a CX write, and CZ for a CCX write.
It retains every existing classical condition.
It also preserves the original measurement result and final reset.

The `u` and `r` actions change the measurement sequence.
Their receipts record activation counts and operation hashes.
An inactive rewrite supplies no evidence that its mechanism transfers to the fast environment.
The `s` action has no observed sites on the current fast circuit.
An inherited inactive `s` remains source context. A new or removed `s` needs an active matched test.

### Independent adjacent cancellation

`base.involution_cancel` runs after the conditional compiler when `cancel_adjacent` is true.
It cancels adjacent equal full operation records for Neg, X, Z, CX, CZ, Swap, CCX, and CCZ.
Classical writes, measurements, resets, registers, and condition-stack records form barriers.
The same module runs at both stages:

```text
vendor/grammar_passes/adjacent_cancel.rs
5bd79f20646acd7b8dcf436fdd604d6a6546f32536e8aa2324d8100875ee328f
```

The native measurement removed 588 pairs from genuine 16-bit `mfsur` point addition.
Its Q214 and T28051 stayed unchanged. Gate removal and resource improvement are separate measurements.
Activation supplies no claim of full-circuit qualification.

## Base contracts

The base specification includes these elementary rules and arithmetic contracts:

- Cancel two adjacent equal involutions under the same unchanged context.
- Commute gates with disjoint quantum and classical supports.
- Move swaps through a fully relabelled gate window.
- Reconstruct and restore an affine operand with X and CX.
- Add a signed source modulo the declared prime while preserving the source.
- Halve modulo an odd prime with a reversible parity correction.
- Choose direct or sparse signed constant kernels with the same value map.
- Change product lowering while preserving the full product and modular reduction.
- Recompute an intermediate from live parents after clean release.
- Use a temporary AND with its required measured inverse and phase correction.
- Reuse scratch after the value and phase obligations have ended.
- Compose one to eight typed arithmetic instructions at an owned boundary.

The arithmetic program alphabet is `add`, `sub`, `half`, `double`, `half_u`, `double_u`, and `swap`.
Each instruction chooses `direct` or `power2` lowering where the backend implements that kernel.
The earlier full-width Rust harness implements this block language.
The staged search records its contract and leaves its independent small-stage mapping blocked.

The known gate identities justify the specification entries.
Adjacent involution cancellation has its own executable implementation.
The other specification entries have no separate mutation binding in this release.
Every new executable lowering still needs a registered implementation and a full circuit check.

## Candidate programs and evolution

Users and models submit the same candidate schema:

```json
{
  "target": "q799",
  "actions": [
    {"rule": "cleanup.unread_write_measure", "parameters": {}},
    {"rule": "cleanup.inverse_and_measure", "parameters": {}}
  ]
}
```

The validator derives policy `ur` from this action list.
It fills the fixed guard and phase parameters.
It adds the selected family, backend, and composed grammar hash.
An explicit `policy` must agree with the action list.
Duplicate actions fail validation.

Evolution can add or remove one action, change a target, or use the random proposal order.
An action for `base.involution_cancel` sets `cancel_adjacent` to true.
An explicit cancellation flag must agree with that action.
Successive edits can traverse several policy subsets.
The emitter applies the resulting policy once to the source circuit.
The fixed source implementation controls the compiler order.

Use the same grammar files in model-free and model-assisted runs:

```bash
lco search --family froghop \
  --grammar grammars/base.json \
  --grammar grammars/families/froghop.json \
  --config configs/offline.json \
  --targets q839 --trials 32 --out work/froghop-offline
```

Replace the configuration file with the selected model preset for model-assisted proposals.
Both modes retain the same constructors, grammar admission checks, and fixed evaluator.

## Frontier settings and catalog actions

The frontier runner admits typed settings and the optional local catalog.
It binds each action to pinned source files and expands the action into counted edits.
The staged search does not execute these actions.

The source census can generate per-family addenda from a saved public snapshot:

```bash
lco public trees work/public.json --git-repo work/public-source \
  --out work/census --owner cluster
```

The census writes typed rule files under `work/census/families/`.
Pass those files through repeated `--grammar` options in `lco frontier run`.
The runtime checks each rule hash and its source bindings before materialization.
Family settings retain their guards, source evidence, and full-width limits.
Combining files admits their supported actions; it does not make incompatible arithmetic families interchangeable.

The [frontier guide](../examples/frontier/README.md) gives the complete command sequence.
The [campaign guide](FRONTIER-CAMPAIGN.md) defines the screening, audit, and export receipts.

## Fast and large mapping

The fast environment computes full point addition over the named 16-bit curve.
Its arithmetic constructor uses Kaliski inversion.
The large Froghop targets use their inherited optimized secp256k1 constructors.
The shared conditional compiler consumes the same operation representation in each stage.

This setup tests transfer of the conditional compiler mechanism across arithmetic backends.
It does not implement a scaled Froghop schedule.
Froghop history and limb rules therefore remain visible with missing fast-stage binding reasons.

Qualified Leapfrog, Skywalk, and COF have separate source contracts.
A family name does not activate another family's arithmetic implementation.
In particular, retained products require a producer, a single consumer, and complete phase repair.
Rail edits also require fresh history geometry and invalidation of stale source-indexed tables.

## Create a family

A custom family can use a pinned Rust constructor under backend `source-conditional`.
Its manifest names a source directory and hashes every editable source file.
The fixed benchmark supplies the build files, simulator, and evaluator.
The generic conditional wrapper supplies the shared mutation bindings.

```json
{
  "schema_version": 1,
  "id": "my-family",
  "name": "My point-addition family",
  "backend": "source-conditional",
  "targets": ["my-point"],
  "source_dir": "source",
  "files_sha256": {
    "src/point_add/mod.rs": "REPLACE_WITH_SHA256",
    "NOTICE": "REPLACE_WITH_SHA256"
  },
  "provenance": {
    "description": "Describe the constructor and its changes.",
    "authors": ["Keep all upstream authors and add the derivative author."],
    "upstream_family": "froghop"
  }
}
```

The example shows the schema. Replace both hash placeholders with actual file hashes.
Include every required module and embedded table under `src/point_add/`.
Preserve the source notices and dependency declarations.
The validator rejects changed files, path traversal, symlinks, and custom build commands.
Optional `grammar_files` paths resolve from the family JSON directory.
Registration rebases these paths and the source directory to keep the installed definition portable.

```bash
lco family register my-family/family.json --registry work/families
lco family show work/families/my-family.json
lco plan --family work/families/my-family.json \
  --grammar grammars/base.json --out work/my-family-plan
```

Users can also define a named subset of an existing backend with `backend: "froghop"`.
That definition keeps the original source and exposes only its selected targets.
Its provenance retains `upstream_family: "froghop"`.

An original `source-conditional` constructor can declare `upstream_family: null`.
It still needs the source hashes, NOTICE, description, and authors.
Inherited templates retain their actual upstream family and source credit.

To add a shared rewrite, define its value map and phase obligations first.
Implement its fast binding and large binding next.
Register the binding names and add a failing-case test for each required premise.
Then mark the rule executable and measure its actual activation.

For a family setting, pin the source edit, its parameters, and its applicability guards.
Use `kind: "setting"` and the registered `frontier.counted_setting` large binding.
If no fast equivalent exists, set `bindings.fast` to null and explain the limit in `bindings.fast_reason`.
The frontier runner must validate the setting against its pinned source and run the full-width checks.

## Verification and scientific scope

The grammar admits a construction. The fixed evaluator checks its output, phase, scratch, and classical addend.
A development pass remains provisional until the frozen candidate passes its fresh large audit.
An input fails when either of two measurement streams fails.
The audit compares its declared confidence bound with probability `0.001`.

Exact replay checks source, recipe, and operation hashes separately from fresh reliability.
The frozen14 replay supplies no claim that a proposal policy discovered those points again.

The [mode pilot](../evidence/mode-pilot/README.md) exercised a one-rule compiled bundle with a registered Rust source family.
It ran model-free and Claude searches, then exported the measured-erasure candidate and checked submission planning.
The report states the audit scope, source pins, artifact reuse, and exact commands.
Inherited nonces stay fixed in these replay recipes.
Custom approximation edits need their own declared parameters and fresh qualification.

Run the grammar admission checks with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p test_grammar.py -v
```
