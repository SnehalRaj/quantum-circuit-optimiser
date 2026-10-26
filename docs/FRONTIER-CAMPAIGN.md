# Campaign execution and receipts

A campaign freezes each job before execution. The job contains its source commit, ordered edits, copied-file hashes, dependencies, and owner.
`Campaign.run_job` resumes from checked stage receipts. It rejects a changed job, receipt, or output artifact.

The stage order is:

```text
materialize → build → emit → static gate → develop → screen → audit → export → send → record
```

The default final stage is `export`. Sending requires `allow_send=True`.
Unresolved setting requests stop before materialization.
A failed gate, development check, screen, or audit stops the job.

## Run a campaign

```python
from llm_circuit_optimiser.frontier.campaign import Campaign
from llm_circuit_optimiser.frontier.runtime import RuntimeAdapter

adapter = RuntimeAdapter({
    "git_repo": "/data/public-source",
    "benchmark_repo": "/data/public-source",
    "rust_version": "1.93.0",
    "username": "your-leaderboard-name",
    "credits": ["Name the authors and the source work used here."],
    "inherited_contract_file": "/data/parent-contract.md",
})
campaign = Campaign("work/campaign", adapter, owner="cluster")
result = campaign.run_job(job, through="export")
```

The source repository must contain each referenced commit before execution.
`benchmark_repo` is the Git repository used to create the detached submission checkout.
The checkout uses the fixed benchmark manifest's commit. `benchmark_commit` can select another pinned commit with the same benchmark bytes.
The fixed benchmark files in that checkout must match the files used during compilation.
The `cargo` path can name a toolchain binary or a Rustup proxy.
The runner keeps the proxy filename so Rustup dispatches to Cargo.
After changing a frozen configuration or implementation, start a fresh output directory.

The adapter also accepts these settings:

| Setting | Meaning |
|---|---|
| `base_dir`, `files_root`, `benchmark_dir` | Explicit source directories accepted by the materializer |
| `cargo`, `cargo_home`, `rustup_home`, `target_dir` | Compiler and build paths |
| `offline`, `build_jobs` | Cargo network mode and compilation parallelism |
| `checker` | Object with `path`, `sha256`, `kind`, and optional `threads` |
| `parent_ops` | Existing parent operations, with `path` and `sha256` |
| `ecdsafail` | CLI executable path; the default is `ecdsafail` |
| `grammar_files` | Grammar documents needed by the job's typed actions |
| `public_snapshot` | Complete saved public list used to resolve dependency prefixes at export |
| `model`, `harness` | Actual proposal mode and harness name for the public note |
| `poll_seconds`, `poll_timeout`, `command_timeout` | Poll interval, polling limit, and optional command limit, in seconds |

The checker kind is `native` or `study`. The default builds the repository's native checker.
The runtime freezes its configuration and the admission, receipt, and checker implementations before the first stage.
Resume checks this record even when every requested stage has already completed.
Typed jobs bind their actions to pinned base files before materialization.
The runtime checks each rule and grammar reference, then records the exact source edits.
Manual counted jobs retain a separate scope statement in the binding receipt.

Emission uses a host lock. Set `LCO_PARALLEL_EMISSIONS=1` only when the host can hold concurrent gate streams.
`run_jobs(jobs, parallel=N)` runs independent jobs and retains each job's durable state.
All jobs that share a registry also share the submission lock.

The catalog runs after the compiler's `POSTPASS` count.
The receipt retains that count, the removed native Toffoli count, and the legacy unit-saving estimate.
The unit-saving estimate assumes each removed gate executes once. Conditional gates can invalidate this assumption.
When the final expected count is unknown, the static gate continues to measured validation.
An emitter that reports no expected count remains unsupported and stops before validation.

## Adapter interface

Custom instruments implement `run_stage(stage, job, job_dir, receipts, context)`.
An optional `preflight(job, job_dir)` checks configuration before the controller reads cached results.
`receipts` maps completed stage names to result objects.
`context` contains the frozen job hash, campaign contract, region, and disclosure records.
For a screen it also contains `screen_claim`. For an audit it contains `audit_slot`.

