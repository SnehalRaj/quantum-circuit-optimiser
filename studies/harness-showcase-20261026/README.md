# Library showcase experiments

These experiments test two uses of the harness:

1. Express the same compiler improvements through a compact grammar and source code. Compare token use and results.
2. Ask an LLM to propose reusable compiler rules. Prove each rule and measure its effect on held-out point-addition circuits.

The [payload preview](model-payload-preview.json) contains the prompt templates.
This experimental driver supports local rule tests and whole-circuit transfer checks.
The public snapshot includes its source and unit tests. Full transfer results remain unfinished.
The manuscript uses the separate point-addition search study.

## Source-backed tasks

The training artifact is the retained Q839 Froghop circuit.
Its operation hash is `0f8ff05332b9394eb63221200872243d2991be30994e95bb8b977e72f45711a1`.
The full circuit contains 42,689,531 operations.
The native miner finds 27 distinct windows with three to seven gates, at most four wires, and at least two Toffoli gates.

A nonlinear-output rank bound proves that 24 windows cannot use fewer Toffoli gates in this language.
The remaining three windows express one Boolean factoring mechanism in different contexts.
Each occurs 894 times. Their occurrences overlap; the experiment measures complete transformed circuits to obtain the actual saving.
The selection uses source structure and a mathematical lower bound. It uses no model outcome.

For example, the same target can receive two controlled flips:

```text
CCX target control_a common
CCX target control_b common
```

The desired replacement must preserve every basis state and its sign.
The model receives the original gates and primitive meanings. It receives no replacement or catalog answer.
The proof enumerates all 16 basis states for each four-wire task.
The separate Rust adapter checks accepted rules with the fixed simulator.

The fast Kaliski circuit has no eligible four-wire windows of this kind.
The study therefore uses small exact tests of mechanisms mined from a full frontier source.
This supplies direct evidence for this compiler mechanism. It does not recreate every arithmetic family in Table 2.

## Matched interfaces

Both forms use the same gate alphabet, seven-gate limit, evaluator, and task information.
The compact form contains statements such as `CX 0 1; X 2`.
The source form contains a Python function that returns gate constructors.
An AST whitelist parses that function. The harness never executes model source.

Each form receives three repetitions of each task.
Each repetition has two calls with eight proposal slots per call: 36 calls at most.
Unused slots, invalid programs, and failed calls remain in the record.
The provider controls its sampling; the repetition labels are not provider sampling seeds.

The report compares the best proved cost for each task and repetition.
It records input, cache creation, cache read, and output tokens separately.
Unknown usage stays unknown. Known usage from invalid responses still counts.
Equal-result token efficiency requires both forms to reach the same result.
This experiment tests a bounded proposal interface. It does not compare complete ad hoc software engineering sessions.

## Compiler extension and transfer

The harness freezes accepted rules before held-out evaluation.
It applies each rule wherever its exact pattern matches, with fresh wire names.
Measurements, resets, classical fields, and register records stop a match.
A rule uses no new wires and must strictly reduce CCX/CCZ count.

The controls include:

- Random and evolutionary search: 20 fixed seeds, with results at 16 and 4,096 proposals per task.
- Bounded enumeration: 20,000 proposals per task.
- The library's existing exact local catalog, applied to each held-out circuit.

The transfer packages combine the first three declared repetitions of each proposal arm.
They select the cheapest proved replacement for each pattern using local results alone.
Q809 tests reuse within the Froghop lineage. AP1270 tests the separate Leapfrog lineage.
The existing catalog provides a strong reference for the attainable deterministic gain.
Matching that catalog demonstrates automatic rule recovery and reuse.
A claim of improvement beyond the complete deterministic library requires a gain beyond that reference.

Each distinct endpoint receives 8,192 fresh input pairs and two measurement streams.
The frozen family reserve is 32, with alpha `0.05/32` and epsilon `0.001`.
The input label is drawn only after all endpoint hashes freeze.
Failures remain in the results. The harness does not select a replacement after a final audit fails.

## Run

Build the native tools with `./lco setup`.
Reconstruct Q839, Q809, and AP1270 through the standard reproduction commands if their operation artifacts are absent.
Use their `ops.bin` files below. The training and held-out artifacts must differ.

```sh
python3 studies/harness-showcase-20261026/run.py prepare \
  --train /path/to/q839/ops.bin \
  --holdout q809=/path/to/q809/ops.bin \
  --holdout ap1270=/path/to/ap1270/ops.bin
python3 studies/harness-showcase-20261026/run.py offline
python3 studies/harness-showcase-20261026/run.py live
python3 studies/harness-showcase-20261026/run.py freeze
python3 studies/harness-showcase-20261026/run.py transfer
python3 studies/harness-showcase-20261026/run.py report
python3 studies/harness-showcase-20261026/run.py verify
```

`live` uses the authenticated Claude CLI. Review the payload and obtain any required data-disclosure permission before that command.
Use `--out` to create a separate run. A frozen run rejects source or binary changes.
A failed model call consumes its slot. Resume reuses a completed call only after checking its prompt, answer, receipt, and evaluated programs.
The offline verifier checks every expected call and the saved control proofs.

Operation artifacts and full verdict files remain under ignored `work/` paths.
Preserve that directory with the protocol and receipts for a complete research archive.
The checked-in payload preview contains prompt templates. Completed model replies remain in the retained run directory.
