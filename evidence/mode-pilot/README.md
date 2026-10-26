# Execution mode pilot

The pilot ran the staged CLI from commit `49bdddbd3b5486739ab4e0e549bf92285c34fc2c`.
It tested the qualified Leapfrog control and an editable derivative of that control.
All five trials met their frozen reliability rule.
These runs establish functional execution paths. They do not establish a new frontier result or a model policy advantage.

## Results

Each fast check used 4,096 full 16-bit point-addition inputs.
Each large check used 32,768 secp256k1 point-addition inputs.
Each input used two measurement streams. An input failed if either stream failed.
The failure probability limit was exactly `0.001`.
Bounds below are the recorded one-sided binomial upper bounds.

| Run | Policy | Model calls | Large screen failures | Screen upper bound | Fresh final failures | Final upper bound |
|---|---|---:|---:|---:|---:|---:|
| AP model-free | none | 0 | 7 / 32,768 | 0.0004400953 | 7 / 32,768 | 0.0004012109 |
| AP Claude | none | 1 | 14 / 32,768 | 0.0007167425 | 7 / 32,768 | 0.0004012109 |
| Custom model-free | none | 0 | 13 / 32,768 | 0.0006783223 | Not requested | Not requested |
| Custom Claude, slot 0 | u | 1 | 10 / 32,768 | 0.0006019882 | Not requested | Not requested |
| Custom Claude, slot 1 | none | 1 | 9 / 32,768 | 0.0005608406 | Not requested | Not requested |

All fast checks had zero failed inputs. Their upper bound was `0.0007311126`.
Both AP contracts requested `audit_pairs=32768` and completed their fresh final audits.
Both custom contracts requested `audit_pairs=0`. The custom results have screening qualification only.
The JSON proofs record each audit's alpha, input packet hash, and per-input result hash.

The AP runs rebuilt the known control and matched its exact operation hash.
The official local check returned Q1270 and T793798.
The Claude AP run reused the verified operation artifact from the model-free AP run.
This was known-target reconstruction. The admitted AP menu contained that control only.

The custom family retained the AP constructor and its inherited credits.
Its `none` artifact matched the AP operation hash exactly.
Claude selected `u` first, then selected the sole remaining `none` recipe.
The second custom trial reused the verified custom control artifact.

The `u` rule replaced one large inverse-AND cleanup with measurement and a conditional phase correction.
The same rule activated at 3,658 fast-stage sites.
Both custom circuits used Q1270. Their measured mean Toffoli counts were near 793,801.
The audits used different packets. These means do not establish a cost reduction from the one large rewrite.

The three real Claude calls reported `claude-opus-5-5`.
The harness disabled tools and MCP, and removed API keys from the child environment.
The proofs retain the CLI usage and price estimates. Those estimates are not subscription invoices.

## Artifact identity and lineage

| Artifact | SHA-256 |
|---|---|
| AP and custom `none` operations | `9271a9aa2b368b88989e905a932d5469baece5f3af00c0e928e8dcb7b9ce3c01` |
| Custom `u` operations | `a5ea332d78084447ea358dd09d4a4eb69e26dafea821b3bcffda6e4d9ff26de1` |
| One-rule composed grammar | `c23e76731483f81db1c7527d0ac7c080816084a21c376a953342c2c836f91bab` |

The custom family is named `pilot-ap`. Its backend is `source-conditional`.
It derives from `qualified-leapfrog`, target `ap1270`, parent commit `023fbe13c59df2ff0087ea4bb916d25f3d4fac70`.
The family manifest pins 124 source files and preserves the parent dependency declaration.
The rebased relative manifest changes only the source directory path from the registered definition.
The manifest contains hashes and metadata. The Rust source remains in the generated, untracked template.
The exact `family init` command below recreates that template from the recorded controller checkout.
The export pins 133 files, including the fixed benchmark and generated wrapper.

The raw grammar contains only `cleanup.inverse_and_measure`.
Its typed parameters require guards and phase correction.
The real CLI composed it into a compiled grammar bundle.
Both custom searches consumed that bundle through `--grammar`.
This tests the complete composition-to-search path.

The fast constructor uses the original Kaliski arithmetic at 16 bits.
The large constructor uses the inherited AP arithmetic at 256 bits.
These runs test a shared compiler rule. They do not establish a scaled mapping of AP-specific arithmetic to the fast constructor.

## Reproduce the commands

Run these commands from a clean checkout of the recorded controller commit.
Set `LCO_MAIN` to the repository that will hold the evidence and work directories.
Set the `LCO_CARGO`, `LCO_CARGO_HOME`, and `LCO_RUSTC` variables to a Rust 1.93.0 installation.
The original run used an offline crate cache and a separate shared target cache.
`LCO_EMISSION_LOCK` must identify the same host lock as any other active circuit emitter.

```sh
export PYTHONDONTWRITEBYTECODE=1
export LCO_OFFLINE=1
export LCO_TARGET_DIR=/tmp/llm-native-target
```

