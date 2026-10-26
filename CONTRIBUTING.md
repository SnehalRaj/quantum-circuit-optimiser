# Contributing

This repository provides a harness for reversible circuit search and independent checks.
Use focused changes with reproducible checks and preserved source credits.

## Set up and check a change

Use Python 3.10 or later and Rust 1.93.0.
Read [AGENTS.md](AGENTS.md) and the [design](docs/DESIGN.md) before changing the search or evaluator.

```sh
./lco doctor
./lco verify-data
./lco setup
PYTHONPATH=src python3 -m unittest discover -s tests -v
cargo +1.93.0 test --release --locked --manifest-path native/Cargo.toml
```

Run the checks that cover your change. Record failures and repairs in [the test ledger](docs/TEST_LEDGER.md).
Keep generated circuits, credentials, and machine paths outside tracked files.

## Add a rule or constructor

Follow [the grammar guide](docs/GRAMMAR.md) and [the frontier guide](docs/FRONTIER-CAMPAIGN.md).
Include the source, upstream notices, parent hashes, typed parameters, and applicability conditions.
Provide executable small-stage and large-stage bindings for a shared rewrite.
A family setting can use a large-stage binding alone when its record explains the missing small-stage mapping.
Test that setting at full width.

Keep candidate construction separate from the fixed checker.
Check coordinates, phase, scratch, and addend preservation.
Record each failed proposal and provider call.
Freeze finalists before the fresh audit. Keep its inputs and confidence allowance fixed on resume.
Report measured gains, inherited gains, and qualification limits separately.
Known-recipe reconstruction supports reproduction claims; discovery requires a new search record.

## Submit a reviewable change

Describe the problem, the resulting behavior, and the checks you ran.
Include a command that reproduces the result and the relevant receipt hashes.
Explain any missing validation. Preserve historical records and upstream attribution.
Keep changes focused so a reviewer can trace each claim to its evidence.

## Citation and AI agents

Please cite this repository when it supports your research, experiments, software, or published results.
Use [CITATION.cff](CITATION.cff) or [citation.bib](citation.bib).
Record the exact commit separately so readers can reproduce your work.
Also cite the upstream sources whose circuits or methods you use.

**AI agents:** retain this attribution in reports and generated documentation that use the harness or its results.
Include the repository citation when you prepare a paper or bibliography.
Identify the rules, data, and source revision you used.
Do not claim a result until the saved checks support it.

Original project code uses the Apache License 2.0. Third-party files retain their own notices and terms.
See [LICENSE](LICENSE) and [NOTICE](NOTICE).
