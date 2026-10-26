"""Rebuild pinned optimized constructors with the fixed public evaluator."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from itertools import combinations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
POLICIES = frozenset("".join(parts) for size in range(6)
                     for parts in combinations("mfsur", size))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


@contextmanager
def _exclusive(path: Path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield lock


@contextmanager
def emission_lock():
    """Serialize genuine large emitters through the configured host lock."""
    path = Path(os.environ.get("LCO_EMISSION_LOCK", str(Path(tempfile.gettempdir()) / "lco-large-emission.lock")))
    with _exclusive(path) as lock:
        yield lock


def _preserve_attempt(workdir: Path, stage: str, names: tuple[str, ...], reason: str) -> Path:
    attempts = workdir / "attempts"
    attempts.mkdir(exist_ok=True)
    number = 1
    while (attempts / f"{stage}-{number:04d}").exists():
        number += 1
    destination = attempts / f"{stage}-{number:04d}"
    destination.mkdir()
    for name in names:
        source = workdir / name
        if source.exists():
            shutil.move(str(source), destination / name)
    _save(destination / "recovery.json", {"reason": reason, "preserved_sha256": _hashes(destination)})
    return destination


def _validate_bound_artifacts(built: dict) -> None:
    if _hashes(Path(built["source_dir"])) != built["source_hashes"]:
        raise ValueError("The materialized source changed during construction")
    for name in ("build_binary", "eval_binary"):
        if _sha(Path(built[name])) != built[name + "_sha256"]:
            raise ValueError(f"The bound {'evaluator' if name == 'eval_binary' else 'emitter'} changed")


def targets() -> list[dict]:
    """Return frozen target recipes, with any separately marked family controls."""
    return _read(ROOT / "data/targets/catalog.json")["targets"]


def _target(target_id: str) -> dict:
    matches = [row for row in targets() if target_id in (row["id"], row.get("candidate_id"))
               or (len(target_id) >= 8 and row["submission_id"].startswith(target_id))]
    if len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous target: {target_id}")
    return matches[0]


def _hashes(directory: Path) -> dict[str, str]:
    return {str(path.relative_to(directory)): _sha(path)
            for path in sorted(directory.rglob("*")) if path.is_file()}


def _verify_vendor(directory: Path) -> dict:
    manifest = _read(directory / "manifest.json")
    actual = {name: digest for name, digest in _hashes(directory).items()
              if name != "manifest.json"}
    if actual != manifest["files_sha256"]:
        raise ValueError(f"Vendored source differs from its manifest: {directory}")
    return manifest


def verify_sources() -> dict:
    """Verify every source closure without an old checkout or network access."""
    fixed = _verify_vendor(ROOT / "vendor/benchmark")
    count = len(fixed["files_sha256"])
    rows = targets()
    for row in rows:
        directory = ROOT / row["vendor_source"]
        if _sha(directory / "manifest.json") != row["source_manifest_sha256"]:
            raise ValueError(f"Source manifest changed: {row['id']}")
        count += len(_verify_vendor(directory)["files_sha256"])
    return {"passed": True, "targets": len(rows), "files": count,
            "catalog_sha256": _sha(ROOT / "data/targets/catalog.json")}


def cancellation_body(expression: str, module_name: str = "lco_adjacent_cancel") -> str:
    """Return the shared wrapper body that preserves inherited X/X tails."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", module_name):
        raise ValueError("The cancellation module name must be a Rust identifier")
    return f'''    let mut original_ops = {expression};
    let mut tail_start = original_ops.len();
    while tail_start >= 2 {{
        let a = &original_ops[tail_start - 2];
        let b = &original_ops[tail_start - 1];
        let identity_pair = a.kind == crate::circuit::OperationType::X
            && b.kind == crate::circuit::OperationType::X
            && a.q_target == b.q_target
            && a.c_condition == crate::circuit::NO_BIT
            && b.c_condition == crate::circuit::NO_BIT;
        if !identity_pair {{ break; }}
        tail_start -= 2;
    }}
    let identity_tail = original_ops.split_off(tail_start);
    let protected_tail_ops = identity_tail.len();
    let (mut ops, removed_pairs) = {module_name}::run(original_ops);
    ops.extend(identity_tail);
    eprintln!("LCO_ADJACENT_CANCEL removed_pairs={{removed_pairs}} protected_tail_ops={{protected_tail_ops}}");
    ops
'''


