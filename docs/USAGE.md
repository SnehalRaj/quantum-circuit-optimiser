# Harness guide

## Quick start

Use Python 3.10 or later and Rust 1.93.0.
The source launcher needs no Python package installation.
Rust builds use the pinned Cargo lockfile.

```sh
git clone https://github.com/SnehalRaj/quantum-circuit-optimiser.git
cd quantum-circuit-optimiser
./lco --help
./lco doctor
./lco verify-data
./lco setup
```

For an existing Rust cache, set its paths before setup:

```sh
export LCO_CARGO=/path/to/toolchain/bin/cargo
export LCO_CARGO_HOME=/path/to/cargo-cache
export LCO_OFFLINE=1
./lco setup
```

These values stay in your shell.
Tracked configurations contain no account credentials or machine paths.
`LCO_TARGET_DIR` can select a shared build cache.
Generated circuits and binaries stay under `work/` by default.

## Choose a family and compose its grammar

```sh
./lco family list
./lco family show froghop
./lco targets
./lco grammar compose \
  grammars/base.json \
  grammars/families/froghop.json \
  --family froghop --out work/grammar.json
```

The base grammar defines shared arithmetic and cleanup contracts.
Family addenda describe their source mechanisms and limits.
Composition accepts any number of files.
Identical definitions deduplicate.
Conflicting definitions require an explicit source choice:

```sh
./lco grammar compose grammars/base.json my-rules.json \
  --override cleanup.inverse_and_measure=my-rules.json \
  --out work/extended-grammar.json
```

Each rule records typed parameters, guards, source evidence, and both stage bindings.
The composer reports active rules and blocked reasons.
The default staged-search inventory contains 29 rules.
Six rules execute; 23 remain blocked until their implementations and mappings exist.
A source description alone does not make a rule executable.
Composition joins rule definitions. The family constructor implements their arithmetic operations.
See [the grammar guide](../docs/GRAMMAR.md) for the full inventory and extension contract.

Five guarded compiler rules can combine independently:

| Flag | Mechanism |
|---|---|
| `m` | Reduce a known control |
| `f` | Replace a phase-only AND with direct phase terms |
| `s` | Build an AND under its classical read condition |
| `u` | Erase an inverse AND by measurement and conditional phase correction |
| `r` | Absorb unread writes before measured cleanup |

These rules form 32 subsets, including `none`.
An independent exact cancellation rule adds an optional second action.
The resulting menu contains 64 combinations per source recipe.
The cancellation rule removed 588 adjacent pairs on the full 16-bit circuit, with unchanged Q and T.
The unchanged compiler orders its analyses independently from the grammar file order.
Rules act only where their guards hold.
Receipts show actual rewrite counts.
The small constructor has no observed `s` sites.
An inherited `s` setting remains recorded source context.
It gives no small-stage evidence for a new `s` mutation.

## Run the two loops

List the admitted recipes before circuit generation:

```sh
./lco plan --family froghop --targets q839 \
  --out work/plan
```

Run model-free evolution:

```sh
./lco search --family froghop --targets q839 \
  --config configs/offline.json --strategy evolution \
  --trials 8 --small-pairs 4096 --large-pairs 16384 \
  --audit-pairs 32768 --out work/offline
```

Evolution proposes one-rule changes from retained parents.
`--strategy random` uses a seeded order over the same admitted menu.
Both modes retain useful Q/T trade-offs.
The run freezes its finalists before fresh audit inputs.
The run ends when its trial budget or unique menu is exhausted.
Search and reproduction request 32,768 fresh audit pairs per finalist by default.
Use `--audit-pairs 0` for a screening-only run. Screening-only results cannot export a submission.

Choose another optimized constructor with the same interface:

```sh
./lco search --family qualified-leapfrog --targets ap1270 \
  --config configs/offline.json --trials 4 \
  --small-pairs 4096 --large-pairs 16384 \
  --out work/leapfrog
```

The 16-bit constructor uses Kaliski arithmetic.
The full backends use their inherited optimized arithmetic.
This setup tests shared compiler mechanisms across constructors.
Family-specific schedule changes require their own small-stage mapping.
The grammar reports missing mappings explicitly.

The four policies passed 100,000 fresh inputs each, with zero observed failures.
Each input used two measurement streams. The joint confidence allowance was 0.05.
All cases used 214 qubits.

| Policy | Measured T | Failed inputs | Upper bound |
|---|---:|---:|---:|
| `mfs` | 31,769 | 0/100,000 | 0.0000438193 |
| `mfsu` | 28,111 | 0/100,000 | 0.0000438193 |
| `mfsr` | 31,709 | 0/100,000 | 0.0000438193 |
| `mfsur` | 28,051 | 0/100,000 | 0.0000438193 |

Reproduce this native study without an LLM:

```sh
./lco qualify-native --pairs 100000 --out work/native-study
```

## Optional LLM guidance

Log in through the provider CLI before this mode.
The harness removes API-key routing variables from the model child process.
Each process has its tools disabled or a read-only sandbox.
Each response must select an admitted recipe.

```sh
./lco search --family froghop --targets q839 \
  --config configs/claude.json --trials 4 \
  --out work/claude

./lco search --family froghop --targets q839 \
  --config configs/codex.json --trials 4 \
  --out work/codex
```

