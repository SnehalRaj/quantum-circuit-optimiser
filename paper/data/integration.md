# Normal pipeline integration

The normal experiment runner shares the block grammar, finite walk guard, and valid starting path across all three strategies.
Each starting action consumes one proposal slot. The fixed evaluator, baseline weights, and run budgets remain in place.

| Strategy | Historical | Grammar only | Integrated pipeline |
|:--|--:|--:|--:|
| Random mutations | 48.699% | 53.149% | 55.519% |
| Evolution | 47.720% | 39.092% | 55.519% |
| Evolution with Opus feedback | 47.790% | 54.012% | 55.519% |

The table shows median audited weighted improvement across the same three seeds.
Failed endpoints receive zero gain. Every baseline retains its weight.

## Target check

| Strategy | Remaining cost reduction against strongest historical control | No baseline median regression | Every endpoint qualified |
|:--|--:|:--|:--|
| Random mutations | 13.294% | yes | yes |
| Evolution | 13.294% | yes | yes |
| Evolution with Opus feedback | 13.294% | yes | yes |

The target passes for every strategy.

| Baseline | Strongest historical median cost | Integrated Random | Integrated Evolution | Integrated Opus |
|:--|--:|--:|--:|--:|
| inverse | 1,033,557 | 890,630 | 890,630 | 890,630 |
| replay | 1,033,557 | 890,630 | 890,630 | 890,630 |
| width | 1,015,329 | 890,630 | 890,630 | 890,630 |

An unqualified endpoint contributes its starting cost to this baseline comparison.

## Model comparison

| Strategy | Integrated median reward | Integrated mean reward |
|:--|--:|--:|
| Random mutations | 55.519% | 55.519% |
| Evolution | 55.519% | 55.519% |
| Evolution with Opus feedback | 55.519% | 55.519% |

Read every seed and the model receipts in the matched comparison (historical source reference).
Three search seeds supply a limited model comparison. These seeds also informed grammar development.
The production gain includes the shared valid starting path. This experiment changes grammar and policy together.
The separate A/B/C campaign (historical source reference) isolates the grammar contribution under a shared policy.

## Recommended use

Use the offline configuration (historical source reference) for this recorded baseline set.
It allows six proposals per baseline and makes no model calls. The matched study above used 100 proposals.
The prefix check (historical source reference) confirms that every final circuit already appears within six starting actions.
The configuration retains the fresh audit. It skips another copy of the measured full-width transfer.

## Preserved failure and repair

The grammar-only study (historical source reference) retains the Evolution regression.
Its rejected finalists use a short Jump-2 walk or an undersized width schedule.
The repair reuses denominator bounds from the earlier pilot. All proposal sources use the same guard.
Arbitrary payload programs still require full-circuit checks.

## Calls and evidence

The two integration campaigns recorded 12 feedback calls and 3 connection probes.
Failed calls: 1. The failed sandbox probe reported zero tokens and zero provider cost.
Reported input tokens: 65,304. Reported output tokens: 32,387.
Provider price estimate: $1.05 USD. This value supplies no subscription invoice amount.
Each campaign preserves its source, exact inputs, model receipts, gate streams, and single-use fresh audit.
Open the final dashboard (historical source reference). Read the full findings (historical source reference).

16-bit modular division. Full-width division transfer supplies development evidence; complete point addition remains separate.
