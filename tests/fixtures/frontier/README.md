# Frozen campaign data

These files come from campaign commit `da182c4`, under `runs/pareto5-20261025/cluster-handoff`.
`provenance.json` records the original file hashes.

- `curve-20261025T2201Z.json` contains 142 frontier coordinates, including 15 assigned to SnehalRaj.
- `jobs-20261025T2201Z.json` contains 37 pool and lane jobs: 33 for the cluster and four for the Mac.

The handoff prose says 32 cluster jobs. The retained job records contain 33.
The tests use the records and preserve this difference.
These are existing `pareto5` candidates. They do not demonstrate discovery by the library.
The generator adds historical labels and blocks execution until the earlier screen records are reconciled.
Loading older exports applies the same block by source recipe, even if a job was renamed.

`lilypad-sweep-20261025T2201Z.json` runs the unchanged `make_lilypad_sweep.py` on the same 22:01 curve.
It contains 111 gated width candidates.
The handoff's stored Lilypad list instead uses the 23:01 curve.
The fixture changes only the input path to a portable relative label.

The curve fixture does not contain the full public response.
Tests reconstruct its coordinates from explicitly synthetic accepted rows and test eligibility separately.
They do not claim to revalidate all submissions from the 22:01 response.

The generator's expected Toffoli counts are inferences from fixed historical source trees.
A changed frontier changes the must-beat threshold without changing those source estimates.
Fresh measurements replace estimates only through a new source record.

The Lilypad edit strings describe a constant instead of giving exact Rust text.
Job normalization therefore records a blocked setting request.
An executable recipe needs the exact source line and must resolve the retained reliability gate.
