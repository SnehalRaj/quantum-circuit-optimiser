# Fixed-circuit timing study

The study requests 100,000 independent inputs for each of three recorded circuits.
Each input has two measurement streams. The protocol fixes all labels before the runs.

`protocol.json` pins the circuits, Python sources, native binaries, batch sizes, and confidence allocation.
`protocol.json.sha256` binds that file. `hardware.json` records the Apple M2 workstation.
The workstation has eight CPU cores and 24 GiB of memory. Other applications share the machine.

The requests run sequentially. Division uses one process. Native point addition uses four worker threads.
The Python batches contain 16,384 input pairs. Native tasks contain up to 64 inputs from one stream.
`batch-units.json` states these units.

## Timing scope

The division construction measurement emits and hashes the complete operation stream without simulation.
The evaluation calls the unchanged `circuitopt.division.division_batch` function from the frozen source copy.
That function combines emission, hashing, simulation, and output checks.
Its timing must retain that combined scope. Subtracting the separate construction time would create an invalid simulation estimate.
Input preparation and outcome archival have separate wall times.

The point-addition measurement uses the original frozen generator and checker binaries.
The checker reports operation loading, input preparation, and simulation wall times separately.
Its process receipt also reports total wall and CPU times.
Fresh resource means belong to the timing packet. Published challenge counts retain their original packet.

## Records

Each circuit folder contains the start record, construction record, and final result.
The division folders save every input, measurement seed, and stream verdict in compressed JSON lines.
The point-addition folder saves the operation bytes, native outputs, and every per-input outcome.
`events.jsonl` is an append-only progress log.
The scripts preserve failures and refuse to overwrite a circuit folder.

`aggregate_timing.py` builds `../../data/timing-study.json` from the recorded results.
`verify_timing.py` independently recounts the saved outcomes and checks the protocol bindings.
It does not execute the circuits again.

The fixed criterion is a failure probability of 0.001 per input.
The three-circuit family allocates alpha = 0.05/3 to each one-sided exact binomial bound.
These finite tests cover the recorded input distributions and two measurement streams.

No model call, public submission, or production source change forms part of this study.