Each result has `artifacts`, a list of `{path, sha256}` objects.
Use an empty list for stages without file outputs.
Relative artifact paths refer to the job directory. Receipts store their absolute locations and verify their bytes on resume.

| Stage | Required result fields |
|---|---|
| emit | `operation_sha256` |
| static_gate, develop | Boolean `passed` |
| screen | `operation_sha256`, `evaluator_sha256`, `shots=9024`, `passed`, and `counts` |
| audit | `operation_sha256`, `pairs`, `streams`, `alpha`, `label`, `failed_inputs`, `upper_bound`, `qualified` |
| send | `submitted=true` and `submission_id`, or `submitted=false` and `refusal` |
| record | Boolean `terminal` |

Screen counts are `classical_mismatches`, `phase_garbage_batches`, and `ancilla_garbage_batches`.
All three must be zero for a passing screen.
The note renderer also requires the screen's measured integer `Q` and `T`.

The audit must use its reserved 20,480 input pairs, two streams, label, and alpha of 0.05/32.
The controller recomputes the exact one-sided binomial upper bound from the recorded failed-input count.
The runtime checks every saved verdict and recounts failed inputs before audit qualification.
Qualification requires an upper bound at most 0.001.

A known submission refusal contains `reason` and Boolean `retryable`.
A retryable refusal leaves the job waiting. The next explicit run can retry the send stage.
An unknown network outcome leaves the attempt unresolved and blocks another send.
A pending service result leaves `record` waiting. Polling that stage never repeats submission.

## Claims and interruption

All workers share one `Registry` database. Its screen key is the operation SHA-256.
A new database contains no earlier `pareto5` screens.
The fixed handoff generator therefore emits historical jobs with execution blocked.
Reconcile the earlier receipts before any search screen. The CLI does not yet import them automatically.
A worker claims the hash before invoking the public evaluator.
A completed result can be reused across jobs when its evaluator hash matches the current build.
An unfinished or interrupted claim prevents another screen of those bytes.
The registry retains failed and interrupted attempts for disclosure.

Each audit family has 32 slots and total alpha 0.05.
The registry reserves a slot and fresh random label before the audit begins.
An interrupted audit keeps its slot. It cannot restart with a new favourable label.
The controller does not open a fresh family when the existing family is full.

Each job uses an operating-system file lock. A lease record identifies the owner, host, and process.
The operating system releases the lock when a process exits.
The immutable attempt record remains, so a new worker can distinguish safe local work from an uncertain external action.

If a process exits after the result commits but before the stage receipt commits, the controller recovers the completed result.
It does not execute the screen, audit, or send again.
An unknown outcome needs explicit reconciliation with the original instrument or service evidence.

SQLite transactions use `BEGIN IMMEDIATE`, a busy timeout, and full synchronization.
The database uses the default rollback journal. It makes no WAL assumptions about shared filesystems.
Use one campaign coordinator, or a shared filesystem with reliable SQLite and POSIX file locking.
Separate registry files cannot prevent duplicates across machines.

## Public notes

`notes.render_note` reads the source-edit receipts, operation hash, screen counts, audit result, and regional disclosure records.
`dependencies_for_submission` keeps dependency roles in the rationale and preserves their evidence.
The note retains the supplied author credits and inherited contract verbatim.
Unknown counts stay unresolved; the renderer does not invent measurements.

`validate_note_size` checks the 5 KiB minimum without padding the note.
If the note is too short, add the missing source, reproduction, or measurement details.
Paired agreement remains finite validation evidence. A general exactness claim needs the rule's separate proof or exhaustive local certificate.

The runtime adapter checks source files, operation bytes, and both compiled binaries before validation and submission.
Before submission, it polls the account and checks the live frontier.
It copies the recorded point-addition source into a detached Git worktree and checks the resulting files.
Submission consumes the recorded screen receipt. It calls `ecdsafail submit` without another evaluation.
The adapter resolves dependency prefixes to unique public UUIDs before submission and records the mapping.
A missing or ambiguous dependency blocks submission.

Run the isolated tests with:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_frontier_campaign.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -p test_frontier_runtime.py -v
```
