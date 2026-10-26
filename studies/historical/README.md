# Historical ablation source and records

This bundle retains the code and evidence behind the paper's grammar and model
ablations. These studies use 16-bit modular division. The current full-point-addition
harness has separate evidence under `evidence/native-validation/`.

The `grammar/source/` and `model-pilot/source/` trees retain their executable source bytes.
Some evidence JSON files redact private path text. Each study retains its drivers,
contracts, hash sidecars, and measured results.
`manifest.json` hashes every shareable file, archive, and archive member.
Changed entries retain their original hashes. The [redaction manifest](../../docs/sharing-redactions.json)
records the changed copies. Frozen contract hashes still identify the original experiment bytes.
`NOTICE.md` records the source notice status.

| Archive | Contents |
|---|---|
| `archives/grammar-searches.tar.gz` | All 15 frozen grammar search records |
| `archives/grammar-run-records.tar.gz` | Study configuration, environment, costs, and rules |
| `archives/model-pilot-run-records.tar.gz` | Study configurations, rules, policies, search traces, model packets and receipts, and final manifests |

Archive member paths start with `grammar/` or `model-pilot/`. Extract archives
into a separate working directory. Generated circuits and intermediate evaluation
caches stay outside this bundle. Recipes and hashes support their reconstruction.
The saved model replies support inspection without another provider call.

The model source includes the original `pyproject.toml` and
`requirements-study.lock`. The recorded execution environment differs from that lock.
The manifest preserves both declarations. Grammar has no frozen dependency lock;
its saved `environment.json` records the measured package versions.

`path-map.json` identifies retained source roles and the exact calibration input.
Private locations use placeholders. Historical scripts retain their original directory
assumptions. Their execution after relocation remains unverified.
Their verification and report commands can write files. A future wrapper must keep
the frozen files unchanged and declare its own identity.

No new model call, circuit experiment, or audit forms part of this retention.
