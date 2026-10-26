# Candidate generator, v7 to v14: results and learnings

Date: 25 October 2026. Follows `finetune/REPORT.md` (v1 to v6) on branch `finetune/candidate-generator`.
Status: autoresearch loop stopped (three consecutive runs with no gain). Nothing below is pushed yet.

## 1. Headline

The fine-tuned model found two real cost levers in the ping-pong circuit: `SUB4_PP_WIDTH_RESCALE` and
`SUB4_PP_ROUNDS_MUL - 1`. Under the acceptance rule (score lower by more than 2 SE and 50k; classical failure rate
at most 0.0015 per input; phase garbage not counted), these give acceptable cuts of 0.20 to 0.30% of the score at
failure rates of 0.085 to 0.14%. The real commits on the same parents saved 0.01 to 0.04%.

On the 37 held-out test parents:

| Generator | Attempts | Acceptable | Parents covered |
|:--|--:|--:|--:|
| Model-discovered levers, applied by script | 88 | 34 | **13 / 37** |
| v6 model, fresh single-shot samples (Part B) | 296 | 2 distinct | 2 / 37 |
| Random +-1 setting moves (control) | 111 | 0 | 0 / 37 |

Training the model further (RFT, a compile-error fixer) did not turn it into a better generator.

## 2. What changed in the measurement

1. **The circuits are approximate.** The shipped circuit gets 11 to 22 of 9,024 test inputs wrong under any nonce
   except its own ground one. It passes the official check only because its nonce was searched until the sampled
   inputs avoided every failure (the challenge notes in `memory/WAYFINDER.md` confirm this). Any edit that changes
   the op stream also redraws the inputs and fails the official check. This is why every "valid" edit in v1 to v6
   was neutral: valid implied unchanged ops.
2. **Settings probe (v7).** At parent 55267662, 20 of 206 `set_default_env` settings change the emitted circuit,
   almost all `SUB4_PP_*` ping-pong knobs. The probe missed `SUB4_PP_WIDTH_RESCALE`, which is read straight from the
   environment.
3. **Paired, seeded evaluator (v8).** A private copy of `eval_circuit` (7-line patch, never used for submissions)
   takes its Fiat-Shamir seed from `V8_SEED`, so parent and candidate run on identical inputs, and it reports cost
   even when correctness fails. Calibration: the real improving commits come out cheaper with unchanged failures,
   nonce-only commits are detected as op-identical, and ROUNDS +-1 trade cost against failures in the expected
   directions. Seed noise on Toffoli is 6 to 9 per seed.
4. **Acceptance rule (Snehal).** Per-input classical failure rate around 0.001 is acceptable; operational cutoff
   0.0015; phase-garbage and ancilla batches do not count. No nonce grinding needed.

## 3. Results by version

| Version | Question | Result |
|:--|:--|:--|
| v7 settings probe | Which settings are live? | 20/206 live; +-1 moves look invalid because they lose the ground nonce |
| v8 evaluator | Can we measure edits without the nonce? | Yes; calibrated on real commits and controls |
| v8/v9 re-score of v6 outputs | Do old model edits help? | 3 acceptable cuts of 0.20 to 0.255% (7b3469ba, a8ab75ac, 787eaa82), 1 small (6b5c82cb, -0.04%) |
| v9 closed loop | Does feeding scores back help? | No: feedback 0/48, no feedback 1/48; 50% compile failures |
| v10 transfer | Do the two levers generalise? | Yes: cost cut on every parent where they exist; random moves never do |
| v10 Part B (BEFORE) | Fresh samples on 37 held-out parents | 2 parents with acceptable edits, both re-discoveries; 40% compile failures |
| v11 RFT (slides: SFT then GRPO) | Can we collect training data from the model? | First round void: a bug fed the model no code. After the fix (v12) 83% of outputs apply, but only 7.8% compile and change ops; gate (10%) failed; projected keep-set about 10 (needed 30) |
| v13 compile diagnosis + fixer | Can compile failures be repaired? | 66% of errors are mechanical, 20% semantic; a deterministic fixer recovered 2 of 64 (gate 5); killed |

## 4. Learnings

1. The model is useful as a **lever discoverer**, not as a reliable edit generator. Its value is in proposing which
   setting to move; a script then applies that move across commits far more reliably than the model.
2. Raw sampling keeps re-discovering the same few moves (identical scores on repeat), so more samples buy duplicates.
3. Compile failure is the main loss for model-written edits (40 to 53%), and most failures are mechanical range slips
   that neither range rebalancing nor de-duplication fixes.
