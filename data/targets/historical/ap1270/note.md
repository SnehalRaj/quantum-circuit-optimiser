Model: GPT-6 (Codex; effort setting unavailable)
Harness: Codex

# A source-replayed Leapfrog configuration at a 1e-3 error budget

## Result and scope

This candidate implements complete 256-bit secp256k1 point addition through the normal editable Rust interface.
It emits the following operation stream from source:

```text
SHA-256: 9271a9aa2b368b88989e905a932d5469baece5f3af00c0e928e8dcb7b9ce3c01
Compressed operation bytes: 39,457,885
Qubits: 1,270
Rounded mean executed Toffolis: 793,798
Q times rounded mean Toffolis: 1,008,123,460
```

The candidate passed all 9,024 official cases locally under the current public challenge harness at source `6b0327d60e40068fcbed5fedcb7f66d926f12320`.
The exact integer Toffoli total was 7,163,229,153 over 9,024 shots.
The harness wrote the score. The candidate source never writes the score file or changes the evaluator.

The live scalar leader at preparation time scored 915,666,254, with Q=1,234 and rounded T=742,031.
That circuit has lower Q and lower T than this candidate.
This submission records a source-replayed, independently tested configuration and its limitations.
Its resource numbers establish no improvement over that live scalar or Q/T frontier.

## Independent failure evidence

The research contract uses epsilon exactly 0.001 per input pair.
An input fails if either of its two measurement streams fails any required output, phase, workspace, or addend check.
Two campaign winners were frozen before a new common input label was drawn.
Each member received 262,144 fresh input pairs and two measurement streams.
Bonferroni allocation gives each member alpha=0.025, for 95 percent simultaneous coverage of the fixed two-member family.
The exact one-sided Clopper-Pearson upper bound must be at most 0.001.

This candidate failed 86 of 262,144 independent inputs and 169 of 524,288 measurement shots.
Its family-adjusted upper failure bound is 0.00040514070968419695.
There were 82 inputs with coordinate failures and 64 with phase failures; those sets overlap.
No workspace or addend failure was observed in this confirmation.
The other fixed winner failed 9 inputs and also met the threshold.
An independent review recounted all 1,048,576 saved outcome rows across the two members.
A direct binomial-CDF inversion reproduced both bounds without using the beta quantile function.
The lowest retained official score among the confirmed members selected this candidate.
The confirmation made no additional official execution and no model calls.

These finite results describe the stated random-input protocol and measurement streams.
The source permits approximation. A successful official packet therefore accompanies a nonzero measured independent failure rate.
The synthetic examples in `memory/qualification-summary.json` expose this limitation for further investigation.
Those examples are research records. The builder never reads them or chooses a circuit based on an input label.
The official packet remains the challenge's own fixed test.

## Source ancestry and credit

The material public dependency is submission `54acebaf-8d55-419e-b739-799d23bdbde0` by krishparadox.
Its published source commit is `377b8a464e221aaeefd9640b6c7ee2f83114c36b`.
This source supplies the Leapfrog point-addition family, its GCD walk, replay structure, modular arithmetic, and cleanup machinery.
The public parent scored 949,917,912 on its published official packet.
An earlier local repair in this project used wider, more conservative settings.
The local repair checkpoint is `48dcefa9f07af349f1561b345558541313cfc825`.
That checkpoint is an ancestor-derived project repair and is declared here as prior local work.
The current integration and selected recipe come from optimizer snapshot `18996572e82c0e48c409d699b5593c68d786f0fc`.
The parent algorithm remains inherited work.
The new work combines an explicit configuration interface, an executable arithmetic grammar, controlled source-guided search, and independent qualification.

Original attribution and NOTICE files remain with the source closure.
The root challenge harness, Cargo manifest, Cargo lock, toolchain pin, and evaluator sources retain the current public versions.
All code changes belong to `src/point_add`.
The submission declares its public dependency using the full submission UUID.

## What changed

The candidate fixes its settings in `WINNER_SETTINGS` in `src/point_add/mod.rs`.
It copies the required Leapfrog source and table closure under `src/point_add/leapfrog`.
Namespace references move under `crate::point_add`.
Compile-time table paths follow that location.
The configuration map uses fixed source values and does not take circuit settings from the process environment.
The earlier identity nonce tail remains disabled.
No nonce search, circuit padding search, or official retry selection produced this result.

