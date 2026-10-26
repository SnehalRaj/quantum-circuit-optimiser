"""Check composition and admission at the executable grammar boundary."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GrammarTests(unittest.TestCase):
    def setUp(self):
        from llm_circuit_optimiser.grammar import compose
        self.compose = compose
        self.base = ROOT / "grammars/base.json"
        self.froghop = ROOT / "grammars/families/froghop.json"

    def test_composition_order_and_duplicate_inputs_do_not_change_hash(self):
        a = self.compose([self.base, self.froghop])
        b = self.compose([self.froghop, self.base, self.base])
        self.assertEqual(a.sha256, b.sha256)
        self.assertEqual(a.to_dict()["rules"], b.to_dict()["rules"])

    def write_bundle(self, directory, grammar, *, availability=True):
        raw = grammar.to_dict()
        if availability:
            raw["availability"] = grammar.availability("froghop")
        path = Path(directory) / "combined.json"
        path.write_text(json.dumps(raw))
        return path

    def rehash_bundle(self, raw):
        content = {k: v for k, v in raw.items() if k not in {"sha256", "availability"}}
        raw["sha256"] = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"),
                                                     ensure_ascii=False).encode()).hexdigest()

    def test_compiled_bundle_is_a_reusable_search_grammar(self):
        from llm_circuit_optimiser.cli import parser, resolve
        with tempfile.TemporaryDirectory() as tmp:
            original = self.compose([self.base, self.froghop])
            bundle = self.write_bundle(tmp, original)
            loaded = self.compose([bundle])
            self.assertEqual(loaded.sha256, original.sha256)
            self.assertEqual(loaded.to_dict(), original.to_dict())
            args = parser().parse_args(["search", "--grammar", str(bundle), "--targets", "q839", "--out", str(Path(tmp) / "run")])
            family, grammar, _, targets = resolve(args)
            self.assertEqual(grammar.sha256, original.sha256)
            self.assertEqual(targets, ["q839"])
            self.assertEqual(grammar.candidate_for_policy("ur", "q839", family, cancel_adjacent=True)["policy"], "ur")

    def test_compiled_bundle_combines_with_raw_addenda_and_duplicate_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = self.compose([self.base, self.froghop])
            bundle = self.write_bundle(tmp, original)
            addendum = ROOT / "grammars/families/qualified-leapfrog.json"
            loaded = self.compose([bundle, self.base, bundle, addendum])
            expected = self.compose([self.base, self.froghop, addendum])
            self.assertEqual(loaded.to_dict(), expected.to_dict())

    def test_compiled_bundle_rejects_changed_content_before_search(self):
        from llm_circuit_optimiser.grammar import GrammarError
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_bundle(tmp, self.compose([self.base, self.froghop]))
            raw = json.loads(path.read_text())
            raw["rules"][0]["description"] += " Changed after composition."
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(GrammarError, "hash"):
                self.compose([path])

    def test_compiled_bundle_revalidates_rules_and_origin_references(self):
        from llm_circuit_optimiser.grammar import GrammarError
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_bundle(tmp, self.compose([self.base]))
            original = json.loads(path.read_text())
            invalid_rule = copy.deepcopy(original)
            invalid_rule["rules"][0]["revision"] = -1
            self.rehash_bundle(invalid_rule)
            path.write_text(json.dumps(invalid_rule))
            with self.assertRaisesRegex(GrammarError, "revision"):
                self.compose([path])
            invalid_origin = copy.deepcopy(original)
            rid = next(iter(invalid_origin["source_map"]))
            invalid_origin["source_map"][rid][0]["sha256"] = "0" * 64
            self.rehash_bundle(invalid_origin)
            path.write_text(json.dumps(invalid_origin))
            with self.assertRaisesRegex(GrammarError, "origin"):
                self.compose([path])

    def test_compiled_bundle_availability_never_grants_a_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = self.compose([self.base, self.froghop])
            path = self.write_bundle(tmp, original)
            raw = json.loads(path.read_text())
            raw["availability"]["active"].append("froghop.history_schedule")
            path.write_text(json.dumps(raw))
            loaded = self.compose([path])
            self.assertNotIn("froghop.history_schedule", loaded.availability("froghop")["active"])

    def test_bundle_override_selects_one_definition_with_multiple_origins(self):
        from llm_circuit_optimiser.grammar import GrammarError
        with tempfile.TemporaryDirectory() as tmp:
            raw = json.loads(self.base.read_text())
            duplicate = copy.deepcopy(raw)
            duplicate["id"] = "duplicate.base"
            other = Path(tmp) / "duplicate.json"
            other.write_text(json.dumps(duplicate))
            selected = self.compose([self.base, other])
            bundle = self.write_bundle(tmp, selected)
            altered = copy.deepcopy(raw)
            altered["id"] = "altered.base"
            altered["rules"] = [altered["rules"][0]]
            rid = altered["rules"][0]["id"]
            altered["rules"][0]["description"] += " Alternative definition."
            changed = Path(tmp) / "altered.json"
            changed.write_text(json.dumps(altered))
            with self.assertRaises(GrammarError):
                self.compose([bundle, changed])
            merged = self.compose([bundle, changed], overrides={rid: bundle})
            self.assertEqual(merged.rules[rid], selected.rules[rid])

    def test_compiled_rules_cannot_change_behind_the_recorded_hash(self):
        grammar = self.compose([self.base, self.froghop])
        before = grammar.sha256
        exported = grammar.rules
        exported["cleanup.inverse_and_measure"]["parameters"]["phase_fix"]["default"] = False
        candidate = grammar.candidate_for_policy("u", "q799", "froghop")
        self.assertIs(candidate["actions"][0]["parameters"]["phase_fix"], True)
        self.assertEqual(grammar.sha256, before)

    def test_invalid_parameter_schema_reports_a_grammar_error(self):
        from llm_circuit_optimiser.grammar import GrammarError
        with tempfile.TemporaryDirectory() as tmp:
            raw = json.loads(self.base.read_text())
            raw["rules"][0]["parameters"]["guards"]["minimum"] = "not-a-number"
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(raw))
            with self.assertRaises(GrammarError):
                self.compose([path])

    def test_conflicting_definition_requires_exact_explicit_override(self):
        from llm_circuit_optimiser.grammar import GrammarError
        with tempfile.TemporaryDirectory() as tmp:
            original = json.loads(self.base.read_text())
            altered = copy.deepcopy(original)
            altered["id"] = "test.revision"
            altered["rules"] = [altered["rules"][0]]
            altered["rules"][0]["revision"] += 1
            altered["rules"][0]["description"] += " Revised."
            path = Path(tmp) / "override.json"
            path.write_text(json.dumps(altered))
            with self.assertRaisesRegex(GrammarError, "conflicting"):
                self.compose([self.base, path])
            rid = altered["rules"][0]["id"]
            result = self.compose([self.base, path], overrides={rid: path})
            self.assertEqual(result.rules[rid]["revision"], altered["rules"][0]["revision"])

    def test_policy_constructs_valid_actions_and_preserves_target(self):
        grammar = self.compose([self.base, self.froghop])
        candidate = grammar.candidate_for_policy("mfsur", "q799", "froghop")
        checked = grammar.validate_candidate(candidate, "froghop")
        self.assertEqual(checked["target"], "q799")
        self.assertEqual(checked["policy"], "mfsur")
        self.assertIn("cleanup.inverse_and_measure", [a["rule"] for a in checked["actions"]])
        self.assertEqual(checked["grammar_sha256"], grammar.sha256)

    def test_candidate_cannot_omit_phase_fix_or_disable_guards(self):
        from llm_circuit_optimiser.grammar import GrammarError
        grammar = self.compose([self.base, self.froghop])
        candidate = grammar.candidate_for_policy("mfsu", "q799", "froghop")
        candidate["actions"][-1]["parameters"]["phase_fix"] = False
        with self.assertRaisesRegex(GrammarError, "phase_fix"):
            grammar.validate_candidate(candidate, "froghop")

    def test_action_list_must_agree_with_policy(self):
        from llm_circuit_optimiser.grammar import GrammarError
        grammar = self.compose([self.base, self.froghop])
        candidate = grammar.candidate_for_policy("mfsur", "q799", "froghop")
        candidate["actions"] = candidate["actions"][:-1]
        with self.assertRaisesRegex(GrammarError, "policy"):
            grammar.validate_candidate(candidate, "froghop")

    def test_all_32_rule_subsets_admit_independent_search(self):
        from itertools import combinations
        grammar = self.compose([self.base, self.froghop])
        expected = {"none"}
        for count in range(1, 6):
            expected.update("".join(flags) for flags in combinations("mfsur", count))
        from llm_circuit_optimiser.grammar import POLICIES
        self.assertEqual(set(POLICIES), expected)
        for policy in sorted(expected):
            candidate = grammar.candidate_for_policy(policy, "q799", "froghop")
            self.assertEqual(candidate["policy"], policy)
            self.assertEqual(len(candidate["actions"]), 0 if policy == "none" else len(policy))

    def test_action_only_candidate_derives_canonical_policy(self):
        grammar = self.compose([self.base, self.froghop])
        candidate = {"target": "q799", "actions": [
            {"rule": "cleanup.unread_write_measure"}, {"rule": "base.known_control_reduce"}]}
        self.assertEqual(grammar.validate_candidate(candidate, "froghop")["policy"], "mr")
        self.assertEqual(grammar.validate_candidate({"target": "q799", "actions": []}, "froghop")["policy"], "none")

    def test_composed_rule_subset_can_be_searched_without_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = json.loads(self.base.read_text())
            raw["rules"] = [r for r in raw["rules"] if r["id"] == "cleanup.inverse_and_measure"]
            path = Path(tmp) / "just-u.json"
            path.write_text(json.dumps(raw))
            grammar = self.compose([path])
            candidate = grammar.candidate_for_policy("u", "q799", "froghop")
            self.assertEqual(candidate["policy"], "u")

    def cancellation_grammar(self, directory):
        raw = json.loads(self.base.read_text())
        rule = next(r for r in raw["rules"] if r["id"] == "base.involution_cancel")
        rule["status"] = "executable"
        rule["parameters"] = {"guards": {"type": "boolean", "enum": [True], "default": True}}
        rule["bindings"] = {"fast": "adjacent16.cancel", "large": {
            backend: f"{backend}.adjacent.cancel" for backend in
            ["froghop", "qualified-leapfrog", "source-conditional"]}}
        path = Path(directory) / "cancel.json"
        path.write_text(json.dumps(raw))
        return self.compose([path])

    def test_cancellation_composes_independently_with_the_conditional_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            grammar = self.cancellation_grammar(tmp)
            candidate = grammar.candidate_for_policy("ur", "q799", "froghop", cancel_adjacent=True)
            self.assertTrue(candidate["cancel_adjacent"])
            self.assertEqual(candidate["policy"], "ur")
            cancel_only = grammar.validate_candidate({"target": "q799", "actions": [
                {"rule": "base.involution_cancel"}]}, "froghop")
            self.assertEqual(cancel_only["policy"], "none")
            self.assertTrue(cancel_only["cancel_adjacent"])

    def test_explicit_cancellation_flag_must_match_its_action(self):
        from llm_circuit_optimiser.grammar import GrammarError
        with tempfile.TemporaryDirectory() as tmp:
            grammar = self.cancellation_grammar(tmp)
            candidate = grammar.candidate_for_policy("u", "q799", "froghop")
            candidate["cancel_adjacent"] = True
            with self.assertRaisesRegex(GrammarError, "cancel_adjacent"):
                grammar.validate_candidate(candidate, "froghop")
            candidate["cancel_adjacent"] = 1
            with self.assertRaisesRegex(GrammarError, "cancel_adjacent"):
                grammar.validate_candidate(candidate, "froghop")

    def test_family_rules_remain_visible_with_missing_stage_reasons(self):
        grammar = self.compose([self.base, self.froghop])
        result = grammar.availability("froghop")
        self.assertIn("cleanup.inverse_and_measure", result["active"])
        blocked = {r["rule_id"]: r["reasons"] for r in result["blocked"]}
        self.assertIn("froghop.history_schedule", blocked)
        self.assertTrue(any("fast" in r for r in blocked["froghop.history_schedule"]))

    def test_capability_claim_cannot_activate_unknown_binding(self):
        from llm_circuit_optimiser.grammar import GrammarError
        with tempfile.TemporaryDirectory() as tmp:
            data = json.loads(self.base.read_text())
            data["rules"] = [data["rules"][0]]
            data["rules"][0]["bindings"] = {"fast": "made.up", "large": {"froghop": "made.up"}}
            data["rules"][0]["status"] = "executable"
            path = Path(tmp) / "claimed.json"
            path.write_text(json.dumps(data))
            grammar = self.compose([path])
            self.assertEqual(grammar.availability("froghop")["active"], [])
            with self.assertRaises(GrammarError):
                grammar.validate_candidate({"target": "q799", "policy": "mfs", "actions": []}, "froghop")

    def test_invalid_parameter_type_and_unknown_fields_fail(self):
        from llm_circuit_optimiser.grammar import GrammarError
        grammar = self.compose([self.base, self.froghop])
        candidate = grammar.candidate_for_policy("mfsur", "q799", "froghop")
        candidate["actions"][0]["parameters"]["guards"] = 1
        with self.assertRaisesRegex(GrammarError, "guards"):
            grammar.validate_candidate(candidate, "froghop")
        candidate = grammar.candidate_for_policy("mfs", "q799", "froghop")
        candidate["epsilon"] = 0.01
        with self.assertRaisesRegex(GrammarError, "unknown"):
            grammar.validate_candidate(candidate, "froghop")

    def test_multiple_family_addenda_do_not_change_selected_family(self):
        grammar = self.compose([self.base, self.froghop,
                                ROOT / "grammars/families/qualified-leapfrog.json",
                                ROOT / "grammars/families/skywalk-cof.json"])
        candidate = grammar.candidate_for_policy("mfsur", "q799", "froghop")
        self.assertEqual(grammar.validate_candidate(candidate, "froghop")["family"], "froghop")
        blocked = {r["rule_id"]: r["reasons"] for r in grammar.availability("froghop")["blocked"]}
        self.assertTrue(any("family" in r for r in blocked["leapfrog.group_signed_updates"]))

    def test_leapfrog_shared_compiler_is_separate_from_unmapped_arithmetic(self):
        grammar = self.compose([self.base, ROOT / "grammars/families/qualified-leapfrog.json"])
        candidate = grammar.candidate_for_policy("ur", "ap1270", "qualified-leapfrog")
        self.assertEqual(candidate["backend"], "qualified-leapfrog")
        blocked = {r["rule_id"] for r in grammar.availability("qualified-leapfrog")["blocked"]}
        self.assertIn("leapfrog.replay_program", blocked)

    def test_custom_family_alias_inherits_real_backend_and_credit(self):
        from llm_circuit_optimiser.families import load_family, register_family
        with tempfile.TemporaryDirectory() as tmp:
            definition = {
                "schema_version": 1, "id": "my-froghop", "name": "My Froghop study",
                "backend": "froghop", "targets": ["q799"],
                "provenance": {"description": "A local derivative of the frozen Froghop backend.",
                               "authors": ["Local researcher"], "upstream_family": "froghop"},
            }
            source = Path(tmp) / "family.json"
            source.write_text(json.dumps(definition))
            installed = register_family(source, Path(tmp) / "registry")
            family = load_family(installed)
            self.assertEqual(family["backend"], "froghop")
            grammar = self.compose([self.base, self.froghop])
            candidate = grammar.candidate_for_policy("mfsu", "q799", family)
            checked = grammar.validate_candidate(candidate, family)
            self.assertEqual(checked["family"], "my-froghop")
            self.assertEqual(checked["backend"], "froghop")
            self.assertIn("froghop", family["provenance"]["upstream_family"])

    def test_custom_family_rejects_unknown_backend_and_commands(self):
        from llm_circuit_optimiser.families import FamilyError, load_family
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "family.json"
            path.write_text(json.dumps({"schema_version": 1, "id": "unsafe", "name": "Unsafe",
                                        "backend": "shell", "targets": ["q799"],
                                        "provenance": {"description": "Unknown implementation", "authors": ["A"], "upstream_family": "shell"},
                                        "command": "echo unsafe"}))
            with self.assertRaises(FamilyError):
                load_family(path)

    def test_cross_family_target_rejected(self):
        from llm_circuit_optimiser.grammar import GrammarError
        grammar = self.compose([self.base, self.froghop])
        with self.assertRaisesRegex(GrammarError, "target"):
            grammar.candidate_for_policy("mfsur", "ap1270", "froghop")

    def source_definition(self, directory):
        directory = Path(directory)
        module = directory / "source/src/point_add/mod.rs"
        module.parent.mkdir(parents=True)
        module.write_text("pub fn build() -> Vec<crate::circuit::Op> { Vec::new() }\n")
        notice = directory / "source/NOTICE"
        notice.write_text("Test fixture. No third-party source.\n")
        data = {"schema_version": 1, "id": "new-family", "name": "New source family",
                "backend": "source-conditional", "targets": ["my-point"], "source_dir": "source",
                "files_sha256": {"src/point_add/mod.rs": hashlib.sha256(module.read_bytes()).hexdigest(),
                                 "NOTICE": hashlib.sha256(notice.read_bytes()).hexdigest()},
                "provenance": {"description": "A test constructor with inherited family attribution.",
                               "authors": ["Test fixture"], "upstream_family": "froghop"}}
        path = directory / "family.json"
        path.write_text(json.dumps(data))
        return path, data

    def test_custom_source_family_pins_constructor_and_uses_shared_rule_contract(self):
        from llm_circuit_optimiser.families import load_family
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.source_definition(tmp)
            family = load_family(path)
            self.assertEqual(Path(family["source_dir"]), (Path(tmp) / "source").resolve())
            grammar = self.compose([self.base, self.froghop])
            candidate = grammar.candidate_for_policy("ur", "my-point", family)
            self.assertEqual(candidate["backend"], "source-conditional")
            blocked = {r["rule_id"] for r in grammar.availability(family)["blocked"]}
            self.assertIn("froghop.history_schedule", blocked)

    def test_original_source_family_can_declare_no_arithmetic_parent(self):
        from llm_circuit_optimiser.families import load_family
        with tempfile.TemporaryDirectory() as tmp:
            path, data = self.source_definition(tmp)
            data["provenance"]["upstream_family"] = None
            data["provenance"]["description"] = "An original test constructor with no inherited arithmetic family."
            path.write_text(json.dumps(data))
            family = load_family(path)
            self.assertIsNone(family["derived_from"])
            grammar = self.compose([self.base])
            self.assertEqual(grammar.candidate_for_policy("u", "my-point", family)["family"], "new-family")

    def test_custom_source_rejects_changed_source_and_path_escape(self):
        from llm_circuit_optimiser.families import FamilyError, load_family
        with tempfile.TemporaryDirectory() as tmp:
            path, data = self.source_definition(tmp)
            module = Path(tmp) / "source/src/point_add/mod.rs"
            module.write_text("changed source")
            with self.assertRaisesRegex(FamilyError, "hash"):
                load_family(path)
            data["files_sha256"]["../outside.rs"] = "0" * 64
            path.write_text(json.dumps(data))
            with self.assertRaises(FamilyError):
                load_family(path)

    def test_custom_source_registration_preserves_relative_source_resolution(self):
        from llm_circuit_optimiser.families import load_family, register_family
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.source_definition(tmp)
            installed = register_family(path, Path(tmp) / "registry")
            self.assertEqual(load_family(installed)["files_sha256"], load_family(path)["files_sha256"])

    def test_custom_grammar_files_resolve_from_definition_and_survive_registration(self):
        from llm_circuit_optimiser.families import load_family, register_family
        with tempfile.TemporaryDirectory() as tmp:
            path, data = self.source_definition(tmp)
            extra = Path(tmp) / "extra.json"
            extra.write_text(self.base.read_text())
            data["grammar_files"] = ["extra.json"]
            path.write_text(json.dumps(data))
            resolved = load_family(path)
            self.assertEqual(resolved["grammar_files"], [str(extra.resolve())])
            installed = register_family(path, Path(tmp) / "other/registry")
            self.assertEqual(load_family(installed)["grammar_files"], resolved["grammar_files"])
            self.assertFalse(Path(json.loads(installed.read_text())["grammar_files"][0]).is_absolute())

    def test_missing_declared_grammar_file_fails_registration(self):
        from llm_circuit_optimiser.families import FamilyError, load_family
        with tempfile.TemporaryDirectory() as tmp:
            path, data = self.source_definition(tmp)
            data["grammar_files"] = ["missing.json"]
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(FamilyError, "grammar"):
                load_family(path)

    def test_custom_source_rejects_symlink_files(self):
        from llm_circuit_optimiser.families import FamilyError, load_family
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.source_definition(tmp)
            module = Path(tmp) / "source/src/point_add/mod.rs"
            saved = module.with_name("saved.rs")
            module.rename(saved)
            module.symlink_to(saved)
            with self.assertRaisesRegex(FamilyError, "symlink"):
                load_family(path)


if __name__ == "__main__":
    unittest.main()
