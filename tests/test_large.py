"""Check the frozen catalog and source materialisation boundary."""
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


class LargeSourcesTest(unittest.TestCase):
    def setUp(self):
        lock_directory = tempfile.TemporaryDirectory()
        self.addCleanup(lock_directory.cleanup)
        environment = patch.dict(os.environ, {"LCO_EMISSION_LOCK": str(Path(lock_directory.name) / "emission.lock")})
        environment.start()
        self.addCleanup(environment.stop)

    def fake_compiler(self, directory, *, mutate_evaluator=False, score_in_manifest=False):
        directory = Path(directory)
        emitter = f'''#!{sys.executable}
from pathlib import Path
counter = Path('call-count')
counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else '1')
Path('ops.bin').write_bytes(b'fresh-fixture')
'''
        evaluator = f'''#!{sys.executable}
from pathlib import Path
import json
Path('score.json').write_text(json.dumps({{'score': 1, 'metrics': {{'qubits': 1, 'toffoli': 1}}}}))
counter = Path('eval-call-count')
counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else '1')
print('tested shots : 9024\\nclassical mismatches : 0\\nphase-garbage batches : 0\\nancilla-garbage batches : 0')
'''
        if score_in_manifest:
            evaluator = evaluator.replace("Path('score.json')", "(Path(__MANIFEST_DIR__) / 'score.json')")
            evaluator += "(Path(__MANIFEST_DIR__) / 'results.tsv').write_text('one trusted run\\n')\n"
        if mutate_evaluator:
            evaluator += "Path(__file__).write_text(Path(__file__).read_text() + '\\n# changed\\n')\n"
        cargo = directory / "fake-cargo"
        cargo.write_text(f'''#!{sys.executable}
from pathlib import Path
import os
folder = Path(os.environ['CARGO_TARGET_DIR']) / 'release'
folder.mkdir(parents=True, exist_ok=True)
for name, content in [('build_circuit', {emitter!r}), ('eval_circuit', {evaluator!r})]:
    path = folder / name
    path.write_text(content.replace('__MANIFEST_DIR__', repr(str(Path.cwd()))))
    path.chmod(0o755)
''')
        cargo.chmod(0o755)
        return patch.dict(os.environ, {"LCO_CARGO": str(cargo),
                                     "LCO_CARGO_HOME": str(directory / "cargo-cache"),
                                     "LCO_TARGET_DIR": str(directory / "target")})

    def backend(self):
        spec = importlib.util.find_spec("llm_circuit_optimiser.large")
        self.assertIsNotNone(spec, "The frozen large backend is missing")
        return importlib.import_module("llm_circuit_optimiser.large")

    def test_frozen_catalog_contains_exact_fourteen_coordinates(self):
        large = self.backend()
        rows = [row for row in large.targets() if row["frozen_pareto"]]
        self.assertEqual([row["Q"] for row in rows],
                         [799, 800, 801, 802, 803, 808, 809, 810,
                          811, 812, 813, 814, 816, 839])
        self.assertTrue(all(len(row["expected_ops_sha256"]) == 64 for row in rows))
        self.assertTrue(all(row["historical_policy"] == "mfsur" for row in rows))

    def test_materialize_preserves_nonce_and_applies_counted_policy(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            receipt = large.materialize("q799", Path(directory), policy="mfsu")
            source = Path(receipt["source_dir"])
            text = (source / "src/point_add/mod.rs").read_text()
            self.assertEqual(text.count('cond_pass::run_with(ops, "mfsu")'), 1)
            self.assertIn("pub const NONCE: usize = 2;", text)
            self.assertIsNone(receipt["expected_ops_sha256"])
            self.assertEqual(receipt["policy"], "mfsu")
            self.assertTrue(receipt["source_hashes"])

    def test_materialize_detects_changed_source_before_reuse(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            receipt = large.materialize("q839", Path(directory))
            path = Path(receipt["source_dir"]) / "src/point_add/mod.rs"
            path.write_text(path.read_text().replace("TAIL_NONCE: u64 = 1", "TAIL_NONCE: u64 = 2"))
            with self.assertRaisesRegex(ValueError, "source|Source"):
                large.materialize("q839", Path(directory))

    def test_target_alias_and_policy_admission(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            receipt = large.materialize("eb015a89", Path(directory))
            self.assertEqual(receipt["target_id"], "q799")
        with self.assertRaises(ValueError):
            large.materialize("q799", Path("/unused"), policy="fault")
        with self.assertRaises(ValueError):
            large.materialize("unknown", Path("/unused"))

    def test_all_vendored_source_hashes_match(self):
        large = self.backend()
        result = large.verify_sources()
        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["targets"], 14)
        self.assertGreater(result["files"], 200)

    def test_independent_compiler_subsets_and_none_are_admitted(self):
        large = self.backend()
        self.assertEqual(len(large.POLICIES), 32)
        with tempfile.TemporaryDirectory() as directory:
            receipt = large.materialize("q839", Path(directory), policy="u")
            self.assertEqual(receipt["policy"], "u")
        with tempfile.TemporaryDirectory() as directory:
            receipt = large.materialize("q839", Path(directory), policy="none")
            self.assertEqual(receipt["policy"], "")
            text = (Path(receipt["source_dir"]) / "src/point_add/mod.rs").read_text()
            self.assertIn("ops = ops;", text)

    def test_compiler_uses_an_explicit_writable_temp_directory(self):
        large = self.backend()
        import sys
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"LCO_CARGO": sys.executable}):
                _, environment, _, _ = large._build_environment(Path(directory))
            temporary = Path(environment["TMPDIR"])
            self.assertTrue(temporary.is_dir())
            self.assertEqual(temporary.parent, Path(directory).resolve())

    def test_cargo_keeps_its_proxy_executable_name(self):
        large = self.backend()
        import sys
        with tempfile.TemporaryDirectory() as directory:
            proxy = Path(directory) / "cargo"
            proxy.symlink_to(sys.executable)
            with patch.dict("os.environ", {"LCO_CARGO": str(proxy)}):
                cargo, _, _, _ = large._build_environment(Path(directory))
            self.assertEqual(Path(cargo).name, "cargo")

    def test_leapfrog_control_keeps_the_pinned_constructor(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            receipt = large.materialize("ap1270", Path(directory), policy="none")
            self.assertEqual(receipt["family"], "qualified-leapfrog")
            self.assertEqual(receipt["expected_ops_sha256"],
                             "9271a9aa2b368b88989e905a932d5469baece5f3af00c0e928e8dcb7b9ce3c01")
            text = (Path(receipt["source_dir"]) / "src/point_add/mod.rs").read_text()
            self.assertIn("let ops = leapfrog::build();", text)
            self.assertIn('(\"HEO_PIN_PP_WALK_MAX_QUBITS\", \"1270\")', text)

    def test_leapfrog_applies_the_same_generic_compiler(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            receipt = large.materialize("ap1270", Path(directory), policy="u")
            text = (Path(receipt["source_dir"]) / "src/point_add/mod.rs").read_text()
            self.assertIn('cond_pass::run_with(leapfrog::build(), "u")', text)
            self.assertIn("mod cond_pass;", text)
            self.assertIsNone(receipt["expected_ops_sha256"])

    def test_adjacent_cancel_is_a_separate_source_recipe(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            baseline = large.materialize("q839", first)
            variant = large.materialize("q839", second, cancel_adjacent=True)
            self.assertNotEqual(baseline["target_recipe_sha256"], variant["target_recipe_sha256"])
            self.assertIsNone(variant["expected_ops_sha256"])
            text = (Path(variant["source_dir"]) / "src/point_add/mod.rs").read_text()
            self.assertIn("build_before_adjacent_cancel", text)
            self.assertIn("lco_adjacent_cancel::run", text)
            self.assertIn("split_off(tail_start)", text)
            self.assertIn("ops.extend(identity_tail)", text)
            self.assertTrue(variant["cancel_adjacent"])

    def test_each_policy_has_a_distinct_source_recipe_hash(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            u = large.materialize("q839", first, policy="u")
            r = large.materialize("q839", second, policy="r")
            self.assertNotEqual(u["target_recipe_sha256"], r["target_recipe_sha256"])

    def test_failed_build_receipt_can_retry_without_losing_evidence(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            work = directory / "work"
            large.materialize("q839", work, policy="u")
            (work / "build.json").write_text(json.dumps({"returncode": 1}))
            (work / "build.stderr.txt").write_text("interrupted compiler")
            with self.fake_compiler(directory):
                result = large.build("q839", work, policy="u")
            self.assertEqual(result["returncode"], 0)
            failures = list((work / "attempts").glob("build-*/build.stderr.txt"))
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].read_text(), "interrupted compiler")

    def test_interrupted_emission_directory_is_preserved_before_retry(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            work = directory / "work"
            large.materialize("q839", work, policy="u")
            (work / "emission").mkdir()
            (work / "emission/ops.bin").write_bytes(b"partial")
            with self.fake_compiler(directory):
                with self.assertRaisesRegex(ValueError, "start receipt"):
                    large.emit("q839", work, policy="u")
                result = large.emit("q839", work, policy="u", recover_interrupted=True)
            self.assertEqual(Path(result["ops_path"]).read_bytes(), b"fresh-fixture")
            archived = list((work / "attempts").glob("emission-*/emission/ops.bin"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_bytes(), b"partial")

    def test_finished_emission_process_recovers_without_another_generation(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            work = directory / "work"
            with self.fake_compiler(directory):
                first = large.emit("q839", work, policy="u")
                (work / "emission.json").unlink()
                recovered = large.emit("q839", work, policy="u")
            self.assertEqual(recovered["ops_sha256"], first["ops_sha256"])
            self.assertEqual((work / "emission/call-count").read_text(), "1")
            self.assertTrue(recovered["emission_recovered"])

    def test_official_evaluator_mutation_fails_the_integrity_gate(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            work = directory / "work"
            with self.fake_compiler(directory, mutate_evaluator=True):
                with self.assertRaisesRegex(ValueError, "evaluator"):
                    large.official_check("q839", work, policy="u")
            result = json.loads((work / "official.json").read_text())
            self.assertFalse(result["passed"])
            self.assertFalse(result["evaluator_unchanged_after"])

    def test_official_outputs_use_the_pinned_manifest_directory(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            work = directory / "work"
            with self.fake_compiler(directory, score_in_manifest=True):
                result = large.official_check("q839", work, policy="u")
            self.assertTrue(result["passed"])
            self.assertEqual((result["Q"], result["T"]), (1, 1))
            self.assertTrue((work / "official/score.json").is_file())
            self.assertTrue((work / "official/results.tsv").is_file())
            self.assertFalse((work / "source/score.json").exists())
            large.materialize("q839", work, policy="u")

    def test_saved_official_run_recovers_a_score_path_parse_error(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            work = directory / "work"
            with self.fake_compiler(directory, score_in_manifest=True):
                large.official_check("q839", work, policy="u")
                for name in ("score.json", "results.tsv"):
                    (work / "official" / name).rename(work / "source" / name)
                path = work / "official.json"
                wrong = json.loads(path.read_text())
                wrong.update(passed=False, Q=None, T=None, score=None, qubit_count=None, toffoli_count=None)
                path.write_text(json.dumps(wrong))
                recovered = large.official_check("q839", work, policy="u")
            self.assertTrue(recovered["passed"])
            self.assertTrue(recovered["official_receipt_recovered"])
            self.assertEqual((work / "official/eval-call-count").read_text(), "1")
            self.assertEqual(len(list((work / "attempts").glob("official-parse-*/official.json"))), 1)

    def test_known_artifact_recovers_from_legacy_timer_failure(self):
        large = self.backend()
        import hashlib
        target = large._target("q839")
        target["expected_ops_sha256"] = hashlib.sha256(b"fresh-fixture").hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            work = directory / "work"
            with self.fake_compiler(directory), patch.object(large, "_target", return_value=target):
                large.emit("q839", work)
                path = work / "emission.json"
                failed = json.loads(path.read_text())
                failed.update(schema="large-emission-v1", returncode=1)
                path.write_text(json.dumps(failed))
                with (work / "emission.stderr.txt").open("a") as stream:
                    stream.write("time: sysctl kern.clockrate: Operation not permitted\n")
                result = large.emit("q839", work)
            self.assertTrue(result["artifact_validated"])
            self.assertTrue(result["emission_recovered"])
            self.assertEqual(result["timing_process_returncode"], 1)
            self.assertEqual((work / "emission/call-count").read_text(), "1")

    def test_cached_emission_does_not_wait_for_the_host_lock(self):
        large = self.backend()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            with self.fake_compiler(directory):
                large.emit("q839", directory / "work", policy="u")
                with patch.object(large, "emission_lock", side_effect=AssertionError("cache acquired host lock")):
                    result = large.emit("q839", directory / "work", policy="u")
            self.assertTrue(result["emission_reused"])

    def test_public_emission_lock_uses_the_configured_path(self):
        large = self.backend()
        with large.emission_lock() as lock:
            self.assertEqual(Path(lock.name), Path(os.environ["LCO_EMISSION_LOCK"]))

    def test_cancellation_body_preserves_inherited_identity_tails(self):
        large = self.backend()
        compiler = os.environ.get("LCO_RUSTC") or shutil.which("rustc")
        if not compiler and os.environ.get("LCO_CARGO"):
            candidate = Path(os.environ["LCO_CARGO"]).parent / "rustc"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            self.skipTest("Set LCO_RUSTC to test the emitted Rust boundary")
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / "boundary.rs"
            code = '#![allow(dead_code)]\n'
            code += '#[path = ' + json.dumps(str(large.ROOT / "vendor/benchmark/src/circuit.rs"), ensure_ascii=False) + '] mod circuit;\n'
            code += '#[path = ' + json.dumps(str(large.ROOT / "vendor/grammar_passes/adjacent_cancel.rs"), ensure_ascii=False) + '] mod lco_adjacent_cancel;\n'
            code += '''
fn original() -> Vec<circuit::Op> {
    use circuit::{Op, OperationType as K, QubitId};
    let mut cx = Op::empty(); cx.kind = K::CX; cx.q_control1 = QubitId(0); cx.q_target = QubitId(1);
    let mut barrier = Op::empty(); barrier.kind = K::R; barrier.q_target = QubitId(2);
    let mut x0 = Op::empty(); x0.kind = K::X; x0.q_target = QubitId(0);
    let mut x1 = x0; x1.q_target = QubitId(1);
    vec![cx, cx, barrier, x0, x0, x1, x1]
}
fn wrapped() -> Vec<circuit::Op> {
'''
            code += large.cancellation_body("original()")
            code += '''}
fn main() {
    let before = original(); let after = wrapped();
    assert_eq!(after, before[2..].to_vec());
    assert_eq!(after.len(), 5);
}
'''
            source.write_text(code)
            binary = directory / "boundary"
            environment = dict(os.environ, TMPDIR=str(directory))
            built = subprocess.run([compiler, "--edition=2021", str(source), "-o", str(binary)],
                                   env=environment, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("removed_pairs=1 protected_tail_ops=4", result.stderr)


if __name__ == "__main__":
    unittest.main()
