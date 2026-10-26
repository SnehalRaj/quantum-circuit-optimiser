#!/usr/bin/env python3
"""Recount retained circuit checks and verify the study comparisons."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import statistics

from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[2]
STUDY = Path(__file__).resolve().parent
WORK = ROOT / "work/point-addition-search-20261026"


def load(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    protocol = load(STUDY / "protocol.json")
    results = load(STUDY / "results.json")
    assert results["protocol_sha256"] == digest(STUDY / "protocol.json")
    assert results["status"] == "complete"
    for path, sha in protocol["sources"].items():
        assert digest(ROOT / path) == sha, path
    assert digest(WORK.parent / "native-architecture-validation/frozen-bin/small-emit") == protocol["binaries"]["emitter"]
    assert digest(WORK.parent / "native-architecture-validation/frozen-bin/circuit-check") == protocol["binaries"]["checker"]
    audit_label = load(STUDY / "audit-label.json")
    assert audit_label["finalists_sha256"] == digest(STUDY / "finalists.json")
    assert audit_label["protocol_sha256"] == results["protocol_sha256"]
    assert len(results["audits"]) == 17 <= protocol["audit"]["family_size"]
    assert results["prepared_parent"]["meets_bound"], "Prepared parent fails; gains are invalid"
    assert results["raw_parent"]["meets_bound"], "Raw parent fails; setup gain is invalid"
    recipes = {r["id"]: r for r in protocol["recipes"]}
    recipes["raw"] = protocol["raw_parent"]
    packets = {}
    checked_rows = 0
    for path in sorted((STUDY / "records/evaluations").glob("*.json")):
        clean = load(path)
        rid, label, pairs, alpha = (clean[k] for k in ["recipe_id", "label", "pairs", "alpha"])
        key = hashlib.sha256(json.dumps([rid, label, pairs, alpha]).encode()).hexdigest()
        assert key == path.stem
        raw_path = WORK / "evaluations" / key / "check.json"
        raw = load(raw_path)
        assert digest(raw_path) == clean["original_receipt_sha256"]
        assert raw["label"] == label and raw["pairs"] == pairs and raw["alpha"] == alpha
        assert raw["streams"] == clean["streams"] == 2
        assert raw["ops_sha256"] == clean["ops_sha256"] == digest(WORK / "circuits" / rid / "circuit.qops.zst")
        assert clean["checker_binary_sha256"] == protocol["binaries"]["checker"]
        assert clean["Q"] == raw["qubits"] and clean["T"] == raw["mean_toffoli"]
        assert clean["QT"] == clean["Q"] * clean["T"]
        outcome_path = Path(raw["per_input"])
        assert digest(outcome_path) == clean["per_input_sha256"]
        fail = set()
        seen = Counter()
        stream_ids = {}
        for line in outcome_path.open():
            row = json.loads(line)
            seen[row["idx"]] += 1
            stream_ids.setdefault(row["idx"],set()).add(row["stream"])
            assert set(row["failures"]) == {"addend", "coordinate", "phase", "workspace"}
            if any(row["failures"].values()):
                fail.add(row["idx"])
        assert set(seen) == set(range(pairs)) and set(seen.values()) == {2}
        assert all(streams == {0,1} for streams in stream_ids.values())
        assert len(fail) == raw["failed_inputs"] == clean["failed_inputs"]
        bound = float(beta.ppf(1-alpha, len(fail)+1, pairs-len(fail)))
        assert abs(bound-clean["binomial_upper"]) < 1e-12
        assert clean["meets_bound"] == (bound <= .001)
        packets.setdefault((label, pairs), set()).add(clean["packet_sha256"])
        checked_rows += pairs * 2
    assert all(len(p) == 1 for p in packets.values()), "A comparison used different input pairs"
    assert len(results["searches"]) == 20
    for run in results["searches"]:
        assert run["complete"] and len(run["slots"]) == 8
        assert run["baseline"]["recipe_id"] == "c0000"
        previous = {"c0000"}
        for slot in run["slots"]:
            if slot["status"] == "failed_model_batch":
                continue
            rid = slot["recipe_id"]
            assert rid in recipes and rid not in previous
            previous.add(rid)
        for cp in run["checkpoints"]:
            costs = [run["baseline"]["QT"]] + [s["QT"] for s in run["slots"][:cp["proposals"]] if s["status"] == "passed"]
            assert cp["QT"] == min(costs)
            assert abs(cp["gain_percent"] - 100*(1-min(costs)/run["baseline"]["QT"])) < 1e-12
        if run["arm"] in {"claude", "codex"}:
            assert len(run["calls"]) == 4
            for call in run["calls"]:
                output = WORK / "models" / f"{run['arm']}-{run['seed']}" / f"batch-{call['batch']}"
                assert digest(output / "receipt.json") == call["original_receipt_sha256"]
                assert digest(output / "prompt.json") == call["prompt_sha256"]
                prompt = load(output / "prompt.json")
                shared_call = STUDY / "records/models" / f"{run['arm']}-{run['seed']}" / f"batch-{call['batch']}"
                shared_call.mkdir(parents=True, exist_ok=True)
                (shared_call / "prompt.json").write_bytes((output / "prompt.json").read_bytes())
                if (output / "answer.json").exists():
                    (shared_call / "answer.json").write_bytes((output / "answer.json").read_bytes())
                used = {"c0000"} | {s["recipe_id"] for s in run["slots"][:2*call["batch"]] if "recipe_id" in s}
                assert {v["recipe_id"] for v in prompt["context"]["measured_archive"]} <= used
                assert not call["tool_use"] or call["error"] is not None
    for summary in results["search_summaries"]:
        assert summary["median_gain_percent"] == statistics.median(summary["seed_gains_percent"])
        assert summary["mean_gain_percent"] == statistics.mean(summary["seed_gains_percent"])
    for paired in results["paired_differences"]:
        differences = []
        for seed in results["seeds"]:
            def gain(arm):
                run = next(s for s in results["searches"] if s["arm"] == arm and s["seed"] == seed)
                return next(c["gain_percent"] for c in run["checkpoints"] if c["proposals"] == paired["proposals"])
            differences.append(gain(paired["arm"])-gain(paired["control"]))
        assert differences == paired["seed_differences_percentage_points"]
    audits = {a["recipe_id"]: a for a in results["audits"]}
    for row in results["grammar_capacity"]:
        admitted = [r for r, recipe in recipes.items() if r != "raw" and all(not recipe[a] for a in ["u","r","free_s","combined_y"] if a not in row["axes"])]
        qualified = [r for r in admitted if audits[r]["meets_bound"]]
        assert len(admitted) == row["recipes"] and len(qualified) == row["qualified"]
        assert row["QT"] == min(audits[r]["QT"] for r in qualified)
        assert abs(row["gain_percent"]-100*(1-row["QT"]/audits["c0000"]["QT"])) < 1e-12
    # Preserve the fresh audit rows and exact circuit bytes in portable artifacts.
    artifacts = []
    folder = STUDY / "records/audit-artifacts"
    folder.mkdir(parents=True, exist_ok=True)
    for rid, audit in audits.items():
        raw_receipt = WORK / "evaluations" / Path(audit["record"]).stem / "check.json"
        raw = load(raw_receipt)
        source = Path(raw["per_input"])
        out = folder / f"{rid}.jsonl.gz"
        out.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
        ops = WORK / "circuits" / rid / "circuit.qops.zst"
        saved_ops = folder / f"{rid}.qops.zst"
        saved_ops.write_bytes(ops.read_bytes())
        artifacts.append({"recipe_id": rid, "outcomes": out.relative_to(ROOT).as_posix(),
                          "outcomes_sha256": digest(out), "uncompressed_sha256": digest(source),
                          "ops": saved_ops.relative_to(ROOT).as_posix(), "ops_sha256": digest(saved_ops)})
    (STUDY / "artifacts.json").write_text(json.dumps(artifacts, indent=2)+"\n")
    report = {"status": "passed", "protocol_sha256": results["protocol_sha256"],
              "results_sha256": digest(STUDY / "results.json"), "recounted_stream_verdicts": checked_rows,
              "searches": 20, "fresh_audits": 17, "prepared_parent_qualified": True,
              "scope": "All evaluation identities, row counts, joint failures, binomial bounds, checkpoints, and paired differences verified"}
    (STUDY / "verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
