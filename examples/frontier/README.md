# Frontier jobs

Run these commands from the repository root. The runner uses no model.
The existing `lco search` command keeps the 16-bit stage and the model-assisted controls.

Copy `runtime.example.json` to an untracked path under `work/`.
Set the source cache, compiler paths, account name, credits, and inherited contract.
The example retains this campaign's account, source credits, and lineage contract.
Change these fields when using a different parent or account.
Use Rust 1.93.0. Each source commit must exist in the cache before execution.
The [campaign guide](../../docs/FRONTIER-CAMPAIGN.md) explains each configuration field.

## Inspect the historical handoff

```sh
./lco public sync --out work/public.json
./lco frontier jobs work/public.json --family froghop --out work/historical-jobs.json
```

The Froghop generator retains the handoff's three source trees and cost estimates.
It updates each job's public comparison from the saved curve.
The handoff contains 33 cluster jobs and four Mac jobs.
Its prose count of 32 cluster jobs was incorrect.
These candidates came from `pareto5`; the library did not discover them.
The generator marks them as historical and blocks execution pending reconciliation of the earlier screen records.
The loader also recognizes earlier exports by their source recipes, including renamed jobs.
An estimated resource improvement, recorded as `live`, gives no evidence that a candidate is unscreened.
Keep the original failed and passed screens. Do not screen these candidates again as search trials.

The Lilypad generator keeps unresolved source edits and reliability limits as blocked requests.
It cannot execute those jobs until their requirements are resolved.

Fetch each base commit and copied-file commit into your source cache.
Use Git fetch for this step. The ECDSA.fail clone command also runs an evaluation.
For example:

```sh
git init work/public-source
git -C work/public-source fetch --depth=1 \
  https://github.com/Layr-Labs/ecdsafail-challenge.git \
  4073f09cfa7fe733fdeb64841193282aaff293d9 \
  2cca36f958f6e5a818e9f693a2f95f7a2e3ece25 \
  8233380fbf34f30bee75dae47a567720ba4db97a \
  6b0327d60e40068fcbed5fedcb7f66d926f12320
```

The first three commits are the Froghop bases. The last pins the fixed benchmark for submission checkouts.

## Run reconciled candidates

A fresh registry has no knowledge of screens run by the earlier pipeline.
The CLI does not yet import that history automatically.
Check proposed source edits and operation hashes against the existing campaign records before any screen.
Use `new-jobs.json` below only for candidates whose prior screen status has been resolved.

```sh
./lco frontier run work/new-jobs.json --config work/frontier.json \
  --owner cluster --out work/campaign --registry work/campaign-registry.sqlite \
  --parallel 1 --through export
./lco frontier status work/campaign
```

Use one registry for all workers in an active campaign.
Increase `--parallel` when the host has enough memory.
Set `LCO_PARALLEL_EMISSIONS=1` to permit concurrent emissions on that host.
The runner locks emissions by default.

To submit qualified exports, repeat the same command with `--through record --execute-submit`.
The runner checks the live curve and waits for earlier account submissions.
It uses the existing screen receipt without another public evaluation.
A waiting or failed job returns a nonzero exit code and retains its receipts.

## Discover settings

Fetch the curve rows' source commits into the same cache, then run:

```sh
./lco public trees work/public.json --git-repo work/public-source \
  --out work/census --owner cluster
```

The census writes `trees.json`, `jobs.json`, and typed rule files under `families/`.
Its proposals can overlap earlier campaign jobs. Source discovery alone does not establish that a circuit is unscreened.
It ranks parent evidence separately from the proposed children's unmeasured reliability.
Public note rates remain labelled estimates. Fresh audit feasibility stays unknown.
Pass those rule files with repeated `--grammar` flags when running the discovered jobs.
The runtime checks their hashes and expands each action into counted edits.
Settings with no 16-bit equivalent record that limit and run at full width.

## Reproduce Q818 and Q819

`replay-jobs.json` reconstructs the recorded catalog submissions from their public parent.
It freezes their expected operation hashes. Use these jobs only for reproduction.
Keep the reproduction records separate from the active search campaign.

Fetch these two commits into the source cache:

- `b618b19cf76f0ffe3829b53a8af6653c95dc7e4e`: the public parent.
- `4073f09cfa7fe733fdeb64841193282aaff293d9`: the catalog file source.

```sh
./lco frontier run examples/frontier/replay-jobs.json \
  --config work/frontier.json --owner replay --out work/replay \
  --registry work/replay-registry.sqlite --through export
./lco frontier compare-replay work/replay --expected evidence/frontier/replay
```

Do not submit these historical jobs. The comparator checks operation hashes, screen counts, note sections, and source roles.
The local source check found only the historical editor's CRLF-to-LF changes.
Fresh emission and evaluator comparison remain part of the cluster run.

The small-stage catalog check found no matching windows in the tested `mfsur` circuit.
The local certificate tests exercise actual rewrites and check value and phase.
The full 16-bit paired check therefore establishes no catalog transfer gain.