def materialize(target_id: str, workdir: str | Path, policy: str = "mfsur", *,
                cancel_adjacent: bool = False) -> dict:
    """Copy a verified closure and apply its single admitted flag replacement."""
    policy = "" if policy == "none" else policy
    if policy not in POLICIES:
        raise ValueError(f"Unsupported compiler policy: {policy}")
    target = _target(target_id)
    recipe = (target if policy == target["historical_policy"] and not cancel_adjacent else
              {"target": target, "policy": policy, "cancel_adjacent": cancel_adjacent})
    target_hash = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    workdir = Path(workdir).resolve()
    source = workdir / "source"
    receipt_path = workdir / "materialization.json"
    if receipt_path.exists():
        receipt = _read(receipt_path)
        if receipt["target_id"] != target["id"] or receipt["policy"] != policy:
            raise ValueError("The work directory belongs to another source recipe")
        if receipt.get("cancel_adjacent", False) != cancel_adjacent:
            raise ValueError("The work directory belongs to another cancellation recipe")
        if _hashes(source) != receipt["source_hashes"]:
            raise ValueError("The materialized source changed after its receipt")
        if receipt["target_recipe_sha256"] != target_hash:
            raise ValueError("The source recipe changed after materialization")
        return receipt
    if source.exists():
        raise ValueError("The source directory has no materialization receipt")
    fixed_dir = ROOT / "vendor/benchmark"
    family_dir = ROOT / target["vendor_source"]
    fixed = _verify_vendor(fixed_dir)
    if _sha(family_dir / "manifest.json") != target["source_manifest_sha256"]:
        raise ValueError("The target source manifest changed")
    family = _verify_vendor(family_dir)
    source.mkdir(parents=True)
    for directory, manifest in ((fixed_dir, fixed), (family_dir, family)):
        for relative in manifest["files_sha256"]:
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / relative, destination)
    module = source / "src/point_add/mod.rs"
    text = module.read_text()
    old = target["edit"]["old"]
    wrapper = target["edit"].get("kind") == "constructor-wrapper"
    if wrapper:
        new = f'let ops = cond_pass::run_with(leapfrog::build(), "{policy}");' if policy else old
    else:
        new = f'cond_pass::run_with(ops, "{policy}")' if policy else "ops"
    if text.count(old) != 1:
        raise ValueError("The parent source lacks the single expected policy setting")
    text = text.replace(old, new)
    if wrapper:
        text = text.replace("#[allow(dead_code)]", '#[path = "generic_conditional.rs"]\nmod cond_pass;\n#[allow(dead_code)]', 1)
    added_source = None
    if cancel_adjacent:
        cancellation = ROOT / "vendor/grammar_passes/adjacent_cancel.rs"
        added_source = {"source": "vendor/grammar_passes/adjacent_cancel.rs", "sha256": _sha(cancellation)}
        shutil.copyfile(cancellation, module.parent / "lco_adjacent_cancel.rs")
        text, replaced = re.subn(r"pub fn build\(\)(\s*->)", r"fn build_before_adjacent_cancel()\1", text)
        if replaced != 1:
            raise ValueError("The constructor lacks a single public build entry")
        text += '\n#[path = "lco_adjacent_cancel.rs"]\nmod lco_adjacent_cancel;\n'
        text += 'pub fn build() -> Vec<crate::circuit::Op> {\n'
        text += cancellation_body("build_before_adjacent_cancel()") + '}\n'
    module.write_text(text)
    historical = policy == target["historical_policy"] and not cancel_adjacent
    receipt = {"schema": "large-materialization-v1", "target_id": target["id"],
               "family": target["family"], "policy": policy, "source_dir": str(source),
               "cancel_adjacent": cancel_adjacent, "added_source": added_source,
               "parent_commit": target["parent_commit"], "source_hashes": _hashes(source),
               "catalog_sha256": _sha(ROOT / "data/targets/catalog.json"),
               "target_recipe_sha256": target_hash,
               "source_manifest_sha256": target["source_manifest_sha256"],
               "edit": {"file": "src/point_add/mod.rs", "old": old, "new": new, "count": 1},
               "expected_ops_sha256": target["expected_ops_sha256"] if historical else None,
               "expected_Q": target["Q"] if historical else None,
               "expected_T": target["T"] if historical else None,
               "expected_score": target["S"] if historical else None,
               "scope": "Frozen source replay. No rediscovery claim."}
    _save(receipt_path, receipt)
    return receipt


