# Fine-tuning study: retained data and limits

This bundle preserves the candidate-generation study through v14. Its frozen evaluation is incomplete. No generated edit contributed to the public frontier results.

The source is commit `0f762c895`.
The earlier evaluation records come from commit `cde226c204e3d57fe8d02f480f40e10e423164b2`.
[PROVENANCE.json](PROVENANCE.json) records the source paths, hashes, and export changes.
The export replaces diagnostic logs with hashes and removes machine paths.
It preserves the measured values and public source credits.

## Recount the results

From this directory, run:

```sh
python3 recompute.py --output summary.json
```

This command uses Python's standard library. It checks every retained file against its hash before it recounts the results.
It also checks the paired means and standard errors against the per-seed measurements.
It does not run a model or a circuit.

[summary.json](summary.json) contains the counts and accepted sample records.
The [source report](records/reports/REPORT_v7_v14.md) and [v14 decision](records/reports/v14_decision.md) preserve the original interpretation and planned gates.
Treat these as historical records. The library uses its own admission and submission rules.

## What the study measured

The model was `Qwen/Qwen2.5-Coder-7B-Instruct`, with bfloat16 weights.
Training used assistant-token loss on edits to public point-addition code.
The early study compared LoRA, OFT, and two QuIC adapter sizes.
The retained training runs use seed 42. They do not establish variation across training seeds.

The initial data had 321 training examples, 23 validation examples, and 47 test examples.
Version 6 used 293 training rows from 147 commits, 10 validation rows, and 39 test rows.
It removed eight nonce-only test examples. Three remaining examples still contain other edits alongside nonce changes.

In v1–v6, none of the valid generated candidates improved its parent's measured score.
All valid generated candidates had the same score as their parent.
Better edit syntax and more successful builds therefore did not establish useful circuit improvements.

The later study used a modified evaluator with eight fixed input seeds.
Each seed defined 9,024 inputs. Parent and candidate used the same inputs.
The historical rule required a mean score reduction above both 50,000 and twice its paired standard error.
It also required an observed classical mismatch rate at most 0.0015.
The rule excluded phase and ancilla failures. It had no correction for the number of tested candidates.
The [protocol excerpt](records/reports/acceptance_protocol_excerpt.md) preserves this definition.

These checks do not meet the library's joint failure criterion of 0.001.
The proposed candidates need fresh joint audits before a reliability claim or public submission.

## The v14 comparison

Both arms used LoRA rank 16, scale 32, learning rate 0.0002, and 10,092,544 trainable parameters.
Both sampled eight edits per test parent at temperature 0.7.
The comparison planned 37 parents and was still incomplete at the source commit.

| Arm | Training rows | Unique training commits | Epochs | Test parents started | Outputs | Compile failures | Paired evaluations | Parents with a passing sample |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Frontier format plus v6 data | 572 | 148 | 2 | 34 | 267 | 107 | 49 | 1 |
| v6 data control | 293 | 147 | 4 | 32 | 255 | 86 | 57 | 3 |

The frontier format put before-metrics in the prompt and after-metrics in the target.
Its 93 filtered training steps fell below the planned minimum of 150.
Of these 93 commits, 92 already occur in the control data.
The comparison mainly changes format and example frequency; it adds one unique training commit.

The control has seven passing sample rows, with score reductions from 0.47 million to 2.12 million.
Their observed classical rates range from 0.00134 to 0.00150.
Each accepted control patch also reduces `SUB4_PP_ROUNDS`.
An ablation must separate this walk-length change from its accompanying `DIALOG_GCD_*` or `KAL_*` setting changes.
The frontier-format arm has one passing sample with a 1.79 million score reduction.
That patch changes replay and fold settings. Its observed classical rate is 0.00141.

The source report counts six distinct control edits.
The retained rows contain seven distinct patch hashes and five distinct measured-outcome signatures.
The records omit emitted operation hashes, so this bundle does not claim a distinct-circuit count.
The report also precedes a few rows in the same commit. The table above counts the retained rows.