4. Feedback in the prompt did not help; training on the model's own outputs could not start for lack of positives.
5. Process: three of the dead ends in this period were my own bugs (a generation script that dropped the user
   message, a parser crash on malformed op headers, an evaluator that ran environment-only items on a stale binary).
   Each was caught by a critic agent or a gate, not by luck. The three-critic gate also caught an overstated tally
   (2 strict confirmations, not 3).

## 5. Suggested next steps (if continued)

1. Use the model as a proposer of setting moves and let a script search the move space (cheap, CPU only).
2. If model-written code edits are still wanted: constrained decoding (paths and line ranges restricted to the prompt;
   REPLACE/INSERT only), then a model repair loop measured against a matched resampling control.
3. Grind nonces only for the single best combined candidate, and only if a submission is the goal.

## 6. v14: frontier steps (circuit before, circuit after, diff) (interim, 26 Oct 2026)

Request (Snehal, 25 Oct): analyse what lands on the frontier (notes/frontier_analysis_2026-10-25.md), then resume
training with the circuit before, the circuit after and the diff in each example. Critics: change/change/change
(decisions/v14_decision.md). Run as an under-powered pilot: only 93 train + 10 val frontier steps survived the filters
(658 accepted submissions; dropped: nonce-only 158, too large 149, table regenerations 138, tokens 62).

Arms (same 37 Part B prompts, same seeded paired evaluator, same acceptance rule: dS < -2 SE and < -50k, classical
rate <= 0.0015):
- v14: LoRA r16 on frontier steps x3 + v6 data, 2 epochs (loss 1.106 -> 0.549, val 0.873, test NLL 1.037).
- v14ctl (rank control): LoRA r16 on v6 data only, 4 epochs (loss 1.427 -> 0.575, val 0.770, test NLL 0.983).
- stage 0 (scripted): -1 cuts on 77 write-up knob families at the 37 parents.

Interim results (v14 34/37 parents, v14ctl 32/37; evaluations still running when this was written):

| Arm | Outputs | No apply | Compile fail | Emit fail | Same ops | Evaluated | Acceptable (distinct) |
|:--|--:|--:|--:|--:|--:|--:|--:|
| v14 | 266 | 17 | 107 | 15 | 79 | 48 | 1 (SUB4_PP_REPLAY_CHUNK/FOLD_WINDOW, -1.79M, rate 0.00141) |
| v14ctl | 254 | 14 | 86 | 10 | 87 | 57 | 6 on 3 parents (DIALOG_GCD_* / KAL_* knobs, -0.47M to -2.12M, rates 0.00134-0.0015) |
| stage 0 | 641 cuts | | | | 346+ | 130 | 0 (30 cost-significant cuts, all classical rate 0.00157-0.00247) |

Reading: the rank-16 control trained on v6 data alone produced edits outside the two known levers (WIDTH_RESCALE,
ROUNDS_MUL), which meets the pre-registered kill rule for that arm; the frontier-step arm did not beat it. The
frontier format (with after-metrics in the target) cost compile rate (107/266 compile failures vs 86/254). Rates sit
at the 0.0015 cutoff, so each acceptable edit needs an independent re-check before any claim.

## 7. What did not work (all versions)

- v1-v6 SFT on all commits or measured improvements: the model learned edit shapes, rarely the levers; most edits
  touched settings the build never reads, or failed to compile (40% of Part B outputs).
- v11 RFT pilot: gate failed (7.8% compile + op-changing < 10%); the first run had a bug that dropped the user turn
  (the model saw no code), caught by the Landmine critic.
- v13 deterministic fixer on compile failures: +2 op-changing compiles (gate 5) -> killed; 66% of errors were
  mechanical but most were outside the fixable window.
- Scripted -1 knob cuts (stage 0): large cost cuts exist but every one raises the classical failure rate above 0.0015.
- Nothing from the generator reached the public leaderboard; the curve points landed on 25-26 Oct came from the
  separate pareto5 dial pipeline (runs/pareto5-20261025 on the experiment/pareto5-20261025 branch), not from the model.

## 8. Files in this commit

`finetune/autoresearch/` (protocol, log, results table), `finetune/decisions/` (v7-v14 proposals, critic verdicts,
decisions), `finetune/notes/`, `finetune/ft2/` (probe.py, v8 seeded paired evaluator runner and job/result jsonl, v9
loop, v11 RFT, v13 diagnosis and fixer, v14 frontier builder and chains), `finetune/data_v11/`, `finetune/data_v14/`
(Git LFS), `finetune/runs_v14/{v14,v14ctl}/adapter` (Git LFS) with result.json, `finetune/gpuq/` (long-lived GPU
worker queue). Not included: checkpoints, the patched evaluator binary (built from the challenge harness), scratch
worktrees.
