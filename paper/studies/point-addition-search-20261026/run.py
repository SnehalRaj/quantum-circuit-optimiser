#!/usr/bin/env python3
"""Compare a finite point-addition grammar and bounded proposer policies."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import itertools
import json
from pathlib import Path
import random
import secrets
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
STUDY = Path(__file__).resolve().parent
WORK = ROOT / "work/point-addition-search-20261026"
sys.path.insert(0, str(ROOT / "src"))
from llm_circuit_optimiser import models

SEEDS = [401, 809, 1601, 2309, 3501]
ARMS = ["random", "evolution", "claude", "codex"]
CHECKPOINTS = [1, 2, 4, 8]
AXES = ["u", "r", "free_s", "combined_y"]
EMITTER = ROOT / "work/native-architecture-validation/frozen-bin/small-emit"
CHECKER = ROOT / "work/native-architecture-validation/frozen-bin/circuit-check"
LOCKS: dict[str, threading.Lock] = {}
LOCK_GUARD = threading.Lock()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def load(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relative(path):
    return Path(path).relative_to(ROOT).as_posix()


def candidate(bits):
    return {"id": "c" + "".join(map(str, bits)), **dict(zip(AXES, map(bool, bits))),
            "policy": "mfs" + ("u" if bits[0] else "") + ("r" if bits[1] else "")}


RECIPES = {r["id"]: r for r in map(candidate, itertools.product([0, 1], repeat=4))}
PARENT = "c0000"
RAW = {**RECIPES[PARENT], "id": "raw", "policy": "none"}
ALL = {**RECIPES, "raw": RAW}


def run_command(command, folder):
    folder.mkdir(parents=True, exist_ok=True)
    begin = time.monotonic()
    proc = subprocess.run(list(map(str, command)), cwd=ROOT, capture_output=True, text=True)
    (folder / "stdout.txt").write_text(proc.stdout)
    (folder / "stderr.txt").write_text(proc.stderr)
    save(folder / "process.json", {"command": list(map(str, command)), "returncode": proc.returncode,
                                  "wall_seconds": time.monotonic() - begin})
    return proc


def freeze_protocol():
    sources = ["native/src/bin/small-emit.rs", "native/src/bin/circuit-check.rs",
               "native/src/lib.rs", "vendor/native_reference/original/mod.rs",
               "src/llm_circuit_optimiser/models.py", relative(Path(__file__))]
    protocol = {
        "schema": "point-addition-search-v1", "parent": RECIPES[PARENT], "raw_parent": RAW,
        "seeds": SEEDS, "arms": ARMS, "proposal_budget": 8, "batch_size": 2,
        "checkpoints": CHECKPOINTS, "recipes": list(RECIPES.values()),
        "grammars": {"G0": ["u", "r"], "G1": ["u", "r", "free_s"], "G2": AXES},
        "metric": "Q times mean executed Toffolis; gains relative to prepared mfs parent on the same inputs",
        "development": {"pairs": 4096, "streams": 2, "admission": "zero failed inputs",
                        "checks": ["coordinates", "phase", "scratch", "classical addend"]},
        "audit": {"pairs": 16384, "streams": 2, "alpha": 0.05 / 20, "epsilon": 0.001,
                  "family_size": 20, "scope": "All sixteen recipes and raw parent; at most seventeen circuits",
                  "label": "Draw only after all twenty search finalists freeze",
                  "failure_policy": "Retain failed finalist as baseline; report failure without reselection"},
        "capacity": "Evaluate all recipes after search; use one common development packet. Report attainable minima by grammar.",
        "batch_policy": "Choose two unseen recipes before either evaluation. All arms receive feedback only after the batch.",
        "random_policy": "Uniform ordered pair without replacement from unseen recipes",
        "evolution_policy": "Choose the lowest-cost valid archive parent with probability 0.75, otherwise a uniform valid parent. Select an unseen one-bit neighbor with probability 0.75 when available; otherwise choose uniformly from unseen recipes. Freeze both choices before feedback.",
        "model_policy": "Select an ordered pair from the same unseen recipe universe using the measured archive and rule descriptions. No exhaustive outcomes or historical winners in the prompt.",
        "failure_policy": "Invalid, duplicate, tool-using, failed, or timed-out model batches consume two slots; no offline replacement or retry",
        "model_configs": {p: {**load(ROOT / f"configs/{p}.json"), "max_calls": 4} for p in ["claude", "codex"]},
        "limits": ["One inherited Kaliski constructor at sixteen bits", "Finite sixteen-recipe space", "Model seeds vary menu order and evaluation packets; CLI sampling seeds are not controlled", "Grammar capacity and proposer efficiency are separate measurements", "No full-width transfer claim"],
        "sources": {p: digest(ROOT / p) for p in sources},
        "binaries": {"emitter": digest(EMITTER), "checker": digest(CHECKER)},
    }
    path = STUDY / "protocol.json"
    if path.exists():
        if load(path) != protocol:
            raise RuntimeError("Frozen protocol differs; do not overwrite an existing study")
    else:
        save(path, protocol)
        (STUDY / "protocol.sha256").write_text(digest(path) + "\n")
    return protocol


def emit(recipe_id):
    r = ALL[recipe_id]
    folder = WORK / "circuits" / recipe_id
    receipt = folder / "summary.json"
    if receipt.exists():
        result = load(receipt)
        assert digest(ROOT / result["ops"]) == result["ops_sha256"]
        return result
    ops = folder / "circuit.qops.zst"
    command = [EMITTER, "--policy", r["policy"], "--parent-policy", "mfs", "--free-s",
               str(r["free_s"]).lower(), "--combined-y", str(r["combined_y"]).lower(), "--out", ops]
    proc = run_command(command, folder)
    if proc.returncode:
        raise RuntimeError(f"Emission failed: {recipe_id}")
    result = {"recipe": r, "ops": relative(ops), "ops_sha256": digest(ops),
              "emitter_sha256": digest(EMITTER), "stdout_sha256": digest(folder / "stdout.txt")}
    save(receipt, result)
    return result


def evaluate(recipe_id, label, pairs=4096, alpha=0.05):
    key = hashlib.sha256(json.dumps([recipe_id, label, pairs, alpha]).encode()).hexdigest()
    with LOCK_GUARD:
        lock = LOCKS.setdefault(key, threading.Lock())
    with lock:
        folder = WORK / "evaluations" / key
        clean = STUDY / "records/evaluations" / f"{key}.json"
        if clean.exists():
            return load(clean)
        emitted = emit(recipe_id)
        output = folder / "check.json"
        proc = run_command([CHECKER, "--width", "16", "--ops", ROOT / emitted["ops"],
                            "--pairs", pairs, "--streams", "2", "--label", label,
                            "--alpha", alpha, "--out", output], folder)
        if proc.returncode not in [0, 1] or not output.exists():
            raise RuntimeError(f"Checker instrument failed: {recipe_id}; {key}")
        raw = load(output)
        keys = ["qubits", "mean_toffoli", "failed_inputs", "failed_shots", "packet_sha256", "per_input_sha256",
                "ops_sha256", "checker_source_sha256", "checker_binary_sha256", "alpha", "binomial_upper",
                "meets_bound", "pairs", "streams", "elapsed_seconds", "domain_exclusions", "scalar_rejections"]
        result = {k: raw[k] for k in keys}
        result.update(recipe_id=recipe_id, label=label, Q=raw["qubits"], T=raw["mean_toffoli"],
                      QT=raw["qubits"] * raw["mean_toffoli"], passed=raw["failed_inputs"] == 0,
                      original_receipt_sha256=digest(output), record=relative(clean))
        save(clean, result)
        return result


def tool_use(provider, output):
    if provider != "codex":
        return False
    path = output / "stdout.txt"
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind = e.get("item", {}).get("type")
        if kind in {"command_execution", "mcp_tool_call", "web_search", "file_change"}:
            return True
    return False


def run_search(arm, seed, protocol):
    path = STUDY / "records/searches" / f"{arm}-{seed}.json"
    if path.exists() and load(path).get("complete"):
        return load(path)
    label = f"point-addition-search-v1/development/{seed}"
    baseline = evaluate(PARENT, label)
    if not baseline["passed"]:
        raise RuntimeError("Prepared parent fails the development checks")
    rng = random.Random(seed)
    tested = {PARENT}
    archive = {PARENT: baseline}
    slots = []
    calls = []
    begin = time.monotonic()
    for batch in range(4):
        unseen = [rid for rid in sorted(RECIPES) if rid not in tested]
        rng.shuffle(unseen)
        picked = []
        error = None
        if arm == "random":
            picked = rng.sample(unseen, 2)
        elif arm == "evolution":
            for _ in range(2):
                parent = min(archive, key=lambda r: (archive[r]["QT"], r)) if rng.random() < .75 else rng.choice(sorted(archive))
                available = [r for r in unseen if r not in picked]
                neighbors = [r for r in available if sum(RECIPES[r][a] != RECIPES[parent][a] for a in AXES) == 1]
                picked.append(rng.choice(neighbors if neighbors and rng.random() < .75 else available))
        else:
            actions = [{"first": a, "second": b} for a in unseen for b in unseen if a != b]
            rng.shuffle(actions)
            context = {
                "objective": protocol["metric"], "prepared_parent": RECIPES[PARENT],
                "budget": {"slots_used": len(slots), "slots_total": 8, "batch_size": 2},
                "rule_descriptions": {"u": "Replace eligible AND erasure by X-basis measurement with phase repair",
                    "r": "Convert unread writes before measurement or reset into phase operations",
                    "free_s": "Release the cofactor register during inversion and reconstruct it later",
                    "combined_y": "Use a combined expression for the coordinate products"},
                "recipes": [RECIPES[r] for r in unseen],
                "measured_archive": [{"recipe_id": r, "Q": v["Q"], "mean_T": v["T"], "QT": v["QT"], "failed_inputs": v["failed_inputs"]} for r, v in archive.items()],
                "previous_slots": [{k: v for k, v in s.items() if k in {"slot", "recipe_id", "status", "Q", "T", "QT", "failed_inputs"}} for s in slots],
                "checks": protocol["development"]["checks"],
                "information_boundary": "Use only this prompt. Use no tools and do not inspect files. No unseen circuit outcomes are provided.",
            }
            output = WORK / "models" / f"{arm}-{seed}" / f"batch-{batch}"
            try:
                choice, receipt = models.select(actions, protocol["model_configs"][arm], output, context=context, call_index=batch)
                if tool_use(arm, output):
                    raise models.ModelError("Model used a tool outside the declared information boundary")
                picked = [choice["first"], choice["second"]]
                if len(set(picked)) != 2 or any(r not in unseen for r in picked):
                    raise models.ModelError("Batch contains a duplicate or inadmissible recipe")
            except models.ModelError as e:
                error = str(e).replace(str(ROOT), "<repository>")
                receipt = load(output / "receipt.json") if (output / "receipt.json").exists() else {}
            clean_receipt = {k: receipt.get(k) for k in ["provider", "requested_model", "resolved_models", "usage", "provider_estimate_usd", "wall_seconds", "success", "reason"]}
            clean_receipt.update(batch=batch, error=error, tool_use=tool_use(arm, output),
                                 original_receipt_sha256=digest(output / "receipt.json") if (output / "receipt.json").exists() else None,
                                 prompt_sha256=digest(output / "prompt.json") if (output / "prompt.json").exists() else None)
            calls.append(clean_receipt)
        if error is not None:
            for _ in range(2):
                slots.append({"slot": len(slots) + 1, "status": "failed_model_batch", "error": error})
        else:
            # Decisions for both candidates precede these evaluations.
            evaluated = [(r, evaluate(r, label)) for r in picked]
            for rid, value in evaluated:
                tested.add(rid)
                slots.append({"slot": len(slots) + 1, "recipe_id": rid, "status": "passed" if value["passed"] else "failed_checks",
                              **{k: value[k] for k in ["Q", "T", "QT", "failed_inputs", "record", "ops_sha256"]}})
                if value["passed"]:
                    archive[rid] = value
        save(path, {"arm": arm, "seed": seed, "complete": False, "slots": slots, "calls": calls})
    checkpoints = []
    for n in CHECKPOINTS:
        choices = [(PARENT, baseline["Q"], baseline["T"], baseline["QT"])]
        choices += [(s["recipe_id"], s["Q"], s["T"], s["QT"]) for s in slots[:n] if s["status"] == "passed"]
        rid, q, t, qt = min(choices, key=lambda x: (x[3], x[0]))
        checkpoints.append({"proposals": n, "recipe_id": rid, "Q": q, "T": t, "QT": qt,
                            "gain_percent": 100 * (1 - qt / baseline["QT"])})
    result = {"arm": arm, "seed": seed, "complete": True, "baseline": baseline,
              "slots": slots, "calls": calls, "checkpoints": checkpoints,
              "finalist": checkpoints[-1]["recipe_id"], "wall_seconds": time.monotonic() - begin}
    save(path, result)
    print(f"complete {arm} seed={seed} gain={checkpoints[-1]['gain_percent']:.4f}%", flush=True)
    return result


def summarise(protocol, searches):
    capacity = {rid: evaluate(rid, "point-addition-search-v1/capacity") for rid in ALL}
    frozen = {"protocol_sha256": digest(STUDY / "protocol.json"),
              "finalists": [{"arm": r["arm"], "seed": r["seed"], "recipe_id": r["finalist"]} for r in searches],
              "audited_recipe_ids": sorted(ALL), "operation_hashes": {rid: emit(rid)["ops_sha256"] for rid in ALL}}
    frozen_path = STUDY / "finalists.json"
    if frozen_path.exists():
        assert load(frozen_path) == frozen
    else:
        save(frozen_path, frozen)
    label_path = STUDY / "audit-label.json"
    if not label_path.exists():
        save(label_path, {"label": "point-addition-search-v1/audit/" + secrets.token_hex(24),
                          "finalists_sha256": digest(frozen_path), "protocol_sha256": digest(STUDY / "protocol.json")})
    audit_label = load(label_path)
    assert audit_label["finalists_sha256"] == digest(frozen_path)
    audits = {rid: evaluate(rid, audit_label["label"], pairs=16384, alpha=.05/20) for rid in ALL}
    grammar_rows = []
    for name, axes in protocol["grammars"].items():
        admitted = [rid for rid, r in RECIPES.items() if all(not r[a] for a in AXES if a not in axes)]
        qualified = [rid for rid in admitted if audits[rid]["meets_bound"]]
        best = min(qualified, key=lambda rid: (audits[rid]["QT"], rid))
        grammar_rows.append({"grammar": name, "axes": axes, "recipes": len(admitted),
                             "distinct_operation_hashes": len({emit(r)["ops_sha256"] for r in admitted}),
                             "qualified": len(qualified), "best_recipe_id": best,
                             **{k: audits[best][k] for k in ["Q", "T", "QT"]},
                             "gain_percent": 100 * (1 - audits[best]["QT"] / audits[PARENT]["QT"])})
    summaries = []
    paired = []
    for arm in ARMS:
        runs = [s for s in searches if s["arm"] == arm]
        for n in CHECKPOINTS:
            gains = [next(c["gain_percent"] for c in s["checkpoints"] if c["proposals"] == n) for s in runs]
            summaries.append({"arm": arm, "proposals": n, "seed_gains_percent": gains,
                              "median_gain_percent": statistics.median(gains), "mean_gain_percent": statistics.mean(gains)})
    for arm in ["claude", "codex"]:
        for control in ["random", "evolution"]:
            for n in CHECKPOINTS:
                differences = []
                for seed in SEEDS:
                    def gain(a):
                        run = next(s for s in searches if s["arm"] == a and s["seed"] == seed)
                        return next(c["gain_percent"] for c in run["checkpoints"] if c["proposals"] == n)
                    differences.append(gain(arm) - gain(control))
                paired.append({"arm": arm, "control": control, "proposals": n,
                               "seed_differences_percentage_points": differences,
                               "median_difference_percentage_points": statistics.median(differences)})
    finalists = []
    for search in searches:
        rid = search["finalist"]
        qualified = audits[rid]["meets_bound"]
        retained = rid if qualified else PARENT
        finalists.append({"arm": search["arm"], "seed": search["seed"], "selected_recipe_id": rid,
                          "qualified": qualified, "retained_recipe_id": retained,
                          "audit_gain_percent": 100 * (1 - audits[retained]["QT"] / audits[PARENT]["QT"])})
    result = {"schema": "point-addition-search-results-v1", "status": "complete",
              "protocol_sha256": digest(STUDY / "protocol.json"), "seeds": SEEDS,
              "prepared_parent": audits[PARENT], "raw_parent": audits["raw"],
              "shared_setup_gain_percent": 100 * (1 - audits[PARENT]["QT"] / audits["raw"]["QT"]),
              "grammar_capacity": grammar_rows, "capacity_development": list(capacity.values()),
              "audits": list(audits.values()), "search_summaries": summaries,
              "paired_differences": paired, "searches": searches, "finalists": finalists,
              "limits": protocol["limits"]}
    save(STUDY / "results.json", result)
    print(json.dumps({"grammar_capacity": grammar_rows, "shared_setup_gain_percent": result["shared_setup_gain_percent"]}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["freeze", "run", "summary"], default="run")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    protocol = freeze_protocol()
    if args.phase == "freeze":
        print(digest(STUDY / "protocol.json"))
        return
    for rid in ALL:
        emit(rid)
    searches = []
    if args.phase == "run":
        # One run per provider at a time avoids CLI contention.
        def arm_runs(arm):
            return [run_search(arm, seed, protocol) for seed in SEEDS]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(arm_runs, arm) for arm in ARMS]
            for future in as_completed(futures):
                searches.extend(future.result())
    else:
        searches = [load(STUDY / "records/searches" / f"{arm}-{seed}.json") for arm in ARMS for seed in SEEDS]
        assert all(s.get("complete") for s in searches)
    searches.sort(key=lambda s: (ARMS.index(s["arm"]), SEEDS.index(s["seed"])))
    summarise(protocol, searches)


if __name__ == "__main__":
    main()
