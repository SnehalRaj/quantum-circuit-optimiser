# v14 decision (2026-10-25): frontier steps (before -> after + diff), staged and gated

Votes: Fairness-Correctness = change, Landmine = change, Time-Budget = change. Decision: GO with changes.
Data: map ALL accepted submissions to commits via origin/main "Accept/Validate submission <uuid>" messages; prior
metrics from the accepted submission whose commit equals the git parent (drop the step if none); keep .rs-only diffs
in src/point_add (memory/*.md allowed) with <= 300 changed lines and <= 4 files; drop steps that also change non-.rs
point_add files, new-file steps, nonce-only steps; strip nonce ops (build_v6 regex + SLOT/SUBMISSION_NONCE names);
build_v6 round-trip check; drop examples over 8192 tokens (prompt + target).
Format: prompt = v6 system + "Circuit before: score S0 (avg executed Toffoli T0 x qubits Q0)" + numbered child-hunk
windows + live configuration; target = "Hypothesis: <write-up headline>" + "Circuit after: score S1 (T1 x Q1);
change dT, dQ" + line ops. After-metrics only in the target; predicted vs real dS reported, not used for acceptance.
Primary endpoint: the old 37 non-burned test parents (BEFORE = Part B, k=8, T=0.7, same evaluator and rule). Training
steps restricted to child time < first test parent time and parent/child not among the 37 (or their children).
Secondary (descriptive): rolling frontier steps from 1 Oct with v6 and v14 on identical prompts.
Gate: >= 150 training steps after filters, else stop.
Controls: fresh LoRA r=16 trained on v6 data only (rank control). v14 = LoRA r=16 on frontier steps mixed 1:1 with v6.
Stage 0 (CPU, parallel): scripted +-1/+-2 moves on the knob families named in the 62+ write-ups at the 37 parents.
Kill rule: keep v14 only if it yields >= 2 acceptable edits outside WIDTH_RESCALE, ROUNDS_MUL and the Stage 0 list,
and its op-changing compile rate is not below v6's. Report distinct new levers; exact one-sided McNemar on the 37.
