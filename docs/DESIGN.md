# Staged circuit optimisation harness

The repository consolidates the core source, grammar, evaluation, and replay tools used in the study.
It provides a16-bit full-point-addition fast stage and existing optimized secp256k1 backends for the large stage.
The paper describes the implemented harness and records each experiment's original versions.

## User workflows

Users select a family, combine grammar files, and choose model-free or LLM-assisted proposals.
Both modes use the same typed candidate interface and evaluators.
Search retains useful Q/T tradeoffs and freezes finalists before independent audits.
Reproduction resolves14 known frozen Pareto targets from25October2026,15:46UTC.
It checks exact source, recipe, operation hashes, official counts, and fresh reliability separately.
Reproduction supplies no claim that either mode rediscovers those targets.

The initial family registry must include Froghop and existing optimized source-backed families.
Reuse tested Leapfrog and Skywalk adapters where their dependency closure permits it.
A user can register a custom family with a constructor, typed parameters, both stage bindings, and provenance.
Registration validates capabilities before search.
A relabelled template retains its inherited family attribution.

## Grammar composition

The base grammar describes reversible gates, arithmetic, cleanup, and measured erasure.
Family files add source-bound mechanisms and parameter domains.
Composition accepts any number of files.
Rules have stable namespaced IDs, revisions, typed parameters, capabilities, guards, stage bindings, and evidence.
Identical definitions deduplicate; conflicting definitions require an explicit override or fail.
The canonical combined grammar has a stable hash and source map.
Instruction order belongs to a candidate program, not the file-composition order.
Rules unavailable in the selected family remain visible with a reason and cannot execute silently.
Custom grammar needs real emitter bindings before activation.

## Stage boundary

The fast stage emits reversible16-bit point addition and checks outputs, phase, scratch, and the classical addend.
It uses a curve with a = 0, b = 7, p = 65419, order64921, and generator(3,16991).
The large stage uses the actual optimized secp256k1 family.
It uses the shared gate format and fixed checker.
Every rule declares how its mechanism maps between stages.
Require nonzero rewrite counts when a compiler pass is the tested mechanism.
Parameter mappings retain source context and width guards.
Fast results are development evidence; final claims use full-width checks and fresh audits.

For all14 retained targets, the relevant mechanism is the same guarded conditional compiler pass.
Its u action replaces inverse ANDs with measurement and a conditional phase correction.
Its r action absorbs unread writes before measured cleanup.
Use the exact pinned compiler at both stages.
The small constructor uses Kaliski arithmetic; the large targets use Froghop.
This is a cross-backend test of the compiler mechanism, rather than a scaled Froghop construction.

## Trust and provenance

Only the emitter imports candidate construction code.
The evaluator has no candidate dependency.
Hash source, grammar, executables, operations, input packets, and verdict records.
Keep the failure limit at probability0.001.
Count an input as failed when either measurement stream fails.
Use clean child-process environments and preserve all compiler guards.
Keep inherited nonces fixed and disclose their provenance.
Preserve source licenses, notices, dependencies, and author credit.
Use no blanket license for third-party source without a valid basis.

## Models and commands

The core CLI has help for setup, data verification, grammar composition, family registration,
planning, search, reproduction, verification, and submission export.
JSON configurations define model-free, Claude CLI, and Codex CLI modes.
The CLI records requested and reported model identifiers and actual usage.
Subscription CLI authentication is the default model route; no API key is required.
Model feedback produces checked actions and cannot change the evaluator or failure limit.

The README contains the vector pipeline figure, complete commands, family examples,
configuration examples, data descriptions, and measured limitations.
The paper, clean datasets, experiment summaries, and source catalog live in this repository.
Large generated caches remain outside Git; source recipes reproduce them.
Submission export contains the exact editable files, dependency declarations, and a public note.
External submission needs an explicit execute action.

## Completion evidence

Test grammar conflicts and family capabilities.
Test model configuration, malformed replies, timeouts, and absent credentials.
Test interrupted runs, hash changes, invalid circuits, phase faults, scratch faults, and addend changes.
Test model-free and LLM-assisted reconstruction of all14 targets.
Measure real small-rule activation before large generation.
Test at least one other optimized family and the custom-family registration path.
Keep a ledger of shortcomings, repairs, and unresolved limits.
Update the paper from completed results only.
