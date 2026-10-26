# Grammar pilot: measured findings

The extended grammar reduced median audited weighted remaining cost by 12.07% against the strongest control.
The prospective seeds confirmed a 12.04% reduction. No baseline median regressed.
The campaign compares the block grammar against both the historical search and an improved search with the old grammar.

The accepted target is ten percent lower weighted remaining cost, with no per-baseline median regression.
The campaign preserves the three baselines, recorded weights, and fixed semantic checker.

S multiplies global allocated qubits by the rounded mean executed Toffoli count.
Each seed score averages S divided by its fixed starting cost, using the recorded baseline weights.
The weights are 1 for inverse, 32.72575137951653 for replay, and 100 for width.
The campaign inherits the reference screen policy: 512 quick pairs and 4,096 long-development pairs.
It performs no new calibration. The historical contract (historical source reference) supplies that policy.

| Arm | Implementation |
|---|---|
| A | Historical random search and recipe grammar |
| B | Diverse parent selection, shared bridges, finite walk guards, old grammar |
| C | The same policy plus ordered block programs and direct constant lowerings |

## Audited result

| Seed group | A median cost | B median cost | C median cost | C reduction versus strongest control | Target met |
|---|---:|---:|---:|---:|:---:|
| all seeds | 0.515846 | 0.508935 | 0.447490 | 12.07% | True |
| historical seeds | 0.513010 | 0.507987 | 0.445238 | 12.35% | True |
| confirmation | 0.518681 | 0.509877 | 0.448501 | 12.04% | True |

Each seed has a weighted remaining-cost score. The table reports its median across seeds. Lower values are better.
Each seed score includes all three baselines. A failed audit endpoint receives its full starting cost.
The strongest control includes the historical median remaining cost of 0.5130103828443335.
The three prospective confirmation seeds are 101, 103, and 107.
Six search seeds support this finite comparison. They do not establish a broad search-performance theorem.

## Baseline checks

| Baseline | A median S | B median S | C median S | No regression |
|---|---:|---:|---:|:---:|
| inverse | 1,033,557 | 1,020,915 | 910,390 | True |
| replay | 1,038,555 | 1,020,915 | 894,790 | True |
| width | 1,032,087 | 1,018,563 | 896,805 | True |

## Validation and search cost

The fresh audit qualified 15 of 16 unique circuits at epsilon 0.001.
Each circuit used 16,384 fresh input pairs. The audit observed 14 failed input pairs across those circuits.
Every C endpoint had zero observed failures and qualified. The old-grammar endpoints account for the observed failures.
The existing audit applies its multiplicity adjustment before endpoint qualification.
All finalists froze before the audit created its input label. Failed endpoints remain in the results.

Each arm used 1,800 proposal slots across six seeds and three baselines.
Each duplicate consumes a slot. The campaign made zero paid provider calls.
Evaluation used 1970.1 CPU seconds and 6,187,008 input cases.
The accounting file includes evaluation calls and actual run times.

Host load was not controlled. Software and snapshot verification overlapped development searches.
Timing comparisons remain descriptive. Circuit cost uses emitted gates and allocated qubits.

The grammar inventory contains 112 programs, including 20 proved seed combinations.
Finite classical signatures refuted 56 programs and matched 56 programs at moduli 5 and 7.
Finite signature matches nominate equalities. Parameterized carry identities and block composition justify the admitted seeds.
Independent review checked coherent phase repair, control restoration, and unchanged legacy streams.

All 95 software tests passed in the owning repository. Its package matched the frozen campaign source.

## What changed

The old grammar fixed the internal constant arithmetic.
The new grammar exposes ordered arithmetic operations and a constant lowering choice for each operation.
The direct kernel emits carry chains without a full constant register.
This makes a cheaper compiled implementation reachable through search.
The block guide (historical source reference) distinguishes implemented mechanisms from the six framework ideas.
The kernel derivation (historical source reference) gives the arithmetic and phase obligations.

The old fitted-width endpoint has a concrete counterexample: x=8192, y=1.
At round boundary 13, it keeps 14 bits where the walk needs 16.
Its saved gates fail the clean-scratch check. The new endpoint passes the same constructed input.
This post-audit diagnostic changes no selection or score.
The diagnostic (historical source reference) preserves the input, measurement seed, and both receipts.

## Actual 256-bit transfer

Both circuits use measured cleanup, the original full-width walk bound, and 511 rounds.
The candidate transfers the complete audited payload program.
Q falls from 2,307 to 2,050. S falls from 4,229,803,755 to 3,750,222,850.
The observed S reduction is 11.34% across 32 development pairs.
Failed inputs: 0 before and 0 after.
This sample does not qualify the circuit at the challenge failure target.
Transfer findings (historical source reference) link the saved gates, inputs, seeds, and resource receipts.

## Evidence and reproduction

- Frozen study contract (historical source reference), manifest (historical source reference), and audit receipts (historical source reference).
- Audited endpoint table (historical source reference), assessments (historical source reference), and accounting (historical source reference).
- Grammar inventory and counterexamples (historical source reference), review (historical source reference), and review log (historical source reference).
- Complete proposal ledger (historical source reference) and its replay provenance (historical source reference).
- Separate metric cross-check (historical source reference), software tests (historical source reference), and snapshot tests (historical source reference).
- Saved source (historical source reference), [source manifest](source-manifest.json), and checker preservation (historical source reference).

```bash
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python runs/grammar-pilot-20261023/review/reproduce_review.py
PYTHONPATH=src python tools/report_grammar_campaign.py runs/grammar-pilot-20261023
```

The source directory preserves the campaign implementation. A new campaign requires a new directory and a new deadline.
The consumed audit remains immutable. Reproduce saved outcomes through saved-circuit replay.

The 256-bit transfer has its own controlled development record.
These division results supply no complete point-addition or challenge reliability qualification.
