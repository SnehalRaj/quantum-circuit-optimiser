# LLM-Assisted Quantum Circuit Optimisation: A Systematic Study

The paper presents the public results, the circuit methods, and the controlled search studies.

- [Paper PDF](LLM_Assisted_Quantum_Circuit_Optimisation.pdf)
- [LaTeX source](main.tex)
- [Public snapshot summary](data/frontier-summary.json)
- [Frontier owner table](data/frontier-users.csv)
- [All our submissions, commits, parents, and resource differences](data/own-submission-families.csv)
- [Family summary and extrema](data/own-submission-families.json)

The snapshot was captured at **12:05:24 UTC on 26 October 2026**.
The analysis uses that publication cutoff and retains 1,778 eligible results from 2,211 submissions.
The public curve has 215 distinct coordinates. Our 46 coordinates represent 21.4% of the curve.
Fifty-one of our submissions reached the curve when published; five have since been displaced.
Our 52 public entries have distinct commits and published resource counts.
Of these commits, 46 remain on the frontier: an 88.5% success rate.
Each tied coordinate belongs to its earliest published entry. This allocation measures coverage; source credits remain separate.

Table II groups all 52 measured commits by family and orders their maximum Toffoli savings from high to low.
Each difference uses the declared parent, except Leapfrog, which uses the local repaired control.
The Froghop maximum saves 271,963 Toffolis and adds 20 qubits.
The SKY-COF qubit maximum saves six qubits and adds 85,793 Toffolis.
Separate maxima can describe different circuits. Public resource differences use separate input packets; paired tests isolate compiler effects.
The packed-inversion family also saves up to six qubits, with 1,811,710 more Toffolis.
The frontier includes 44 packed-inversion points and two shared-cofactor points.

The family plot classifies 1,152 eligible submissions from their notes and omits 626 unclassified submissions.
The frontier panel includes all 215 coordinates and labels one as unclassified. Each panel sorts its families by decreasing count.
Removing 48 repeated source/Q/T rows leaves 1,730 observations and preserves the frontier.
The exact selection counts remain in [the admission table](data/admission-table.tex).

The snapshot at 01:51 UTC on 26 October remains in files with the suffix `20261026T015112Z`.
The earlier snapshot at 15:46 UTC on 25 October remains in files with the suffix `20261025T154600Z`.
It contains the fourteen original width recipes and their source maps.
Their independent 20,480-input audits form a dated cohort. Public notes supply separate evidence for other submissions.
This snapshot update performs no new circuit audits. The inventory retains each note hash and source identity.
The earlier 14:00 response remains in `data/public-submissions-140019.json.gz`.

Our product candidate has Q1270, T793,798, and QT1,008,123,460.
Its ninefold observed failure ratio uses public submission `46ae05c5` at commit `6b0327d6`, frozen on 24 October, with the same 32,768 inputs.
The counts are 9 and 81 failed inputs. This comparison does not use the 26 October product leader.
The larger confirmation audit and matched comparison retain their original source identities and confidence allocations.

## Experiments and tested scope

| Study | Comparison | Records |
|---|---|---|
| Grammar capacity | Three nested grammars over 16 full point-addition circuits | [Results](data/point-addition-search.json), [protocol](studies/point-addition-search-20261026/protocol.json) |
| Model feedback | Random search, evolution, Claude, and GPT; five runs and eight proposals each | [Results](data/point-addition-search.json), [study](studies/point-addition-search-20261026/README.md) |
| Point-addition timing | Full 16-bit and secp256k1 circuits; 100,000 input pairs each | [Results](data/point-addition-timing.json), [scope](studies/point-addition-timing/README.md) |
| Fine-tuning | Partial paired tests of frontier-formatted training and a rank-matched control | [Recount](data/finetuning-summary.json), [full records](studies/historical/finetuning/) |

The fast benchmark computes full 16-bit point addition. Shared compiler rewrites need implementations at both widths.
Full-width layout settings record their missing small-stage mapping and run directly on secp256k1.
The bundle retains the historical division and register-release studies outside the manuscript tables.
Every full-width candidate still needs its own checks and audit.

The full point-addition study separates common preparation from subsequent edits.
Preparation reduces QT by 5.81%. Cleanup reduces the prepared parent's QT by 11.70%; register release raises that reduction to 18.60%.
Claude's first-proposal median is 17.57%, compared with 7.77% for evolution.
Evolution and both models reach the same 18.60% median after eight proposals.
All 17 circuits pass fresh 16,384-pair joint audits with zero failures and an upper bound of 0.000365624, rounded upward.
The comparison measures early search efficiency within one finite grammar.
The public frontier points come from deterministic compiler and layout edits.

The fine-tuning study uses a classical-failure limit of 0.0015 and has no independent joint-check qualification.
Its candidates account for none of the public frontier points reported here.
The Limitations section proposes search over typed arithmetic programs; the 23 research and specification cards still lack executable bindings.
It also shows a proposed fine-tuning loop with separate training and held-out evaluation.

Timing uses an Apple M2 workstation with eight cores and 24 GiB of memory.
The 16-bit checker uses one thread; the secp256k1 checker uses four.
The timing table reports validation time only, in seconds. Circuit generation and compilation remain outside that measurement.

## Build from saved evidence

Use Python 3.11 or later, the packages in `requirements.txt`, Inkscape, and TeX with REVTeX and latexmk.

```bash
python -m pip install -r requirements.txt
make PYTHON=python
```

The build makes no model, network, or circuit calls.
It regenerates tables and vector figures, compiles the PDF, and checks the saved evidence and document structure.
It recounts all 557,056 saved stream verdicts from the point-addition search study and the retained fine-tuning results.
The package includes both studies and the six source files pinned by the search protocol.
These source files support evidence checks. Use the library checkout to build circuits or run new searches.
The Froghop diagram uses [TikZ](figures/froghop-circuit.tex); its [source map](studies/froghop-source/README.md) records the constructors.
The pipeline uses an editable SVG with setup, fast-loop, and submission rows.
The proposed fine-tuning pipeline uses [TikZ](figures/finetuning-pipeline.tex).

The [library](https://github.com/SnehalRaj/quantum-circuit-optimiser) packages the circuit methods and their experiment records.
Historical records identify the implementations that produced each measurement.
The source bundle preserves scientific provenance while removing private workspace paths from shareable copies.
Original record hashes and transformed-copy hashes remain distinct.
The study scripts can perform new experiments; they are outside the offline paper build.

The verification report records completed checks. Successful compilation does not imply author acceptance or a fresh circuit replay.

## Reader review

Each manuscript revision needs a critic pass for first-use definitions, connected paragraphs, and clear comparison baselines.
Check for draft-history language that divides one reported experiment set into original and added results.
State concrete future studies in place of vague claims followed by generic disclaimers.
Define the joint-check limit once in Approximation tuning. Distinguish observed failure rates from statistical bounds.
Keep source identities and audit provenance in the source guide when they interrupt the research argument.