The following commands express recorded absolute paths through `LCO_MAIN`.
They retain the actual options and trial counts.
Each campaign needs a new output directory when its source or contract changes.

```sh
./lco reproduce --family qualified-leapfrog --targets ap1270 \
  --config configs/offline.json \
  --artifact-root /tmp/lco-reproduction/qualified-leapfrog \
  --small-pairs 4096 --large-pairs 32768 --audit-pairs 32768 \
  --official --offline-build \
  --out "$LCO_MAIN/work/mode-pilot/ap-offline-v2"

./lco reproduce --family qualified-leapfrog --targets ap1270 \
  --config configs/claude.json \
  --artifact-root /tmp/lco-reproduction/qualified-leapfrog \
  --small-pairs 4096 --large-pairs 32768 --audit-pairs 32768 \
  --official --offline-build \
  --out "$LCO_MAIN/work/mode-pilot/ap-claude-v2"

./lco family init --from-target ap1270 --id pilot-ap \
  --out "$LCO_MAIN/work/mode-pilot/pilot-ap-template-v2"

./lco family register \
  "$LCO_MAIN/work/mode-pilot/pilot-ap-template-v2/family.json" \
  --registry "$LCO_MAIN/work/mode-pilot/registered-families-v2"

./lco grammar compose \
  "$LCO_MAIN/evidence/mode-pilot/inverse-and-only.json" \
  --family "$LCO_MAIN/work/mode-pilot/registered-families-v2/pilot-ap.json" \
  --out "$LCO_MAIN/work/mode-pilot/inverse-and-source.compiled-v2.json"

./lco search \
  --family "$LCO_MAIN/work/mode-pilot/registered-families-v2/pilot-ap.json" \
  --grammar "$LCO_MAIN/work/mode-pilot/inverse-and-source.compiled-v2.json" \
  --targets pilot-ap --trials 1 --config configs/offline.json \
  --artifact-root /tmp/lco-reproduction/mode-pilot-source \
  --small-pairs 4096 --large-pairs 32768 --audit-pairs 0 --offline-build \
  --out "$LCO_MAIN/work/mode-pilot/custom-offline-v2"

./lco search \
  --family "$LCO_MAIN/work/mode-pilot/registered-families-v2/pilot-ap.json" \
  --grammar "$LCO_MAIN/work/mode-pilot/inverse-and-source.compiled-v2.json" \
  --targets pilot-ap --trials 2 --config configs/claude.json \
  --artifact-root /tmp/lco-reproduction/mode-pilot-source \
  --small-pairs 4096 --large-pairs 32768 --audit-pairs 0 --offline-build \
  --out "$LCO_MAIN/work/mode-pilot/custom-claude-v2"
```

Source edits belong in the generated template. Update the manifest hashes before registration.
This pilot preserved the template source. It did not introduce a new arithmetic algorithm.
The tracked, rebased family manifest can inspect the same generated template with `lco family show`.

## Export and submission dry run

```sh
./lco export --run "$LCO_MAIN/work/mode-pilot/custom-claude-v2" \
  --slot 0 --out "$LCO_MAIN/work/mode-pilot/export-v2"

./lco submit --export "$LCO_MAIN/work/mode-pilot/export-v2" \
  --benchmark "$LCO_BENCHMARK"
```

`LCO_BENCHMARK` names a valid saved challenge Git checkout.
The recorded checkout path appears relative to this repository in `export-dryrun-v2.json`.
The command omitted `--execute`.
It verified the export and returned the proposed submission steps.
It created no worktree, ran no official rebuild, and made no external submission.
The benchmark's tracked-file status stayed unchanged.

The export contains the qualified `u` screen, source notices, and inherited dependency declaration.
Its archive is 493,311 bytes. Its submission note is 51,707 bytes.
The frozen custom contract permits export after screening qualification.
This dry run does not establish that the exported derivative passes an official rebuild or public acceptance.

## Evidence and limits

The four run proofs preserve hashes, audit scopes, activation counts, model receipts, and artifact reuse.
`pilot-ap-family.json` preserves the editable source pins and lineage.
`export-source-manifest-v2.json` and `export-dependencies-v2.json` preserve the export inputs.
`export-dryrun-v2.json` records the selected screen and dry-run outcome.
Raw packets, per-input verdicts, source builds, and model responses remain under the named local work directories.
The operation and build caches remain outside the tracked repository.

Run `python3 evidence/mode-pilot/verify.py` to check the compact proof.
Use `--raw` to check the saved runs and artifact hashes when the work directories and caches are available.
This verification does not rerun simulations.

The modes used different input packets. The custom runs also used different trial counts.
This pilot supports no matched comparison of proposal policies.
The separate 14-target Froghop reconstruction campaign has its own evidence and status.
The interrupted `ap-offline-v1` run remains recorded under work. It stopped before emission and supplied no final result.