The presets request `claude-opus-5-5` and `gpt-6.1-sol`.
Edit the JSON to choose another available model, timeout, effort, or call allowance.
The CLI records requested and reported model identities separately.
Missing reported identities remain unknown.
Failed calls and malformed recommendations remain in the run records.
The harness never converts a failed model call into an offline recommendation.

## Editor workflow

Open the checkout in Cursor or your preferred editor.
Run the documented `./lco` commands in its terminal.
Give the editor's agent [AGENTS.md](../AGENTS.md) and [CONTRIBUTING.md](../CONTRIBUTING.md) as project context.
The repository provides a terminal workflow. It does not include a Cursor extension or direct Cursor model adapter.

## Reproduce the frozen 14 targets

The paper reports 46 of 215 frontier coordinates at 12:05 UTC on 26 October 2026.
The reconstruction catalog retains the 14 Froghop recipes from the 25 October, 15:46 UTC snapshot.
The current frontier analysis and this fixed reconstruction set have separate scopes.
The target catalog pins its parent source, exact edit, notices, nonce settings, and expected operation hash.
Reproduction reconstructs known recipes.
It measures neither model rediscovery nor model advantage.

```sh
./lco reproduce --family froghop --targets all \
  --config configs/offline.json --offline-build \
  --artifact-root work/shared-artifacts \
  --out work/reproduce-offline

./lco reproduce --family froghop --targets all \
  --config configs/claude.json --offline-build \
  --artifact-root work/shared-artifacts \
  --out work/reproduce-claude
```

The two workflows can share verified construction artifacts.
Receipts mark every cache hit.
The cache checks source, binary, recipe, and operation hashes before reuse.
Each workflow records its proposals and its independent checker results.
Large Froghop emission can require several minutes and about 12 GB of memory.
Run these emissions in sequence on a 24 GB machine.

## Create an editable family

Start from a pinned source-backed template:

```sh
./lco family init --from-target ap1270 --id my-family \
  --out work/my-family
```

Edit the Rust constructor under `work/my-family/src/point_add/`.
Keep the inherited notices and parent declarations.
Freeze the deliberate edits, then register the family:

```sh
./lco family refresh work/my-family/family.json
./lco family register work/my-family/family.json \
  --registry work/families
./lco search --family work/families/my-family.json --targets all \
  --config configs/offline.json --trials 4 \
  --out work/my-family-run
```

The editable constructor exposes `pub fn build() -> Vec<Op>`.
The source adapter copies only declared editable paths into the fixed build harness.
The independent checker stays fixed.
Shared rewrites need real bindings at both circuit sizes.
Full-width settings can omit the fast binding when the rule explains why.
Users can also register a named subset of an existing backend's pinned recipes.

## Reliability and approximation

The failure limit is probability 0.001, or 0.1%.
Every input uses two measurement streams.
An input fails when either stream fails.
Qualification compares the binomial upper bound with the exact limit.
The number of tested inputs, confidence allowance, and observed failures stay in every audit.
An observed failure can still meet the declared probability bound.

The frozen recipes preserve inherited approximation settings and nonces.
Reproduction performs no nonce search.
The paper separates inherited approximation, our approximation studies, and fresh reliability measurements.
Finite tests establish evidence under the declared protocol.

## Inspect, resume, and export

Each run contains `contract.json`, `grammar.json`, per-trial receipts, and `results.json`.
The source contract freezes the grammar, model, targets, tools, inputs, and cutoff.
Re-run the same command to resume an unchanged run.
A changed source or protocol requires a new run directory.
Resume preserves the original finalists, input labels, and confidence allowances, including failed audits.
It checks completed audit receipts and every saved verdict before reuse.
An incomplete audit blocks resume until its evidence is recovered and checked.
The runner preserves consumed inputs and does not repeat an interrupted check automatically.

```sh
./lco export --run work/offline --slot 0 \
  --out work/submission
```

An export contains the checked source, a submission note, and dependency declarations.
It also records hashes for the circuit and the audit evidence.
Export requires a passing fresh final audit under the frozen selection and protocol.
Its reported mean Toffoli count comes from that final audit.
Use an ECDSA.fail benchmark checkout to submit the exported editable paths.
Use `./lco results --run work/offline` to choose a retained Q/T trade-off.
The export instructions retain the original benchmark harness.

```sh
./lco submit --export work/submission --benchmark /path/to/benchmark
./lco submit --export work/submission --benchmark /path/to/benchmark --execute
```

The first command shows the plan.
The execute command uses a separate benchmark worktree, runs the official local check, and submits its score.
The original benchmark worktree keeps its current files.

## Frontier campaigns

The frontier runner syncs the public curve and builds jobs from counted source edits and copied-file hashes.
It supports pool settings, carry lanes, compiler flags, and the exact local catalog.
Source discovery writes typed setting rules with pinned evidence.
The runner checks those rules before it materializes a candidate.

Each job retains stage receipts, one public screen, a reserved audit label, and the source credits.
It checks the live curve before submission.
The [frontier examples](../examples/frontier/README.md) include cluster commands and Q818/Q819 replay jobs.
The [campaign guide](../docs/FRONTIER-CAMPAIGN.md) describes restart behavior and the shared registry.
The [implementation record](../docs/FRONTIER-STATUS.md) separates completed checks from the pending cluster replay.

