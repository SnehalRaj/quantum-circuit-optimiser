# Detailed integration and challenge publication report

The measured outcome is partial cost recovery after a reliability repair.
The selected circuit costs **6.13% more than its public Leapfrog ancestor** and **10.10% more than the current leader**.
The 8.99% gain uses our conservative imported repair as its denominator.
The 22.95% gain also includes the change from the more expensive PingPong control.
Neither percentage measures an advance over the public frontier.
The selected circuit uses the inherited arithmetic program. Its five changed settings produced the winning cost.
A matched independent reliability test of the current public leader remains pending.

## Where we started

The starting control was already an optimized PingPong circuit. We then used a repaired version of an earlier public Leapfrog frontier implementation.
The inherited implementation and the new settings search account for separate gains.

| Point | Q | Rounded mean T | S | Role |
|---|---:|---:|---:|---|
| Challenge launch display | 2,715 | 3,962,753 | 10,758,874,395 | Public API context |
| Public Leapfrog ancestor | 1,236 | 768,542 | 949,917,912 | Published source ancestor |
| Frozen PingPong control | 1,316 | 994,205 | 1,308,373,780 | Starting control for this study |
| Imported repaired Leapfrog | 1,292 | 857,382 | 1,107,737,544 | Conservative imported starting route |
| Other session's clean PingPong | 1,312 | 978,653 | 1,283,992,736 | Independently qualified parallel study |
| Selected Leapfrog settings | 1,270 | 793,798 | **1,008,123,460** | Current selected result |
| Current public leader | 1,234 | 742,031 | **915,666,254** | Live frontier comparison |

The local L2 repair descends from public source `377b8a464e221aaeefd9640b6c7ee2f83114c36b`.
A Git ancestry check confirms that relationship.
An earlier project diagnostic recorded 85 failed inputs among 32,768 for that parent on its stated development packet.
The local repair increased cost to reduce failures on new inputs.
The current search uses epsilon exactly 0.001, which permits more approximation than the earlier strict repair goals.
The selected result improves the repaired control. It has a higher raw score than its earlier public ancestor and today's public leader.
The source algorithm is inherited. Our measured additions are the integration, executable grammar, selected settings, and qualification workflow.


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

## Integration into main

Main advanced independently to `43a500fe6` while this branch developed.
The merge preserves its clean point-addition command, guarded proposal policy, and future 0.001 defaults.
It also retains the frontier command, four native backends, editable arithmetic programs, and OpenEvolve study.
Historical frozen contracts retain their recorded criteria.
The combined code passes 264 Python tests and 53 Rust tests, with one explicit Rust ignore.
The public source adapter passes 18 Rust tests, with zero failures and zero ignores.
The combined generator and public adapter both reproduce the selected 39,457,885-byte operation stream exactly.
The current official benchmark passes all 9,024 cases at S=1,008,123,460.

The merge retains both versions of one historical PDF and both original inventories.
They live under `merge-evidence/20261024/opus55-blocks-20261023/`.
The canonical run uses main's PDF and keeps every unique file from both branches.
Its combined inventory covers 10,026 files.
Both original LFS gate streams were restored and checked against their recorded hashes.

## Candidate portfolio and Q/T

The portfolio screen covers 538 saved candidate records across the completed campaigns and the older V1 checkpoint.
All candidates within the observed 0.001 development failure rate are dominated by accepted public Q/T points.
Some Skywalk development rows lie outside the curve formed by accepted records, but exceed the current error limit.
Those rows need a reliability repair and new tests before a frontier claim.
Cheaper Leapfrog profiles can meet the independent error bound while failing the fixed official packet.
For example, `687d25b0` measured S=1,003,513,360, failed 28/65,536 independent inputs, and had upper bound 0.000724800553.
Its earlier official packet failed. This is a separate eligibility condition from its nonzero independent error count.
The screening preserves such candidates in the report and data instead of requiring perfect million-input results.

A Pareto improvement can reduce Q at a given T or reduce T at a given Q.
A new point must avoid domination by the existing resource curve.
One point dominates another when neither resource is larger and at least one is smaller.
Equal duplicates supply no frontier advance.
The selected circuit is dominated by the live leader on both resources.
Its product is 10.10% higher than the leader's score.
The live leader has not been independently audited here under our exact random-input protocol.

The public site displays a Pareto chart.
The selected CLI endpoint reports mode scalar and automatic promotion.
The public platform catalogue contains one open ECDSA benchmark and no separate open ECDSA Pareto execution track.
The returned server decision controls publication and promotion status.
The public snapshot and candidate-portfolio.json preserve the comparison inputs.

## Delivery status

The public source package contains only permitted `src/point_add` changes.
The private optimizer repository and personal research material remain private.
The public submission note exceeds the required 5 KiB and declares its material public source dependency.
The server registered submission `06b08594-7509-4e81-9ca3-f620b2ab1437` on 24 October 2026 at 07:17:43 UTC.
It returned the official score **1,008,123,460**, Q=1,270 and T=793,798.
It returned status `rejected`, with reason `score did not improve current best`.
The source is preserved in public commit [`023fbe13`](https://github.com/Layr-Labs/ecdsafail-challenge/commit/023fbe13c59df2ff0087ea4bb916d25f3d4fac70).
This result confirms execution and score. It does not establish a public frontier advance.
The receipt is `integration-20261024/public-submission-result.json`.
The development change merged into main as `c885ee4470f49e984303bd931a7a97a2e832d0f8`.
The retained report records a passing CI run.
The merged code exactly matches the reviewed feature tree.
Fresh tests on that merged code passed 264 Python tests and 53 Rust tests, with one explicit Rust ignore.
The primary checkout contains concurrent work from another session. Those files remain untouched.
The clean reviewed worktree provides the merged main code and validation artifacts.
The receipt is `integration-20261024/main-integration-receipt.json`.
