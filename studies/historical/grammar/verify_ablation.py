#!/usr/bin/env python3
"""Independently check the recorded comparison and its confidence bounds."""
from pathlib import Path
import hashlib
import json
import math
import statistics
import sys

from scipy.stats import binom

HERE = Path(__file__).resolve().parent
PAPER = HERE.parents[1]


def read(path):
    return json.loads(Path(path).read_text())


def canonical(x):
    return json.dumps(x,sort_keys=True,separators=(",",":"))


def digest(x):
    return hashlib.sha256(canonical(x).encode()).hexdigest()


def bound(k,n,alpha):
    if k == n:
        return 1.
    low, high = k/n, 1.
    for _ in range(70):
        mid = (low+high)/2
        if binom.cdf(k,n,mid) > alpha:
            low = mid
        else:
            high = mid
    return (low+high)/2


def main():
    contract = read(HERE / "contract.json")
    assert digest(contract) == (HERE / "contract.sha256").read_text().strip()
    assert hashlib.sha256((HERE / "run_ablation.py").read_bytes()).hexdigest() == contract["runner_sha256"]
    for name, expected in contract["source_sha256"].items():
        assert hashlib.sha256((HERE / "source/src/circuitopt" / name).read_bytes()).hexdigest() == expected
    result = read(PAPER / "data/grammar-ablation.json")
    assert result["status"] == "complete"
    assert result["contract_sha256"] == digest(contract)
    manifest = read(HERE / "finalists.json")
    assert digest(manifest) == (HERE / "finalists.sha256").read_text().strip()
    receipts = read(HERE / "audit-receipts.json")
    assert set(receipts) == set(manifest["recipes"])
    alpha = .05 / len(receipts)
    for cid, r in receipts.items():
        assert cid == digest(r["recipe"])
        assert r["pairs"] == 16384 and r["streams"] == 2
        assert r["S"] == r["Q"] * math.floor(r["T"] + .5)
        assert abs(bound(r["failed_inputs"],16384,alpha)-r["upper"]) < 1e-11
        assert r["eligible"] == (r["upper"] <= .001)
    assert len({r["input_sha256"] for r in receipts.values()}) == 1
    slots = 0
    runs = {}
    for arm in contract["arms"]:
        for seed in contract["seeds"]:
            run = read(HERE / "searches" / f"{arm}-{seed}.json")
            runs[arm,seed] = run
            assert run["status"] == "complete"
            assert set(run["baselines"]) == set(contract["weights"])
            assert len(run["trials"]) == 180
            for baseline in contract["weights"]:
                assert [r["trial"] for r in run["trials"] if r["baseline"] == baseline] == list(range(1,61))
            for row in run["trials"]:
                if "recipe" in row:
                    p = row["recipe"].get("block_program")
                    assert p is None or p in contract["programs"][arm]
            slots += len(run["trials"])
    assert slots == 2700
    assert len(result["rows"]) == 45
    for arm_summary in result["arms"]:
        arm = arm_summary["arm"]
        costs = []
        for seed in contract["seeds"]:
            rows = [r for r in result["rows"] if r["arm"] == arm and r["seed"] == seed]
            assert len(rows) == 3
            value = 0.
            for row in rows:
                before,after = receipts[row["starting"]],receipts[row["selected"]]
                valid = before["eligible"] and after["eligible"]
                expected = min(before["S"],after["S"]) if valid else before["S"]
                assert expected == row["retained_S"]
                value += contract["weights"][row["baseline"]] * expected / before["S"]
            costs.append(value / sum(contract["weights"].values()))
        assert abs(statistics.median(costs)-arm_summary["median_remaining"]) < 1e-12
    report = {"status":"passed","proposal_slots":slots,"endpoint_rows":len(result["rows"]),
              "audit_bounds_recomputed_by_binomial_cdf":len(receipts),
              "source_files_verified":len(contract["source_sha256"]),
              "contract_sha256":digest(contract),"all_audit_inputs_match":True,
              "scope":"Recorded circuit samples and comparison arithmetic; no new circuit executions"}
    (HERE / "verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
