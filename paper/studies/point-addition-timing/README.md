# Full point-addition timing

The paper compares full 16-bit point addition with full secp256k1 point addition.
Each row measures 100,000 input pairs and two measurement streams per pair.
The rows use separate fixed runs on the same shared Apple M2 workstation.
The workstation has eight cores and 24 GiB of memory.
These costs depend on the circuit, checker, thread count, and machine load.

## Full 16-bit point addition

`small16/contract.json` fixes the policy, circuit hash, tools, input label, and sample count before the run.
The circuit uses the library's Kaliski constructor and guarded `mfsur` compiler policy.
Its source revision is `49bdddbd3b5486739ab4e0e549bf92285c34fc2c`.
It uses 214 qubits and 28,051 mean Toffolis.
The checker uses one thread.

Run `run_small.py --tools PATH_TO_BINARIES --revision COMMIT --out EMPTY_DIRECTORY` to repeat the measurement.
Each call reserves a fresh input label. The script refuses to overwrite a run.
The binaries are `small-emit` and `circuit-check`, built by the library's `native/build.sh`.

The emission receipt includes compiler passes, hashing, and compressed circuit output.
The evaluation receipt includes loading, input preparation, simulation, checks, and per-input output.
CPU time measures the checker process. Post-run compression is outside both evaluation times.
Installation and compilation are outside the measurement.

The single-circuit timing run uses alpha=0.05 and observes zero failures.
Its upper bound is 0.0000299568740.
The separate four-policy qualification study retains its original alpha=0.05/4 allocation.

## Full secp256k1 point addition

The row reuses `../timing/point_addition/result.json`, including its original timing inputs and four-thread checker.
The fixed circuit uses 1,270 qubits and has operation hash `9271a9aa2b368b88989e905a932d5469baece5f3af00c0e928e8dcb7b9ce3c01`.
It observes 26 failed inputs and 793,801.02822 mean Toffolis.
Its original three-circuit study uses alpha=0.05/3. This table does not change that allocation.
Its source, generator, checker, inputs, and timings remain in the original study records.

`../../data/point-addition-timing.json` links both result receipts.
The division rows remain in the original archive and do not appear in the paper's timing table.