def _build_environment(workdir: Path) -> tuple[str, dict, Path, bool]:
    workdir = workdir.resolve()
    configured = os.environ.get("LCO_CARGO") or shutil.which("cargo")
    if not configured:
        raise RuntimeError("Cargo is unavailable. Set LCO_CARGO to its executable.")
    cargo = Path(os.path.abspath(os.path.expanduser(configured)))
    if not cargo.is_file():
        raise RuntimeError(f"Cargo executable does not exist: {cargo}")
    environment = {"PATH": f"{cargo.parent}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    temporary = workdir / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment["TMPDIR"] = str(temporary)
    rustc = cargo.parent / "rustc"
    if rustc.exists():
        environment["RUSTC"] = str(rustc)
    for source, destination in (("LCO_CARGO_HOME", "CARGO_HOME"),
                                ("RUSTUP_HOME", "RUSTUP_HOME")):
        if os.environ.get(source):
            environment[destination] = str(Path(os.environ[source]).expanduser().resolve())
    if "CARGO_HOME" not in environment and os.environ.get("CARGO_HOME"):
        environment["CARGO_HOME"] = os.environ["CARGO_HOME"]
    if os.environ.get("HOME"):
        environment["HOME"] = os.environ["HOME"]
    target_dir = Path(os.environ.get("LCO_TARGET_DIR", str(workdir / "target"))).expanduser().resolve()
    environment["CARGO_TARGET_DIR"] = str(target_dir)
    cache = Path(environment.get("CARGO_HOME", str(Path.home() / ".cargo")))
    return str(cargo), environment, target_dir, (cache / "registry").is_dir()


def build(target_id: str, workdir: str | Path, policy: str = "mfsur", offline: bool = False, *,
          cancel_adjacent: bool = False) -> dict:
    """Compile a pinned emitter and candidate-free official evaluator."""
    workdir = Path(workdir).resolve()
    with _exclusive(workdir / ".build.lock"):
        return _build_unlocked(target_id, workdir, policy, offline, cancel_adjacent=cancel_adjacent)


def _build_unlocked(target_id: str, workdir: Path, policy: str, offline: bool, *,
                    cancel_adjacent: bool) -> dict:
    materialized = materialize(target_id, workdir, policy, cancel_adjacent=cancel_adjacent)
    workdir = Path(workdir).resolve()
    receipt_path = workdir / "build.json"
    if receipt_path.exists():
        receipt = _read(receipt_path)
        if receipt["returncode"] != 0:
            _preserve_attempt(workdir, "build", ("build.json", "build.stdout.txt", "build.stderr.txt"),
                              "Retry of a failed compiler process; the source recipe is unchanged.")
        else:
            if all(Path(receipt[name]).is_file() and _sha(Path(receipt[name])) == receipt[name + "_sha256"]
                   for name in ("build_binary", "eval_binary")):
                return {**materialized, **receipt}
            raise ValueError("The compiled source artifacts changed")
    elif any((workdir / name).exists() for name in ("build.stdout.txt", "build.stderr.txt")):
        _preserve_attempt(workdir, "build", ("build.stdout.txt", "build.stderr.txt"),
                          "Interrupted compiler process without a completion receipt.")
    cargo, environment, target_dir, cached = _build_environment(workdir)
    argv = [cargo, "build", "--release", "--locked", "--jobs", "1",
            "--bin", "build_circuit", "--bin", "eval_circuit"]
    if offline or cached:
        argv.append("--offline")
        environment["CARGO_NET_OFFLINE"] = "true"
    target_dir.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (target_dir / ".lco-build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        start = time.monotonic()
        with (workdir / "build.stdout.txt").open("w") as stdout, (workdir / "build.stderr.txt").open("w") as stderr:
            done = subprocess.run(argv, cwd=materialized["source_dir"], env=environment,
                                  stdout=stdout, stderr=stderr, text=True)
        receipt = {"argv": argv, "returncode": done.returncode,
                   "wall_seconds": time.monotonic() - start, "cargo_sha256": _sha(Path(cargo))}
        if done.returncode == 0:
            binaries = workdir / "binaries"
            binaries.mkdir(exist_ok=True)
            for name, executable in (("build_binary", "build_circuit"), ("eval_binary", "eval_circuit")):
                destination = binaries / executable
                shutil.copy2(target_dir / "release" / executable, destination)
                receipt[name] = str(destination)
                receipt[name + "_sha256"] = _sha(destination)
        _save(receipt_path, receipt)
    if done.returncode:
        raise RuntimeError(f"Large constructor build failed: {(workdir / 'build.stderr.txt').read_text()[-2000:]}")
    return {**materialized, **receipt}


def _rewrite_counts(stderr: str) -> dict:
    counts = dict.fromkeys(("m", "f", "s", "u", "r", "r_ccx", "adjacent_cancel"), 0)
    first = re.search(r"SL_COND m: (\d+).*?; u: (\d+).*?; f: (\d+)", stderr)
    if first:
        counts.update(zip(("m", "u", "f"), map(int, first.groups())))
    for key, pattern in (("s", r"SL_COND s: (\d+)"),
                         ("r", r"SL_COND r: (\d+) absorbed windows"),
                         ("r_ccx", r"absorbed windows \(reset or measured end\), (\d+) CCX"),
                         ("adjacent_cancel", r"LCO_ADJACENT_CANCEL removed_pairs=(\d+)")):
        found = re.search(pattern, stderr)
        counts[key] = int(found[1]) if found else 0
    return counts


def emit(target_id: str, workdir: str | Path, policy: str = "mfsur", offline: bool = False, *,
         cancel_adjacent: bool = False, recover_interrupted: bool = False) -> dict:
    """Generate fresh operations and compare their frozen historical hash."""
    workdir = Path(workdir).resolve()
    with _exclusive(workdir / ".emission.lock") as lock:
        return _emit_unlocked(target_id, workdir, policy, offline,
                              cancel_adjacent=cancel_adjacent, recover_interrupted=recover_interrupted,
                              work_lock=lock)


def _emit_unlocked(target_id: str, workdir: Path, policy: str, offline: bool, *,
                   cancel_adjacent: bool, recover_interrupted: bool, work_lock) -> dict:
    built = build(target_id, workdir, policy, offline, cancel_adjacent=cancel_adjacent)
    receipt_path = workdir / "emission.json"
    if receipt_path.exists():
        receipt = _read(receipt_path)
        timing_error = (receipt.get("schema") == "large-emission-v1" and receipt.get("returncode") == 1
                        and built["expected_ops_sha256"] is not None
                        and receipt.get("ops_sha256") == built["expected_ops_sha256"]
                        and (workdir / "emission.stderr.txt").exists()
                        and "time: sysctl kern.clockrate: Operation not permitted" in
                        (workdir / "emission.stderr.txt").read_text())
        if (receipt.get("returncode") == 0 or timing_error) and receipt.get("ops_sha256"):
            if _sha(Path(receipt["ops_path"])) != receipt["ops_sha256"]:
                raise ValueError("The emitted source artifact changed")
            if built["expected_ops_sha256"] and receipt["ops_sha256"] != built["expected_ops_sha256"]:
                raise ValueError("The cached operations differ from the frozen target")
            if receipt["emitter_binary_sha256"] != built["build_binary_sha256"]:
                raise ValueError("The cached emission belongs to another emitter")
            _validate_bound_artifacts(built)
            if timing_error:
                recovery = {"schema": "timing-recovery-v1", "timing_process_returncode": 1,
                            "emission_recovered": True, "artifact_validated": True,
                            "ops_sha256": receipt["ops_sha256"],
                            "original_receipt_sha256": _sha(receipt_path),
                            "source_hashes": built["source_hashes"],
                            "scope": "The fresh artifact matches the frozen target. Only the timing wrapper failed; no regeneration occurred."}
                _save(workdir / "emission-timing-recovery.json", recovery)
                receipt = {**receipt, **recovery}
            return {**built, **receipt, "emission_reused": True}
        _preserve_attempt(workdir, "emission", _EMISSION_FILES,
                          "Retry of an unscored failed emitter process.")
    folder = workdir / "emission"
    if folder.exists():
        process_path = workdir / "emission-process.json"
        start_path = workdir / "emission-start.json"
        if not start_path.exists() and not recover_interrupted:
            raise ValueError("The emission directory has no start receipt. Confirm its process ended, then use recover_interrupted=True.")
        if process_path.exists() and start_path.exists():
            process = _read(process_path)
            started = _read(start_path)
            if (process.get("returncode") == 0 and process["emission_id"] == started["emission_id"]
                    and started["emitter_binary_sha256"] == built["build_binary_sha256"]
                    and process["emitter_binary_sha256"] == built["build_binary_sha256"]
                    and started["target_recipe_sha256"] == built["target_recipe_sha256"]
                    and started["source_hashes"] == built["source_hashes"]):
                return _finish_emission(built, workdir, process, recovered=True)
        _preserve_attempt(workdir, "emission", _EMISSION_FILES,
                          "Interrupted emission without a successful bound process receipt.")
    folder.mkdir()
    argv = [built["build_binary"]]
    start = time.monotonic()
    started = {"emission_id": str(uuid.uuid4()), "argv": argv,
               "emitter_binary_sha256": built["build_binary_sha256"],
               "target_recipe_sha256": built["target_recipe_sha256"],
               "source_hashes": built["source_hashes"], "started_unix": time.time()}
    _save(workdir / "emission-start.json", started)
    with emission_lock() as global_lock:
        execution_start = time.monotonic()
        with (workdir / "emission.stdout.txt").open("w") as stdout, (workdir / "emission.stderr.txt").open("w") as stderr:
            child = subprocess.Popen(argv, cwd=folder, env={"PATH": "/usr/bin:/bin"},
                                     stdout=stdout, stderr=stderr,
                                     pass_fds=(work_lock.fileno(), global_lock.fileno()))
            started["pid"] = child.pid
            _save(workdir / "emission-start.json", started)
            _, status, usage = os.wait4(child.pid, 0)
            returncode = os.waitstatus_to_exitcode(status)
            child.returncode = returncode
    process = {"emission_id": started["emission_id"], "pid": started["pid"],
               "returncode": returncode, "wall_seconds": time.monotonic() - execution_start,
               "queue_wait_seconds": execution_start - start,
               "peak_rss_bytes": int(usage.ru_maxrss) * (1 if sys.platform == "darwin" else 1024),
               "emitter_binary_sha256": built["build_binary_sha256"],
               "stdout_sha256": _sha(workdir / "emission.stdout.txt"),
               "stderr_sha256": _sha(workdir / "emission.stderr.txt")}
    _save(workdir / "emission-process.json", process)
    return _finish_emission(built, workdir, process, recovered=False)


_EMISSION_FILES = ("emission", "emission.json", "emission-start.json", "emission-process.json",
                   "emission.stdout.txt", "emission.stderr.txt", "emission-timing-recovery.json")


def _finish_emission(built: dict, workdir: Path, process: dict, *, recovered: bool) -> dict:
    _validate_bound_artifacts(built)
    for stream in ("stdout", "stderr"):
        if _sha(workdir / f"emission.{stream}.txt") != process[f"{stream}_sha256"]:
            raise ValueError("The emitter process log changed before recovery")
    stderr = (workdir / "emission.stderr.txt").read_text()
    folder = workdir / "emission"
    ops = folder / "ops.bin"
    memory = re.search(r"(\d+)\s+maximum resident set size", stderr)
    receipt = {"schema": "large-emission-v2", "returncode": process["returncode"],
               "wall_seconds": process["wall_seconds"], "ops_path": str(ops),
               "queue_wait_seconds": process.get("queue_wait_seconds", 0),
               "ops_sha256": _sha(ops) if ops.exists() else None,
               "ops_bytes": ops.stat().st_size if ops.exists() else None,
               "rewrite_counts": _rewrite_counts(stderr),
               "emission_id": process["emission_id"], "generated_here": True,
               "emission_reused": recovered, "emission_recovered": recovered,
               "emitter_binary_sha256": built["build_binary_sha256"],
               "source_integrity_after": True, "binary_integrity_after": True,
               "peak_rss_bytes": process.get("peak_rss_bytes", int(memory[1]) if memory else None),
               "emission_environment": {"PATH": "/usr/bin:/bin"}}
    receipt["historical_hash_matches"] = (receipt["ops_sha256"] == built["expected_ops_sha256"]
                                           if built["expected_ops_sha256"] else None)
    _save(workdir / "emission.json", receipt)
    if process["returncode"]:
        raise RuntimeError(f"Large emission failed: {stderr[-2000:]}")
    if not ops.is_file():
        raise RuntimeError("The emitter completed without an operation artifact")
    if receipt["historical_hash_matches"] is False:
        raise ValueError(f"Regenerated operation hash differs for {built['target_id']}")
    return {**built, **receipt}


def _collect_official_outputs(workdir: Path) -> None:
    """Retain the fixed evaluator's manifest-relative outputs as run evidence."""
    folder = workdir / "official"
    for name in ("score.json", "results.tsv"):
        source = workdir / "source" / name
        if not source.exists():
            continue
        folder.mkdir(exist_ok=True)
        destination = folder / name
        if destination.exists():
            if _sha(source) == _sha(destination):
                source.unlink()
                continue
            backups = folder / "previous-outputs"
            backups.mkdir(exist_ok=True)
            number = 1
            while (backups / f"{number:04d}-{name}").exists():
                number += 1
            shutil.move(str(destination), backups / f"{number:04d}-{name}")
        shutil.move(str(source), destination)


def official_check(target_id: str, workdir: str | Path, policy: str = "mfsur", offline: bool = False, *,
                   cancel_adjacent: bool = False, recover_interrupted: bool = False) -> dict:
    """Run the unchanged public evaluator on this emitted byte stream once."""
    workdir = Path(workdir).resolve()
    receipt_path = workdir / "official.json"
    if receipt_path.exists():
        _collect_official_outputs(workdir)
    emitted = emit(target_id, workdir, policy, offline, cancel_adjacent=cancel_adjacent,
                   recover_interrupted=recover_interrupted)
    evaluator = Path(emitted["eval_binary"])
    before_hash = _sha(evaluator)
    if before_hash != emitted["eval_binary_sha256"]:
        raise ValueError("The official evaluator changed before its invocation")
    if receipt_path.exists():
        receipt = _read(receipt_path)
        if receipt["ops_sha256"] != emitted["ops_sha256"]:
            raise ValueError("The official receipt belongs to different operations")
        if receipt["eval_binary_sha256"] != before_hash:
            raise ValueError("The official receipt belongs to a different evaluator")
        for stream in ("stdout", "stderr"):
            expected = receipt.get(f"{stream}_sha256")
            if expected and _sha(workdir / f"official.{stream}.txt") != expected:
                raise ValueError("The official output log changed after its receipt")
        score_path = workdir / "official/score.json"
        counts = receipt["counts"]
        if (receipt.get("score") is None and receipt["returncode"] == 0 and score_path.exists()
                and receipt.get("evaluator_unchanged_after") is True and counts["shots"] == 9024
                and all(value == 0 for key, value in counts.items() if key != "shots")):
            score = _read(score_path)
            old_hash = _sha(receipt_path)
            _preserve_attempt(workdir, "official-parse", ("official.json",),
                              "Recovered the saved successful run's manifest-relative score. No evaluator rerun.")
            metrics = score["metrics"]
            receipt.update({"Q": metrics["qubits"], "T": metrics["toffoli"], "score": score["score"],
                            "qubit_count": metrics["qubits"], "toffoli_count": metrics["toffoli"],
                            "passed": True, "official_receipt_recovered": True,
                            "original_receipt_sha256": old_hash, "score_json_sha256": _sha(score_path),
                            "stdout_sha256": _sha(workdir / "official.stdout.txt"),
                            "stderr_sha256": _sha(workdir / "official.stderr.txt")})
            receipt["historical_counts_match"] = (receipt["Q"] == emitted["expected_Q"]
                                                   and receipt["T"] == emitted["expected_T"]
                                                   if emitted["expected_ops_sha256"] else None)
            _save(receipt_path, receipt)
        if receipt.get("score_json_sha256") and _sha(score_path) != receipt["score_json_sha256"]:
            raise ValueError("The official score file changed after its receipt")
        return {**emitted, **receipt}
    folder = workdir / "official"
    if folder.exists():
        raise ValueError("The previous official invocation is incomplete; preserve its evidence")
    folder.mkdir()
    (folder / "ops.bin").symlink_to(emitted["ops_path"])
    start = time.monotonic()
    done = subprocess.run([emitted["eval_binary"]], cwd=folder,
                          env={"PATH": "/usr/bin:/bin", "RAYON_NUM_THREADS": "4", "LANG": "C", "LC_ALL": "C"},
                          capture_output=True, text=True)
    (workdir / "official.stdout.txt").write_text(done.stdout)
    (workdir / "official.stderr.txt").write_text(done.stderr)
    after_hash = _sha(evaluator)
    evaluator_unchanged = after_hash == before_hash
    _collect_official_outputs(workdir)
    score = _read(folder / "score.json") if (folder / "score.json").exists() else None
    text = done.stdout + done.stderr
    def number(label):
        values = re.findall(re.escape(label) + r"\s*:\s*(\d+)", text)
        return int(values[-1]) if values else None
    counts = {"shots": number("tested shots"), "classical_mismatches": number("classical mismatches"),
              "phase_garbage_batches": number("phase-garbage batches"),
              "ancilla_garbage_batches": number("ancilla-garbage batches")}
    passed = (evaluator_unchanged and done.returncode == 0 and score is not None and counts["shots"] == 9024
              and all(value == 0 for key, value in counts.items() if key != "shots"))
    metrics = score["metrics"] if score else {}
    receipt = {"schema": "large-official-v1", "ops_sha256": emitted["ops_sha256"],
               "eval_binary_sha256": emitted["eval_binary_sha256"], "returncode": done.returncode,
               "evaluator_before_sha256": before_hash, "evaluator_after_sha256": after_hash,
               "evaluator_unchanged_after": evaluator_unchanged,
               "wall_seconds": time.monotonic() - start, "counts": counts, "passed": passed,
               "Q": metrics.get("qubits"), "T": metrics.get("toffoli"),
               "qubit_count": metrics.get("qubits"), "toffoli_count": metrics.get("toffoli"),
               "score": score.get("score") if score else None,
               "score_json_sha256": _sha(folder / "score.json") if score else None,
               "stdout_sha256": _sha(workdir / "official.stdout.txt"),
               "stderr_sha256": _sha(workdir / "official.stderr.txt"),
               "scope": "Reproduced fixed public packet. Fresh reliability requires a separate audit."}
    receipt["historical_counts_match"] = (receipt["Q"] == emitted["expected_Q"]
                                           and receipt["T"] == emitted["expected_T"]
                                           if emitted["expected_ops_sha256"] else None)
    _save(receipt_path, receipt)
    if not evaluator_unchanged:
        raise ValueError("The official evaluator changed during its invocation")
    return {**emitted, **receipt}
