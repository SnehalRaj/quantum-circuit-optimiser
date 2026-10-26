# Relocated checkout validation

These checks use a source archive of commit `0f4b277182df701a22b6454f6a65b26c2bcdfe16` in a separate directory.
The checkout has no Git history or symlinked data.
The reconstruction uses only the archived circuit sources and data.
Python runs in a fresh virtual environment with no installed third-party packages.
Rust 1.93.0 uses an existing crate cache. The tests compile fresh binaries from the archived source.
The provider CLIs use the tester's own authenticated accounts.

The model-free, Claude, and Codex modes reconstruct the known AP1270 recipe.
Each mode checks 4,096 full 16-bit input pairs, then 32,768 secp256k1 input pairs.
Each mode also audits the selected circuit on 32,768 fresh input pairs.
Every input uses two measurement streams. Either stream can fail the input.
All three modes meet the probability limit of 0.001.
The output hash matches the recorded AP1270 circuit.

| Mode | Model calls | Large check failures | Fresh audit failures |
|---|---:|---:|---:|
| Model-free | 0 | 4 / 32,768 | 6 / 32,768 |
| Claude | 1 | 7 / 32,768 | 6 / 32,768 |
| Codex | 1 | 10 / 32,768 | 12 / 32,768 |

Claude reports `claude-opus-5-5`. Codex accepts `gpt-6.1-sol` and leaves the resolved model identity unknown.
Each model receives the one-recipe reproduction menu. These checks establish execution through each provider.
They do not measure search quality or compare model performance.
The modes use different input packets and share the freshly built, hash-checked operation artifact.

The first attempt waited for an existing large emission.
The controller stopped before its own emitter started, then resumed the same frozen contracts.
A separate lock allowed the small AP constructor to run after a host memory check.
The emission used 1,434,845,184 peak RSS bytes and took 38.59 seconds on the shared host.
The resume reused both successful model recommendations. It made no extra model call.
The original interrupted records remain in local work storage.

The Python suite passed 208 tests. The Rust suite passed 26 tests.
The final source archive also includes the sharing tool's separate regression tests.
The large 14-recipe Froghop reconstruction has its own completion record.

## Reproduce

Run each mode from a source checkout with the documented Rust environment:

```sh
./lco reproduce --family qualified-leapfrog --targets ap1270 \
  --config configs/offline.json --small-pairs 4096 --large-pairs 32768 \
  --audit-pairs 32768 --artifact-root work/release-artifacts --out work/release-offline
```

Repeat with `configs/claude.json` and `configs/codex.json`, using separate output directories.
The measured runs also used `--offline-build` with a populated crate cache.
Fresh runs produce their own input labels and counts.

## Check the saved evidence

```sh
python3 evidence/release-20261026/verify.py
```

The verifier recounts every input/stream pair and recomputes each exact one-sided binomial bound.
It also checks the execution-source hashes against this checkout.
The compressed records project each original verdict to seven columns:
input index, stream, pass, coordinate failure, phase failure, workspace failure, and addend failure.
`results.json` retains the original verdict hashes, packet hashes, input labels, and checker identity.
The full input and output coordinates remain in local work storage; the fixed checker can regenerate the input packets from their labels.
The projections verify counts and bounds. They do not rerun the circuit or independently establish its output coordinates.
