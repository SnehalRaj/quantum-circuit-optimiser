#!/usr/bin/env python3
"""Run a paired grammar study against the frozen division evaluator."""
from pathlib import Path
import copy
from datetime import datetime, timezone
import hashlib
import json
import random
import secrets
import shutil
import statistics
import sys
import time
from collections import Counter

HERE = Path(__file__).resolve().parent
PAPER = HERE.parents[1]
SOURCE = HERE / "source/src"
sys.path.insert(0, str(SOURCE))
from circuitopt import block_program, engine, grammar_campaign, rules, search_space, study

WEIGHTS = {"inverse": 1, "replay": 32.72575137951653, "width": 100}
SEEDS = [401, 809, 1601]
ARMS = {
    "G0": {"name": "Fixed payload, legacy constants", "shape": False, "direct": False, "guard": False},
    "G1": {"name": "Editable payload shape, legacy constants", "shape": True, "direct": False, "guard": False},
    "G2": {"name": "Fixed payload shape, editable constants", "shape": False, "direct": True, "guard": False},
    "G3": {"name": "Editable shape and constants", "shape": True, "direct": True, "guard": False},
    "G4": {"name": "Editable shape and constants with finite guard", "shape": True, "direct": True, "guard": True},
}
WORK = HERE / "run"


def save(path, value):
    study.save(path, value)


def now():
    return datetime.now(timezone.utc).isoformat()


def programs(arm):
    cfg = ARMS[arm]
    seeds = block_program.seeds()
    if not cfg["shape"] and not cfg["direct"]:
        return []
    if not cfg["shape"]:
        return seeds[:4]
    if not cfg["direct"]:
        return [p for p in seeds if all(x["lowering"] == "legacy" for x in p["ops"])]
    return seeds


