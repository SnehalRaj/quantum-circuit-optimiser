# Matched model pilot

The pilot compares offline evolution, Claude Opus 5.5, and GPT-6.1-Sol.
It uses the frozen `blocks-v1` grammar and the existing circuit evaluator.
The study changes no production source.

Both conditions use three original 16-bit starts and the same three fresh seeds.
Each search receives 24 proposals per start.
Model feedback occurs at rounds 2 and 5.
Each model has 12 scheduled feedback attempts across both conditions.
The model proposes edits through the same rule executor as the offline policy.

- `guarded-seeds-v1` retains the production guards and shared construction path.
- `guarded-cleanup-only-v1` retains those guards and seeds measured cleanup at round 1.
  The chosen proposer selects all later actions.

The production path takes priority over queued model proposals during its seed actions.
The two conditions therefore test the interaction between model proposals and shared constructions.
This is a retrospective study designed after the earlier frontier results.

## Frozen records

`contract.json` fixes the budgets, seeds, source hashes, weights, and audit rule before the new search outcomes.
`identifier-amendment.json` records the supported GPT-6.1-Sol identifier.
The generic `gpt-6.1` alias failed its connection check before any GPT search.
That failure remains in `preflight/codex.json`.
The corrected identifier uses a separate `codex61sol` arm.

Claude reports its resolved model identifier.
Codex accepts the requested identifier but omits the resolved identifier from its JSON stream.
The records preserve this limit.
Both CLIs use subscription authentication.
The driver removes API-key variables from its own environment.

Every available arm freezes its finalists before the common fresh audit starts.
The audit uses 16,384 input pairs and two measurement streams per input.
The qualification rule uses epsilon 0.001 and alpha 0.05/128 per distinct circuit.
The fixed family reserve exceeds the maximum possible number of frozen circuits.
Failed endpoints retain starting cost in the weighted statistic.

## Files and commands

`run_pilot.py` prepares and runs the original matrix.
`identifier_amendment.py` preserves the alias failure and runs the supported identifier.
`continue_after_original.py` keeps the corrected arm and common audit sequential with the original matrix.
The logs retain completed searches and failed provider calls.
The `runs/` folders retain prompts, replies, token reports, proposal events, and evaluator costs.

After the common audit completes, regenerate the paper data without provider or circuit calls:

```bash
python studies/model-pilot/report_metrics.py
```

Run that command from the workshop paper directory.
It writes `data/model-pilot.json`.
The `arms` list supplies a compact interface for paper tables and figures.
The full `summary`, selections, contrasts, and call records preserve the underlying measurements.

The provider sums retain missing-usage counts.
An unreported field must remain unreported in the paper.
CLI cost estimates describe token prices; they do not establish subscription billing.
The explicit circuit packets match across providers.
The CLIs add different base contexts.

The saved model replies support exact offline inspection.
Fresh model calls can return different replies.
Create a new study and contract for another experiment.

## Observed results

The table gives the median weighted improvement across the three seeds.

| Construction policy | Offline evolution | Claude Opus 5.5 | GPT-6.1-Sol |
|---|---:|---:|---:|
| Full shared path | 55.519% | 55.519% | 55.519% |
| Measured cleanup only | 49.783% | 55.117% | 53.507% |

Both model arms improve over offline evolution on all three cleanup-only seeds.
The shared path reaches the same final cost in every arm.
These observations describe this small CLI workflow study.
They supply no general model-provider ranking.

All 1,296 proposal slots complete.
Claude completes all 12 feedback attempts.
GPT-6.1-Sol completes ten attempts; two exceed the 180-second limit.
Each condition contains one timeout.
Those calls provide no new proposals.
Existing pending proposals remain available.
The normal evolutionary policy supplies later mutations when the queue is empty.
The failed calls receive no retry.
Their token usage remains unknown.

The common audit covers 29 recipes and 23 distinct operation streams.
Each recipe receives 16,384 fresh inputs with two measurement streams.
Every recipe has zero observed failures.
The adjusted upper bound is 0.000478874716.
All 54 selected endpoints qualify.
The independent verification passes 241 checks of sources, budgets, scores, and bounds.
