# Results update, 24-25 October 2026

This file updates `AUDIT.md`, which predates these results. It records every public outcome, the evidence for each new decision, and the experiments that still need to run. Evidence lives in the optimizer repository, branch `experiment/final-pareto-eps1e3-20261024`, under `runs/final-frontier-20261024/` and `runs/frontier-levers-20261025/`.

Labels: **measured** (our run, receipts retained), **service** (the public service's own benchmark), **sourced** (another solver's note, not reproduced), **inference** (our calculation or judgment).

## 1. Public outcomes

All submissions below are ours. The service promotes only a new best Q x T. It verifies every valid submission and marks it `rejected` when Q x T is not a record. Curve membership is as of 25 Oct, 07:02 UTC (`runs/frontier-levers-20261025/public-snapshot-20261025-0702/`).

| Submission | Circuit | Q | T | Q x T | Service result | On the public Q/T curve |
|---|---|--:|--:|--:|---|---|
| 06b08594 | Native Leapfrog settings | 1270 | 793,798 | 1,008,123,460 | verified | no (Q1234-Q1240 points dominate) |
| 11613a51 | Q1097 SKY-COF + exact catalog | 1097 | 1,814,134 | 1,990,104,998 | verified | yes |
| d419f804 | Q818 froghop-one + lever r | 818 | 9,750,086 | 7,975,570,348 | verified | displaced (see below) |
| 7a7367a3 | Q839 froghop-one + levers u, r | 839 | 8,564,543 | 7,185,651,577 | verified | yes |
| 4274c7e0 | Q808 froghop-one + levers u, r | 808 | 10,640,971 | 8,597,904,568 | verified | yes |
| 31326aa6 | Q810 froghop-one + levers u, r | 810 | 10,282,406 | 8,328,748,860 | verified | yes |
| c9f82524 | Q811 froghop-one + levers u, r | 811 | 10,264,333 | 8,324,374,063 | verified | yes |
| 32e2e506 | Q812 froghop-one + levers u, r | 812 | 10,071,328 | 8,177,918,336 | verified | yes |
| b9c632ec | Q813 froghop-one + levers u, r | 813 | 9,907,605 | 8,054,882,865 | verified | yes |
| 0ee3673f | Q814 froghop-one + levers u, r | 814 | 9,829,517 | 8,001,226,838 | verified | yes |
| 28651875 | Q816 froghop-one + levers u, r | 816 | 9,803,523 | 7,999,674,768 | verified | yes |
| 7dd3b9ca | Q1059 SKY-COF, narrower cap + margins | 1059 | 1,978,608 | 2,095,345,872 | verified | yes |
| d3fecc7a | Q1063 SKY-COF, narrower cap + margins | 1063 | 1,970,390 | 2,094,524,570 | verified | yes |
| 309bf464 | Q1064 SKY-COF, narrower cap + margins | 1064 | 1,967,974 | 2,093,924,336 | verified | yes |

Q818 (`d419f804`) was on the curve when verified. At 05:52 UTC on 25 Oct, odinfree's `6d08713c` switched lever u on in its own Q818 row (T 9,748,991), cited our Q808 note, and displaced it. odinfree also posted Q817 and Q815, the two rows where our screens failed; its notes say it re-checked the nonce.

The scalar leader was 915,451,538 (Q1234, T 741,857) until jackylee0424's `da097c38` set 915,281,246 (Q1234, T 741,719) at 05:20 UTC on 25 Oct. The earlier leader's note states a ground nonce and about 19 failing shots per 9,024 (sourced); Track C measured 18.9 for the leader template on 131,072 fresh shots (an earlier 16,384-shot packet gave 25.9). Settings alone lowered it to 15.8 at +0.67% T and to 14.2 at +1.94% T (Q x T 921.8M and 933.4M), so the scalar record stays out of honest reach. The band Q1175-Q1233 admits only Q1233, at lambda 14.8-17.7 (`tracks/leapfrog-band/README.md`).

## 2. Every single public check we ran

A public check runs the unchanged public benchmark once: 9,024 shots drawn from the hash of the circuit's operations. One honest check passes with probability about exp(-9024 p), where p is the per-shot failure rate (measured on our audits).

| Circuit | Per-shot p (audit) | Predicted pass chance | Outcome |
|---|--:|--:|---|
| q1097-catalog | 2.44e-4 | 11% | pass |
| cof-cap1096-catalog | 2.28e-4 | 13% | fail (2 mismatches, 1 phase batch) |
| q1112-catalog | 7.09e-4 | 0.2% | fail (3, 2) |
| joint-cap1113-catalog | 7.96e-4 | 0.1% | fail (4, 4) |
| joint-cap1112-catalog | 7.42e-4 | 0.1% | fail (3, 5) |
| cof-cap1093-g20-c22 | 4.25e-4 | 2.2% | fail (1, 2) |
| cof-cap1090-g23-c21 | 4.70e-4 | 1.4% | fail (2, 1) |
| cof-cap1093-g24-c22 | 3.56e-4 | 4.0% | fail (1, 1) |
| cof-cap1093-g23-c21 | 4.41e-4 | 1.9% | fail (2, 3) |
| q839-r (screen) | not audited | about 76% (sourced lambda) | fail (1, 1) |
| q818-r | 0 in 40,960 shots | high | pass (screen and sealed run) |
| q839-u | 4 in 40,960 shots | about 41% | pass (screen and sealed run) |
| q839-ur | 0 in 40,960 shots | high | pass (screen and sealed run) |