Five settings differ from the imported repaired control:

| Setting | Imported control | Selected candidate |
|---|---|---|
| Forward fusion endpoint | 124 | 134 |
| One-fold payload window | 64 | 61 |
| Extra walk ticks | 12 | 4 |
| Walk workspace cap | 1290 | 1270 |
| Walk width padding | Conservative per-tick table | Uniform 4 |

These are coupled construction and precision choices.
The cap changes available workspace. Padding changes the signed walk rails.
Extra ticks change the fixed walk horizon. The payload window changes approximate fold work.
Fusion changes the point where the walk and payload share a step.
A candidate must also satisfy the source's construction assertions and clean up its state.
The selected circuit uses the inherited arithmetic program.
The wider research grammar supports ordered programs, but those program edits did not produce the winning cost here.

## Search protocol and measured comparison

The comparison uses the same complete point-addition interface and fixed evaluator for all generated candidates.
The earlier qualified PingPong control scored 1,308,373,780.
The imported repaired Leapfrog control scored 1,107,737,544.
Moving between those inherited implementations reduced the score by 15.33 percent.
The selected settings reduce it by a further 8.99 percent against imported Leapfrog.
The combined reduction against that frozen PingPong control is 22.95 percent.
These comparisons use the current 0.001 research cutoff.
The source's older, stricter repair goals remain separate historical experiments.
The reduction includes the use of a larger permitted approximation budget.

Two completed campaigns proposed 192 and 48 mutations.
The 48-proposal source-guided slate built 36 mutations and rejected 12 during construction.
Its four control circuits brought the evaluated row count to 40.
Both campaigns froze 32 distinct finalists before their independent audits.
Each finalist received 65,536 new input pairs and two streams, with alpha=0.05/32.
A statistical pass remained separate from the official packet.
The source-guided run had five qualified streams: two controls and three mutations.
The broader run had thirteen: two controls and eleven mutations.
Nine qualified circuit states used eight distinct edited arithmetic instruction sequences.
The best settings seed was one compound grammar action. A minimum required edit depth was not established.

The native searches made no candidate-feedback model calls.
They used Python for orchestration and Rust for emission and simulation.
The grammar exposed backend choices, typed settings, and ordered field-arithmetic instructions.
It retained failed construction recipes as potential parents for later edits.
The separate source-guided slate changed related settings together when isolated edits could not construct a circuit.
All rejected recipes, sample labels, operation hashes, and process results were retained in the research record.

## Reproduction

Start from this submission's source on the current challenge harness.
Use the toolchain pinned by the repository, Rust 1.93.0, and the existing declared crates.
Run the normal challenge commands:

```bash
ecdsafail setup
ecdsafail run
```

The equivalent local benchmark entry is `./benchmark.sh` after setup.
It removes stale operation and score files, builds the untrusted builder and trusted evaluator, confines emission, and scores the generated stream.
The source generates the operation stream through `point_add::build()`.
It does not embed a complete saved operation file or any precomputed benchmark answers.
The small binary tables are the inherited constant-arithmetic templates and retain verified source contents.
They are unrelated to the independent sample labels.
Check `ops.bin` against the SHA-256 above and inspect the harness-written `score.json`.
A build-only replay of the adapted source reproduced the exact stream.
Both source replay routes and the current official local run agreed on the operation hash and score.
The builder's local tests also exercise the editable arithmetic support and phase cleanup.

## Limitations and follow-up

The selected configuration improves the imported repaired control, while the current public frontier has lower resource costs.
A future result could move the Q/T frontier while its product remains above the scalar record.
Such a result must lie outside the existing resource curve and satisfy the receiving benchmark's validity policy.
The current selected CLI benchmark reports scalar promotion.
A small independent failure count remains eligible under the stated 0.001 research rule.
A candidate that fails the official packet remains a separate experimental result.
Our cheaper source-guided profiles include such failures, so a low development score alone does not establish eligibility.
Potential Q/T moves in the retained development history currently exceed the 0.001 observed failure limit.
A useful next step is to repair those profiles while retaining their resource advantage.
Each new profile requires its own tests and exact operation identity.

## Model and harness disclosure

The coding session identifies the model as GPT-6 and the harness as Codex.
The session's effort setting was unavailable.
The earlier local repair was prior project work; the public Leapfrog parent and its authors retain credit.
The final native search, replay, independent checks, and current challenge execution used no model calls.
