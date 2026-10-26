#!/usr/bin/env python3
"""Verify shared evidence without work directories, binaries, or model calls."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import statistics

from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[2]
STUDY = Path(__file__).resolve().parent


def load(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    protocol = load(STUDY / "protocol.json")
    result = load(STUDY / "results.json")
    assert result["status"] == "complete"
    assert result["protocol_sha256"] == digest(STUDY / "protocol.json")
    for name, sha in protocol["sources"].items():
        assert digest(ROOT / name) == sha, name
    frozen = load(STUDY / "finalists.json")
    label = load(STUDY / "audit-label.json")
    assert label["finalists_sha256"] == digest(STUDY / "finalists.json")
    assert frozen["protocol_sha256"] == label["protocol_sha256"] == result["protocol_sha256"]
    audits = {r["recipe_id"]: r for r in result["audits"]}
    assert len(audits) == 17 <= protocol["audit"]["family_size"]
    artifacts = load(STUDY / "artifacts.json")
    assert {r["recipe_id"] for r in artifacts} == set(audits)
    shots = 0
    for artifact in artifacts:
        audit = audits[artifact["recipe_id"]]
        path = ROOT / artifact["outcomes"]
        assert digest(path) == artifact["outcomes_sha256"]
        raw = gzip.decompress(path.read_bytes())
        assert hashlib.sha256(raw).hexdigest() == artifact["uncompressed_sha256"] == audit["per_input_sha256"]
        assert digest(ROOT / artifact["ops"]) == artifact["ops_sha256"] == audit["ops_sha256"]
        counts = Counter()
        stream_ids = {}
        failed = set()
        for line in raw.splitlines():
            row = json.loads(line)
            counts[row["idx"]] += 1
            stream_ids.setdefault(row["idx"],set()).add(row["stream"])
            assert set(row["failures"]) == {"coordinate", "phase", "workspace", "addend"}
            assert row["passed"] == (not any(row["failures"].values()))
            assert row["failures"]["coordinate"] == (row["observed_target"] != row["input"]["expected"])
            assert row["failures"]["addend"] == (row["observed_addend"] != row["input"]["addend"])
            assert row["failures"]["workspace"] == bool(row["dirty_qubits"])
            if any(row["failures"].values()):
                failed.add(row["idx"])
        assert set(counts) == set(range(16384)) and set(counts.values()) == {2}
        assert all(streams == {0,1} for streams in stream_ids.values())
        assert audit["failed_inputs"] == len(failed)
        assert audit["pairs"] == 16384 and audit["streams"] == 2
        assert audit["label"] == label["label"] and audit["alpha"] == .05/20
        upper = float(beta.ppf(1-audit["alpha"],len(failed)+1,audit["pairs"]-len(failed)))
        assert abs(upper-audit["binomial_upper"]) < 1e-12
        assert audit["meets_bound"] == (upper <= .001)
        assert audit["Q"] * audit["T"] == audit["QT"]
        assert audit["checker_binary_sha256"] == protocol["binaries"]["checker"]
        shots += sum(counts.values())
    assert len({r["packet_sha256"] for r in audits.values()}) == 1
    assert audits["c0000"]["meets_bound"] and audits["raw"]["meets_bound"]
    assert result["prepared_parent"] == audits["c0000"] and result["raw_parent"] == audits["raw"]
    assert abs(result["shared_setup_gain_percent"]-100*(1-audits["c0000"]["QT"]/audits["raw"]["QT"])) < 1e-12
    recipes = {r["id"]: r for r in protocol["recipes"]}
    assert len(result["searches"]) == 20
    for run in result["searches"]:
        assert len(run["slots"]) == 8 and run["complete"]
        previous = {"c0000"}
        for slot in run["slots"]:
            if slot["status"] == "failed_model_batch":
                continue
            rid = slot["recipe_id"]
            assert rid in recipes and rid not in previous
            previous.add(rid)
            evidence = load(ROOT / slot["record"])
            for k in ["Q", "T", "QT", "failed_inputs", "ops_sha256"]:
                assert slot[k] == evidence[k]
            assert evidence["ops_sha256"] == audits[rid]["ops_sha256"]
            assert evidence["checker_binary_sha256"] == protocol["binaries"]["checker"]
            assert evidence["label"] == f"point-addition-search-v1/development/{run['seed']}"
            assert evidence["packet_sha256"] == run["baseline"]["packet_sha256"]
        for cp in run["checkpoints"]:
            eligible = [("c0000",run["baseline"]["QT"])] + [(s["recipe_id"],s["QT"]) for s in run["slots"][:cp["proposals"]] if s["status"] == "passed"]
            rid, qt = min(eligible,key=lambda x:(x[1],x[0]))
            assert (cp["recipe_id"],cp["QT"]) == (rid,qt)
            assert abs(cp["gain_percent"]-100*(1-qt/run["baseline"]["QT"])) < 1e-12
        for call in run["calls"]:
            folder = STUDY / "records/models" / f"{run['arm']}-{run['seed']}" / f"batch-{call['batch']}"
            assert digest(folder/"prompt.json") == call["prompt_sha256"]
            prompt = load(folder/"prompt.json")
            prior = {"c0000"} | {s["recipe_id"] for s in run["slots"][:2*call["batch"]] if "recipe_id" in s}
            assert {r["recipe_id"] for r in prompt["context"]["measured_archive"]} <= prior
            unseen = set(recipes)-prior
            assert {r["id"] for r in prompt["context"]["recipes"]} == unseen
            assert all(a["first"] in unseen and a["second"] in unseen and a["first"] != a["second"] for a in prompt["actions"])
            if call["error"] is None:
                assert not call["tool_use"] and call["success"]
                answer = load(folder/"answer.json")
                chosen = prompt["actions"][answer["action_index"]]
                slots = run["slots"][2*call["batch"]:2*call["batch"]+2]
                assert [s["recipe_id"] for s in slots] == [chosen["first"],chosen["second"]]
    for row in result["grammar_capacity"]:
        admitted = [rid for rid,r in recipes.items() if all(not r[a] for a in ["u","r","free_s","combined_y"] if a not in row["axes"])]
        qualified = [rid for rid in admitted if audits[rid]["meets_bound"]]
        assert len(admitted) == row["recipes"] and len(qualified) == row["qualified"]
        best = min(qualified,key=lambda r:(audits[r]["QT"],r))
        assert best == row["best_recipe_id"]
        assert all(row[k] == audits[best][k] for k in ["Q","T","QT"])
        assert abs(row["gain_percent"]-100*(1-row["QT"]/audits["c0000"]["QT"])) < 1e-12
    for s in result["search_summaries"]:
        gains = [next(c["gain_percent"] for c in r["checkpoints"] if c["proposals"] == s["proposals"]) for r in result["searches"] if r["arm"] == s["arm"]]
        assert gains == s["seed_gains_percent"]
        assert statistics.median(gains) == s["median_gain_percent"]
    for pair in result["paired_differences"]:
        differences = []
        for seed in result["seeds"]:
            def gain(arm):
                r = next(r for r in result["searches"] if r["seed"] == seed and r["arm"] == arm)
                return next(c["gain_percent"] for c in r["checkpoints"] if c["proposals"] == pair["proposals"])
            differences.append(gain(pair["arm"])-gain(pair["control"]))
        assert differences == pair["seed_differences_percentage_points"]
    for record in result["finalists"]:
        selected = record["selected_recipe_id"]
        assert record["qualified"] == audits[selected]["meets_bound"]
        retained = selected if record["qualified"] else "c0000"
        assert record["retained_recipe_id"] == retained
        assert abs(record["audit_gain_percent"]-100*(1-audits[retained]["QT"]/audits["c0000"]["QT"])) < 1e-12
    report = {"status":"passed","results_sha256":digest(STUDY/"results.json"),
              "fresh_audits":17,"recounted_stream_verdicts":shots,"searches":20,
              "model_calls":sum(len(r["calls"]) for r in result["searches"]),
              "scope":"Retained audit rows, circuit bytes, model prompts, proposals, bounds, and comparisons. Development rows and raw CLI logs remain local."}
    (STUDY/"shared-verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