Measured: the first nine checks predicted 0.34 passes in total and gave 1. The screening evaluator reproduced a sealed failure exactly, including the first failing shot (`screening/validation-01.json`).

Measured, in the check's own units (experiment X11): the public check counts classical mismatches per shot and phase garbage per batch of 64 shots. One failing shot can add to both counts. For the eight failed checks, the audits predicted 27.4 mismatches and 24.4 phase batches. The checks showed 18 and 19. Under the audit rates, a total this low has a chance of 0.04, so the audits may overstate the public failure rate.

Correction (25 Oct): an earlier version of this section compared 37.6 predicted failing shots with these 37 channel events. The units differ, so that match was not evidence.

## 3. Is the Toffoli reduction a repeating pattern?

Yes, within limits. Both moves apply an exact transformation that a public author left unused.

**Catalog (measured).** On the 8,192-pair development packet, the catalog lowered mean T on 23 of 36 public sources. It saved at least 1,000 Toffolis on 18 of them: eight SKY-COF rows (1,098-1,236), two joint-array rows (2,240-2,842), six rows from Q1133 to Q1144 (1,476-2,507), froghop Q818 (20,254) and Lilypad Q856 (45,490). It saved nothing on 13 sources, including every row from Q1164 to Q1240. An earlier ZX census on that lineage found the same: at most 36-62 removable Toffolis.

**Pass levers (measured and sourced).** The conditional-AND pass documents five levers (`m`, `u`, `f`, `s`, `r`). On the curve between Q791 and Q839, 23 rows run the pass. 13 use `mfs` and 10 use `mfsr`; none uses `u`. On the Q839 parent, `r` alone saved 4,092 mean T, `u` alone 1,103, and `u` with `r` 5,227. Parent and children had no failing input on the 8,192-pair development packet, so these A/Bs could not compare failing sets (correction of 25 Oct; an earlier version said the sets matched). At 16 bits, where failing sets are known exactly, 70 of 70 exact-lever arms kept the failing set (experiment X7). A batch added `u` and `r` to ten more rows (Q808-Q818): seven passed their screens and three failed.

**Independent sign (sourced).** odinfree's notes describe the same search: "mining public low-qubit notes for exact levers that a row's author had not applied to that row."

**Limits (inference).** The gains are 0.04%-0.06% of T. They lower T at a fixed Q and never touch the scalar leader. Whether a gain becomes a point depends on the parent's failure rate, since that sets the pass chance. A fast competitor can take the same move first.

## 4. Approximation disclosure

Our circuits inherit each parent's approximations: finite precision windows, truncated signed rails and fixed register widths. The exact moves add none. The rail study shows where the SKY-COF errors come from: a sign bit dropped when the walk trims a rail explains 138 of 213 coordinate-failing inputs in the Q1097 audit (`studies/rail-profile-20261024/`). The remaining 75 have an unknown cause. Every public note states the audited failure rate and the screening counts.

## 5. New decisions and their evidence

| ID | Decision | Evidence | Gap and next action | Priority |
|---|---|---|---|---|
| D24 | Look for exact transformations that public authors left unused | Catalog: 23/36 sources improved; verified point Q1097. Levers: 0/23 curve rows use `u`; three paired A/Bs, whose packets held no failing input; Q818 and Q839 verified. At 16 bits, 70/70 exact-lever arms keep the failing set (X7) | Full-scale lever exactness rests on the pass author's proof sketch. Run E9 with the aligned diagnostic on inputs that fail | P0 |
| D25 | Model the pass chance as exp(-9024 p) | 9 checks: 0.34 passes predicted, 1 observed. Check units: 27.4 mismatches and 24.4 phase batches predicted, 18 and 19 observed (chance 0.04, X11). At 16 bits the model fits exactly known rates (X3) | Audits may overstate the public rate. Record both channel counts for every screen (E10) | P0 |
| D26 | Allow several real-setting variants per region, one screen each, with disclosure | Snehal's decision, 25 Oct (`CONTRACT-AMENDMENT-01.json`). The screen equals the public check for the same bytes | A pass selects a packet; it does not lower p. Report screened and failed counts and the audited p in every note | P0 |
| D27 | Audit 20,480 pairs before submission | Snehal's choice. Zero failures bound p below 3.15e-4 per input at alpha 0.05/32, inside the 0.001 limit | Cannot separate lambda 0.3 from 1.0. Run larger audits for submitted circuits when compute allows | P1 |
| D28 | Park the global and targeted rail margins | Rails explain 65% of COF coordinate failures. Removing them all lifts the pass chance from 11% to about 38% for 5-12k T | Find the second mechanism first (E11) | P1 |
| D29 | Larger catalog outputs (4-5 gates) | In-memory census: +765 to +1,530 more Toffolis on Q1097; none on Q1112 | Build, certify and measure (E12) | P1 |
| D30 | Instrument changes | 400M-operation checker matched all 16,384 per-input outcomes of the frozen checker; memory guard 20 GiB for 13 GB builds | Record each change as an amendment; never weaken the checker | P0 |