def initialize():
    if (HERE / "contract.json").exists():
        raise FileExistsError("This fixed study already exists")
    c = study.initialise(WORK, bits=[16], trials=60, seeds=SEEDS,
                         provider="offline", model=None, audit_pairs=16384,
                         epsilon=.001, strategies=["evolution"], format_arms=[],
                         max_model_calls=0, grammar="blocks-v1", wall_seconds=86400)
    c["calibration"]["16"] = {"quick_pairs": 512, "long_pairs": 4096, "weights": WEIGHTS}
    save(WORK / "study.json", c)
    (WORK / "contract.sha256").write_text(engine.digest(c) + "\n")
    contract = {
        "schema": 1,
        "created_utc": now(),
        "purpose": "Retrospective component ablation designed after the earlier frontier results",
        "arms": ARMS,
        "seeds": SEEDS,
        "baselines": search_space.baseline_recipes(16),
        "weights": WEIGHTS,
        "proposals_per_baseline_seed_arm": 60,
        "total_proposal_slots": 5 * 3 * 3 * 60,
        "quick_pairs": 512,
        "long_pairs": 4096,
        "development_label": "workshop-20261025/grammar/development-v1",
        "audit_pairs": 16384,
        "audit_label": "drawn only after all finalists freeze",
        "epsilon": .001,
        "family_alpha": .05,
        "model_calls": 0,
        "policy": {
            "parents": "Choose a valid family uniformly; random parent with probability0.35, otherwise its minimum-cost parent",
            "bridges": "First four slots share measured replay, measured width, width compare15, and exact finite widths",
            "mutation": "Existing nine recipe rules plus R21 for the permitted proved payload programs",
            "duplicates": "Recipe or operation duplicates consume their slot",
            "guard": "Only G4 rejects candidates that violate exact finite walk and width requirements",
            "cache": "Reuse exact recipe and common-packet evaluations across arms; compare proposal budgets, not wall-clock efficiency",
        },
        "scope": "Two proved equivalent payload shapes and per-operation constant lowering; arbitrary block programs are outside this component study",
        "programs": {a: programs(a) for a in ARMS},
        "source_sha256": study.source_hashes(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    save(HERE / "contract.json", contract)
    (HERE / "contract.sha256").write_text(engine.digest(contract) + "\n")
    save(HERE / "progress.json", {"status": "contract_frozen", "created_utc": now()})
    return contract


def cached_evaluate(recipe, pairs, stage, cache):
    key = f"{engine.digest(recipe)}-{pairs}"
    if key in cache:
        return copy.deepcopy(cache[key])
    result = study.evaluate(WORK, recipe, pairs,
                            "workshop-20261025/grammar/development-v1", key, stage,
                            timeout=1800)
    cache[key] = result
    save(HERE / "evaluation-cache.json", cache)
    return copy.deepcopy(result)


def propose(state, rng, arm, trial, requirements):
    valid = [e for e in state["archive"].values() if e["result"]["stage"] == "long"
             and not e["result"]["failed_inputs"]]
    groups = {}
    for e in valid:
        groups.setdefault(e["recipe"]["family"], []).append(e)
    family = rng.choice(sorted(groups))
    group = groups[family]
    parent = rng.choice(group) if rng.random() < .35 else min(group, key=lambda e:e["result"]["S"])
    r = copy.deepcopy(parent["recipe"])
    bridges = [dict(family="replay", cleanup="measured"),
               dict(family="width", cleanup="measured"),
               dict(family="width", cleanup="measured", compare_bits=15),
               dict(family="width", cleanup="measured", widths=requirements["widths"])]
    if trial <= 4:
        r = {**search_space.baseline_recipes(16)["width"], **bridges[trial-1]}
        return parent, r, "shared_bridge"
    choices = search_space.options(r, rng, include_blocks=False)
    allowed = programs(arm)
    if allowed and r["family"] in ("inverse", "replay", "width"):
        choices.append(("R21", {"program": copy.deepcopy(rng.choice(allowed))}))
    priorities = {c["id"]:c["priority"] for c in rules.cards(WORK / "rules")}
    while choices:
        rid, parameters = rng.choices(choices, weights=[priorities[x[0]] for x in choices], k=1)[0]
        try:
            applied = rules.apply(WORK / "rules", rid, r, parameters)
            return parent, applied["candidate"], rid
        except ValueError:
            choices.remove((rid, parameters))
    raise ValueError("No applicable rule")


def run_search(arm, seed, requirements, cache):
    rng = random.Random(seed)
    run = {"arm":arm, "seed":seed, "started_utc":now(), "trials":[], "baselines":{}}
    start = time.monotonic()
    for baseline, recipe in search_space.baseline_recipes(16).items():
        r = cached_evaluate(recipe, 4096, "long", cache)
        if r["failed_inputs"]:
            raise ValueError("A baseline fails development")
        entry = {"id":engine.digest(recipe), "recipe":recipe, "result":r, "parent":None}
        run["baselines"][baseline] = {
            "starting":entry, "selected":entry, "archive":{entry["id"]:entry},
            "seen":[entry["id"]], "operation_seen":[r["operation_sha256"]],
        }
    for trial in range(1,61):
        for baseline, state in run["baselines"].items():
            row = {"trial":trial, "baseline":baseline, "status":"generation_error"}
            try:
                parent, recipe, rule = propose(state,rng,arm,trial,requirements)
                cid = engine.digest(recipe)
                row.update(rule=rule,id=cid,parent=parent["id"],recipe=recipe)
                if cid in state["seen"]:
                    row["status"] = "recipe_duplicate"
                elif ARMS[arm]["guard"] and not grammar_campaign.exact_guard(recipe, requirements):
                    row["status"] = "guard_rejected"
                    state["seen"].append(cid)
                else:
                    state["seen"].append(cid)
                    quick = cached_evaluate(recipe,512,"quick",cache)
                    if quick["operation_sha256"] in state["operation_seen"]:
                        row["status"] = "operation_duplicate"
                    else:
                        state["operation_seen"].append(quick["operation_sha256"])
                        r = quick if quick["failed_inputs"] else cached_evaluate(recipe,4096,"long",cache)
                        row.update(S=r["S"],failed_inputs=r["failed_inputs"],stage=r["stage"])
                        entry = {"id":cid,"recipe":recipe,"result":r,"parent":parent["id"]}
                        state["archive"][cid] = entry
                        row["status"] = "failed" if r["failed_inputs"] else "long_pass"
                        if not r["failed_inputs"] and r["stage"] == "long" and r["S"] < state["selected"]["result"]["S"]:
                            state["selected"] = entry
                            row["status"] = "improvement"
            except ValueError as exc:
                row["error"] = str(exc)
            run["trials"].append(row)
        save(HERE / "searches" / f"{arm}-{seed}.json", run)
        if trial % 10 == 0:
            print(json.dumps({"phase":"search","arm":arm,"seed":seed,"trial":trial,
                              "selected":{b:s["selected"]["result"]["S"] for b,s in run["baselines"].items()},
                              "unique_evaluations":len(cache)}), flush=True)
    run.update(status="complete",wall_seconds=time.monotonic()-start,completed_utc=now())
    save(HERE / "searches" / f"{arm}-{seed}.json",run)
    save(HERE / "progress.json", {"status":"search_complete","arm":arm,"seed":seed,"utc":now()})
    return run


def audit(runs):
    selected, recipes = [], {}
    for run in runs:
        for baseline, state in run["baselines"].items():
            row = {"arm":run["arm"],"seed":run["seed"],"baseline":baseline,"weight":WEIGHTS[baseline]}
            for role in ("starting", "selected"):
                entry = state[role]
                row[role] = entry["id"]
                recipes[entry["id"]] = entry["recipe"]
            selected.append(row)
    manifest = {"created_utc":now(),"selections":selected,"recipes":recipes,
                "pairs":16384,"alpha":.05,"epsilon":.001,"family_size":len(recipes)}
    save(HERE / "finalists.json",manifest)
    (HERE / "finalists.sha256").write_text(engine.digest(manifest)+"\n")
    label = "workshop-20261025/grammar/audit/" + secrets.token_hex(24)
    save(HERE / "audit-label.json", {"label":label,"created_utc":now(),"consumed":True})
    receipts = {}
    for cid, recipe in sorted(recipes.items()):
        r = study.evaluate(WORK,recipe,16384,label,"audit-"+cid,"audit",timeout=1800)
        r["upper"] = engine.upper(r["failed_inputs"],16384,.05/len(recipes))
        r["eligible"] = r["upper"] <= .001
        receipts[cid] = r
        save(HERE / "audit-receipts.json",receipts)
        print(json.dumps({"phase":"audit","done":len(receipts),"total":len(recipes),
                          "failed_inputs":r["failed_inputs"],"upper":r["upper"]}),flush=True)
    rows = []
    for s in selected:
        before, after = receipts[s["starting"]], receipts[s["selected"]]
        eligible = before["eligible"] and after["eligible"]
        rows.append({**s,"starting_S":before["S"],"S":after["S"],"Q":after["Q"],"T":after["T"],
                     "failed_inputs":after["failed_inputs"],"pairs":16384,"upper":after["upper"],
                     "eligible":eligible,"retained_S":min(before["S"],after["S"]) if eligible else before["S"]})
    summaries = []
    for arm in ARMS:
        seed_scores = []
        for seed in SEEDS:
            chosen = [r for r in rows if r["arm"] == arm and r["seed"] == seed]
            assert set(x["baseline"] for x in chosen) == set(WEIGHTS)
            remaining = sum(x["weight"]*x["retained_S"]/x["starting_S"] for x in chosen)/sum(WEIGHTS.values())
            seed_scores.append({"seed":seed,"remaining":remaining,"improvement_percent":100*(1-remaining)})
        counts = Counter(t["status"] for run in runs if run["arm"] == arm for t in run["trials"])
        median_remaining = statistics.median(r["remaining"] for r in seed_scores)
        summaries.append({"arm":arm,**ARMS[arm],"seed_scores":seed_scores,
                          "median_remaining":median_remaining,"median_improvement_percent":100*(1-median_remaining),
                          "per_baseline_median_S":{b:statistics.median(x["retained_S"] for x in rows if x["arm"] == arm and x["baseline"] == b) for b in WEIGHTS},
                          "proposal_slots":sum(counts.values()),"proposal_outcomes":dict(counts),
                          "qualified_endpoints":sum(x["eligible"] for x in rows if x["arm"] == arm),"total_endpoints":9})
    best = max(r["median_improvement_percent"] for r in summaries)
    winners = [r["arm"] for r in summaries if abs(r["median_improvement_percent"]-best)<1e-10]
    costs = [json.loads(line) for line in (WORK / "costs.jsonl").read_text().splitlines()]
    return {"schema":1,"status":"complete","created_utc":now(),
            "design":"Retrospective2x2payload-shape/constant-lowering comparison, plus finite-guard treatment",
            "contract":"studies/grammar/contract.json","contract_sha256":engine.digest(study.load(HERE/"contract.json")),
            "seeds":SEEDS,"baseline_weights":WEIGHTS,"model_calls":0,"proposals_per_baseline_seed_arm":60,
            "quick_pairs":512,"long_pairs":4096,"audit_pairs":16384,"audit_family_size":len(recipes),
            "audit_alpha_per_member":.05/len(recipes),"epsilon":.001,
            "arms":summaries,"rows":rows,"best_arms":winners,
            "timing":{"evaluation_cpu_seconds":sum(x.get("cpu_seconds",0) or 0 for x in costs),
                       "evaluation_wall_seconds":sum(x.get("wall_seconds",0) or 0 for x in costs),
                       "unique_evaluations":len(costs)},
            "limits":["Study designed after earlier frontier results; it does not reconstruct original discovery chronology.",
                      "Only two proved equivalent payload shapes are tested; this does not cover arbitrary one-to-eight-operation programs.",
                      "Identical recipe and common-packet evaluations are cached across arms; wall-clock comparisons are not meaningful.",
                      "Three seeds on the known16-bit suite do not establish transfer to full point addition.",
                      "The five arms differ in grammar support; their shared proposal policy induces different legal action probabilities."]}


def main():
    start = time.monotonic()
    initialize()
    requirements = grammar_campaign.walk_requirements(16,65521)
    save(HERE / "finite-requirements.json",requirements)
    cache = {}
    runs = []
    for seed in SEEDS:
        for arm in ARMS:
            runs.append(run_search(arm,seed,requirements,cache))
    result = audit(runs)
    result["wall_seconds"] = time.monotonic()-start
    save(PAPER / "data/grammar-ablation.json",result)
    save(HERE / "progress.json", {"status":"complete","utc":now(),"wall_seconds":result["wall_seconds"]})
    print(json.dumps({"status":"complete","arms":result["arms"],"wall_seconds":result["wall_seconds"]}),flush=True)


if __name__ == "__main__":
    main()