Earlier experiments found 34 passing scripted transfers on 13 parents, versus zero for 111 random setting moves.
Fresh model samples passed on two parents.
The corrected self-training pilot produced five operation-changing builds from 64 samples, below its 10% gate.
A deterministic repair step recovered two operation-changing builds, below its gate of five.
These observations support further tests of model-proposed settings with deterministic edits.
They do not establish a trained generator that reliably improves unseen circuit families.

## Data limits and next tests

The retained v14 training and test sets share no exact commit IDs, parent IDs, prompts, or targets.
The combined parent/child identity check also finds no overlap.
Related circuit families remain in both sets.
Repeated experiments informed later designs, so the evaluation sets are no longer untouched holdouts.
The prompts also identify code regions from known edits. They do not measure unrestricted source navigation.

A follow-up should freeze new parent families and repeat training across seeds.
It should match sampling budgets and compare deterministic setting searches with model proposals.
For the provisional candidates, first isolate each setting change. Then apply fresh joint failure audits under the library's frozen contract.
Constrained edit formats and measured repair attempts remain proposed improvements.

## Train or sample from the retained data

The optional scripts require CUDA, PyTorch, Transformers, PEFT, and Accelerate.
The recorded environment used PyTorch 2.5.1 and Transformers 4.46.3 with a custom PEFT fork.
LoRA uses the standard `LoraConfig` interface. QuIC needs that fork's `CompoundConfig` extension, which this bundle does not include.
The model weights and adapter weights are separate downloads.
The export checked Python syntax and replayed the records. It did not repeat GPU training or generation.

The scripts accept the compressed data directly. Run from this directory:

```sh
python3 train.py --method lora16 --lr 0.0002 --seed 42 --epochs 2 \
  --data datasets/v14/mix --out work/v14
python3 train.py --method lora16 --lr 0.0002 --seed 42 --epochs 4 \
  --data datasets/v14/ctl --out work/v14ctl
python3 generate.py --label v14 --adapter work/v14/adapter --merge \
  --data datasets/v14/test37.jsonl.gz --out work/v14-candidates.jsonl \
  --k 8 --temperature 0.7
```

These commands produce training receipts and raw candidate text.
The library must admit a generated edit through its typed rules before it can enter a campaign.
The frozen test set supports historical comparison. Use a fresh holdout when developing further methods.

[records/lfs_objects.json](records/lfs_objects.json) lists the upstream adapter and dataset objects by SHA-256 and size.
The archived model adapters are outside this public snapshot.
The compressed datasets and measured records needed for the offline recount are included.

## Scope of the retained source

`train.py` and `generate.py` preserve the training and sampling logic, with explicit data paths and gzip support.
`source/build_dataset.py` preserves the initial dataset builder.
`source/eval_candidates.py` preserves the initial official-screen experiment, which used a different acceptance rule.
It is historical study code and is separate from the library's campaign runner.

`source/v8_eval.py.txt` retains the seeded evaluator's Python runner for inspection.
The source commit omits its modified Rust evaluator and binary.
Full circuit replay of the seeded study requires that missing instrument.
This bundle can verify the saved measurements, but cannot reconstruct their inputs from the Python runner alone.

The v8 decision in the retained source record
identifies the base as `src/bin/eval_circuit.rs` at challenge commit `552676623a93d39f6000268db3d3cd8a9284090c`.
The base file has SHA-256 `b35314bc47a5f8eecbf60459e6c76c9b43aa034eeb9b7ec3421655d74f9e890b`.
The missing patch reads `V8_SEED` and prints cost and failure metrics even when correctness checks fail.
The runner expects the resulting binary at `ft2/v8/eval_v8`.
The exact patch, patched source hash, and build command were absent from the retained commit and inspected local evaluator sources.
Recover those original artifacts before attempting an exact replay.

Public contributors retain credit for their circuit code and commits.
The datasets keep those source identifiers. The fine-tuning study does not claim their changes as model discoveries.