## 6. Experiments that justify these decisions

- **E9, lever ablation.** For every curve row that runs the pass, build `r`, `u` and `u`+`r` variants. Measure paired development T and failing sets on a fixed packet, and run the aligned diagnostic (`SL_COND_ALIGN`) for a shot-by-shot comparison. Report per-row saving and any change in failing sets.
- **E10, pass-chance calibration.** For each screened variant, compare the predicted pass chance from its audit with the screen outcome. Fit the per-shot model and the batch-unit correction.
- **E11, second failure mechanism.** Classify the 75 unexplained coordinate failures on fresh inputs with the frame model's other checks and a product-precision probe.
- **E12, catalog grammar depth.** Compare output depths 3, 4 and 5 on the same parents at equal evaluator budgets, with the native certificate rerun for each table.

Each experiment fixes its inputs, labels and budgets before it runs. This follows the same rule as E0-E8.

**Small-scale tests, run 25 Oct (measured, exploratory).** Experiments X0-X11 test each strategy on the 16-bit and 256-bit division pipelines (`runs/strategy-experiments-20261025/PLAN.md`). At 16 bits a sweep of all 65,520 denominators gives each failing set exactly. The decision rules were written after the runs; `protocols.json` fixes the confirmatory repeats.

| Strategy | Test | Result |
|---|---|---|
| Catalog | X6 | Every rewrite exact; yield about zero on our own emitters, which have almost no reducible windows |
| Levers | X7, X10b | 70/70 arms keep the failing set and lower T; an 8,192-pair A/B missed 3 of 22 real changes; a fault inside an AND lifetime changes channel and rate |
| Pass chance | X3, X11 | exp(-lambda) fits with the per-shot rate; 20,480-pair audits overstate passes by 14%; public checks showed 71% of the predicted channel events |
| Variants | X4 | 1-(1-P)^k holds; a pass leaves p unchanged; audit gating stops unqualified submissions |
| Audit size | X5 | 82,702 pairs separate lambda 0.3 from 1.0; power is 0 at 20,480 pairs |
| Margins | X8, X9, P6a | Fits on 20,480 pairs remove 24-30% of true failures; at 256 bits a 96-bit trim lowers the reported circuit's T from 1,829,377 to 1,633,537 (-10.7%), and a confirmatory audit fixed before its run qualified it (0 failures in 20,480 fresh pairs) |

## 7. Where the human made the decisions

- Snehal set the error threshold (epsilon 0.001 per input) and the original ban on nonce search.
- After the reliability results, Snehal widened the rule to disclosed multi-variant screening (25 Oct). The wording is quoted in `CONTRACT-AMENDMENT-01.json`.
- Snehal asked for the odinfree and Pareto-corner studies. They moved effort from SKY-COF (pass chance about 11%) to the froghop rows (pass chance 50-90%).
- Snehal proposed landing a derivative of the Leapfrog circuit in the empty Q1175-Q1233 band. Track C found only Q1233 reachable, at lambda 14.8-17.7 (one-check pass chance 4e-7 or less), so no candidate was screened.
- The coordinator checked each note before submission. It removed an over-copied block from the inherited contract, and it replaced a misleading "0.00" failure row.

Coordinator records cannot show which ideas Snehal derived personally. D23 still applies.

## 8. Writeup tasks still open

1. Add a circuit diagram of lever `u`: a Toffoli uncompute replaced by an X-basis measurement and a conditional CZ.
2. Done (25 Oct): `figures/public_curve_20261025.{pdf,png,svg}` shows the public curve at 07:02 UTC with our 14 verified submissions; `figures/public_curve_20261025.csv` is its table view; `scripts/plot_public_curve.py` rebuilds it from the snapshot.
3. Done (25 Oct): `figures/pass_chance_20261025.{pdf,png,svg}` plots the predicted pass chance against the outcome for the 20 checks that had a prediction before they ran: 9 final-frontier checks (1 passed, 0.34 predicted) and 11 SKY-COF band screens (3 passed, 3.03 predicted). The froghop screens are left out because their audits ran after the screen.
4. Rebuild `Evidence_Audit_and_Experiment_Plan.pdf` with D24-D30 and E9-E12.
