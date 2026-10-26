"""Compose source-bound rules and admit only implemented candidate actions."""

from __future__ import annotations

import copy
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .families import FamilyError, load_family


class GrammarError(ValueError):
    """A grammar or candidate violates its executable contract."""


COND_SHA256 = "a56f374ad86c7dd3ca13cfd08291f5f9a4f40a54e122959c481656a54b9d987e"
POLICY_ORDER = "mfsur"
POLICIES = ("none",) + tuple("".join(flags) for count in range(1, 6)
                              for flags in combinations(POLICY_ORDER, count))
POLICY_RULES = {
    "m": "base.known_control_reduce", "f": "base.phase_only_and",
    "s": "base.classical_guard_sink", "u": "cleanup.inverse_and_measure",
    "r": "cleanup.unread_write_measure",
}
CANCEL_RULE = "base.involution_cancel"
CATALOG_RULE = "base.local_catalog"
_FLAGS = {v: k for k, v in POLICY_RULES.items()}
_PARAM_TYPES = {"boolean", "integer", "number", "string"}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise GrammarError(f"The grammar must contain finite JSON values: {exc}") from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GrammarError(message)


def _parameter(value: Any, spec: dict[str, Any], label: str) -> Any:
    kind = spec["type"]
    valid = {"boolean": type(value) is bool, "integer": type(value) is int,
             "number": type(value) in (int, float), "string": isinstance(value, str)}[kind]
    _require(valid, f"Parameter {label} must have type {kind}")
    if "enum" in spec:
        _require(any(type(value) is type(x) and value == x for x in spec["enum"]),
                 f"Parameter {label} is outside its admitted values")
    if "minimum" in spec:
        _require(value >= spec["minimum"], f"Parameter {label} is below its minimum")
    if "maximum" in spec:
        _require(value <= spec["maximum"], f"Parameter {label} exceeds its maximum")
    return value


def _validate_rule(rule: Any, source: Path) -> dict[str, Any]:
    _require(isinstance(rule, dict), f"A rule in {source} must be an object")
    fields = {"id", "revision", "title", "description", "status", "families", "parameters",
              "guards", "bindings", "mechanism", "preserves", "cost", "evidence", "requires", "notes", "kind"}
    _require(not set(rule) - fields, f"Unknown rule fields in {source}: {sorted(set(rule) - fields)}")
    for field in fields - {"notes", "kind"}:
        _require(field in rule, f"Rule in {source} lacks {field}")
    _require(isinstance(rule["id"], str) and bool(re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", rule["id"])), "Rule id must be namespaced")
    _require(type(rule["revision"]) is int and rule["revision"] > 0, "Rule revision must be positive")
    _require(rule["status"] in {"executable", "specification", "research"}, "Unknown rule status")
    _require(rule.get("kind", "rewrite") in {"rewrite", "setting"}, "Unknown rule kind")
    for key in ["title", "description"]:
        _require(isinstance(rule[key], str) and bool(rule[key].strip()), f"Rule {rule['id']} needs {key}")
    for key in ["families", "guards", "preserves", "requires", "evidence"]:
        _require(isinstance(rule[key], list), f"Rule {rule['id']} {key} must be a list")
    for key in ["families", "guards", "preserves", "requires"]:
        _require(all(isinstance(x, str) and x for x in rule[key]), f"Rule {rule['id']} {key} must contain nonempty names")
    _require(rule["families"] and all(isinstance(f, str) for f in rule["families"]), "Rule families must be names")
    _require(isinstance(rule["cost"], str) and bool(rule["cost"]), "Rule cost must explain its measurement scope")
    mechanism = rule["mechanism"]
    _require(isinstance(mechanism, dict) and set(mechanism) == {"match", "replace"}
             and all(isinstance(v, str) and v for v in mechanism.values()), "Rule mechanism requires match and replace descriptions")
    _require(isinstance(rule["parameters"], dict), "Rule parameters must be an object")
    for name, spec in rule["parameters"].items():
        _require(bool(re.fullmatch(r"[a-z][a-z0-9_]*", name)), f"Invalid parameter name {name}")
        _require(isinstance(spec, dict) and spec.get("type") in _PARAM_TYPES, f"Parameter {name} has no admitted type")
        _require(not set(spec) - {"type", "default", "enum", "minimum", "maximum", "description"}, f"Unknown parameter schema fields for {name}")
        _require("default" in spec, f"Parameter {name} needs a default")
        if "enum" in spec:
            _require(isinstance(spec["enum"], list) and bool(spec["enum"]), f"Parameter {name} enum must be a nonempty list")
        for bound in ["minimum", "maximum"]:
            if bound in spec:
                _require(spec["type"] in {"integer", "number"} and type(spec[bound]) in {int, float},
                         f"Parameter {name} {bound} requires a numeric type and value")
        _parameter(spec["default"], spec, name)
    bindings = rule["bindings"]
    _require(isinstance(bindings, dict) and {"fast", "large"} <= set(bindings)
             and not set(bindings) - {"fast", "large", "fast_reason"}, "Bindings require fast and large entries")
    _require(bindings["fast"] is None or isinstance(bindings["fast"], str), "Fast binding must be a name or null")
    _require(isinstance(bindings["large"], dict), "Large bindings must map family backends to names")
    if "fast_reason" in bindings:
        _require(isinstance(bindings["fast_reason"], str) and bool(bindings["fast_reason"].strip()),
                 "The missing-fast reason must explain the setting's scope")
    if rule.get("kind") == "setting" and bindings["fast"] is None:
        _require(bool(bindings.get("fast_reason")), "A full-width-only setting needs a missing-fast reason")
        _require(bool(rule["guards"]) and bool(rule["evidence"]), "A setting needs guards and source evidence")
    for entry in rule["evidence"]:
        _require(isinstance(entry, dict) and isinstance(entry.get("path"), str), "Evidence requires a source path")
        if "sha256" in entry:
            _require(bool(re.fullmatch(r"[0-9a-f]{64}", str(entry["sha256"]))), "Invalid evidence SHA-256")
    return copy.deepcopy(rule)


class CompiledGrammar:
    """A deterministic rule set with explicit family admission decisions."""

    def __init__(self, rules: dict[str, dict[str, Any]], documents: list[dict[str, Any]],
                 source_map: dict[str, list[dict[str, str]]]):
        self._rules = dict(sorted(copy.deepcopy(rules).items()))
        self.documents = sorted(copy.deepcopy(documents), key=lambda d: (d["id"], d["sha256"]))
        self.source_map = {k: sorted(v, key=lambda x: (x["document"], x["sha256"])) for k, v in sorted(source_map.items())}
        self._content = {"schema_version": 1, "documents": copy.deepcopy(self.documents),
                         "rules": copy.deepcopy(list(self._rules.values())), "source_map": copy.deepcopy(self.source_map),
                         "compiler_order": ["m", "u", "f", "s", "r"],
                         "conditional_source_sha256": COND_SHA256}
        self.sha256 = hashlib.sha256(_canonical(self._content)).hexdigest()
        self.digest = self.sha256

    @property
    def rules(self) -> dict[str, dict[str, Any]]:
        """Export a copy so callers cannot change the frozen rule set."""
        return copy.deepcopy(self._rules)

    def to_dict(self) -> dict[str, Any]:
        return {**copy.deepcopy(self._content), "sha256": self.sha256}

    def availability(self, family: str | Path | dict[str, Any]) -> dict[str, Any]:
        family = load_family(family)
        backend = family["backend"]
        active, blocked = [], []
        for rid, rule in self._rules.items():
            reasons = []
            if "*" not in rule["families"] and backend not in rule["families"]:
                reasons.append(f"Rule belongs to another family; selected backend is {backend}")
            if rule["status"] != "executable":
                reasons.append(f"Rule status is {rule['status']}; it has no admitted executable action")
            flag = _FLAGS.get(rid)
            cancellation = rid == CANCEL_RULE
            catalog = rid == CATALOG_RULE
            setting = rule.get("kind") == "setting"
            expected_fast = "adjacent16.cancel" if cancellation else (f"conditional16.{flag}" if flag else None)
            if catalog:
                expected_fast = "catalog16.signed-permutation"
            expected_large = None
            if setting:
                expected_large = "frontier.counted_setting"
            if catalog:
                expected_large = "frontier.local_catalog"
            if backend in {"froghop", "qualified-leapfrog", "source-conditional"}:
                if cancellation:
                    expected_large = f"{backend}.adjacent.cancel"
                elif flag:
                    expected_large = f"{backend}.conditional.{flag}"
            if setting and rule["bindings"]["fast"] is None and rule["bindings"].get("fast_reason"):
                pass
            elif not rule["bindings"]["fast"]:
                reasons.append("No fast-stage binding exists")
            elif rule["bindings"]["fast"] != expected_fast:
                reasons.append(f"Unknown fast-stage binding {rule['bindings']['fast']}")
            binding = rule["bindings"]["large"].get(backend)
            if not binding:
                reasons.append(f"No large-stage binding exists for family {backend}")
            elif binding != expected_large:
                reasons.append(f"Unknown large-stage binding {binding} for family {backend}")
            if not flag and not cancellation and not setting and not catalog:
                reasons.append("The executable registry has no implementation for this rule ID")
            for capability in rule["requires"]:
                if capability not in family["capabilities"]:
                    reasons.append(f"Family lacks capability {capability}")
            if reasons:
                blocked.append({"rule_id": rid, "reasons": reasons})
            else:
                active.append(rid)
        return {"family": family["id"], "backend": backend, "active": active, "blocked": blocked}

    def validate_frontier_actions(self, actions: Any, family: str | Path | dict[str, Any]) -> list[dict]:
        """Validate typed frontier actions before binding them to counted source edits."""
        active = set(self.availability(family)["active"])
        _require(isinstance(actions, list), "Frontier actions must be a list")
        normalized, seen = [], set()
        for action in actions:
            _require(isinstance(action, dict) and set(action) <= {"rule", "parameters"}, "Invalid frontier action fields")
            rid = action.get("rule")
            _require(isinstance(rid, str) and rid in active, f"Rule {rid!r} has no admitted frontier binding")
            _require(rid not in seen, f"Duplicate frontier rule {rid}")
            seen.add(rid)
            rule = self._rules[rid]
            values = action.get("parameters", {})
            _require(isinstance(values, dict) and not set(values) - set(rule["parameters"]), "Unknown frontier action parameters")
            params = {name: _parameter(values.get(name, spec["default"]), spec, f"{rid}.{name}")
                      for name, spec in rule["parameters"].items()}
            _require(params.get("guards") is True, f"Rule {rid} guards must remain true")
            if "phase_fix" in params:
                _require(params["phase_fix"] is True, f"Rule {rid} phase correction must remain true")
            normalized.append({"rule": rid, "parameters": params, "kind": rule.get("kind", "rewrite"),
                               "fast_binding": rule["bindings"]["fast"],
                               "fast_reason": rule["bindings"].get("fast_reason")})
        return normalized

    def candidate_for_policy(self, policy: str, target: str,
                             family: str | Path | dict[str, Any], *, cancel_adjacent: bool = False) -> dict[str, Any]:
        _require(policy in POLICIES, f"Unknown policy {policy!r}; use one of {POLICIES}")
        _require(type(cancel_adjacent) is bool, "cancel_adjacent must be Boolean")
        resolved = load_family(family)
        actions = [{"rule": POLICY_RULES[f], "parameters": {}} for f in ("" if policy == "none" else policy)]
        if cancel_adjacent:
            actions.append({"rule": CANCEL_RULE, "parameters": {}})
        return self.validate_candidate({"policy": policy, "target": target,
                                        "actions": actions, "family": resolved["id"],
                                        "cancel_adjacent": cancel_adjacent}, resolved)

    def validate_candidate(self, candidate: Any, family: str | Path | dict[str, Any]) -> dict[str, Any]:
        try:
            family = load_family(family)
        except FamilyError as exc:
            raise GrammarError(str(exc)) from exc
        _require(isinstance(candidate, dict), "A candidate must be an object")
        allowed = {"policy", "target", "actions", "family", "backend", "grammar_sha256", "cancel_adjacent"}
        _require(not set(candidate) - allowed, f"Candidate has unknown fields: {sorted(set(candidate) - allowed)}")
        _require(candidate.get("family", family["id"]) == family["id"], "Candidate family does not match selected family")
        _require(candidate.get("backend", family["backend"]) == family["backend"], "Candidate backend does not match selected family")
        if "grammar_sha256" in candidate:
            _require(candidate["grammar_sha256"] == self.sha256, "Candidate grammar hash is stale")
        target, policy = candidate.get("target"), candidate.get("policy")
        _require(isinstance(target, str) and target in family["targets"], f"Unknown target {target!r} for family {family['id']}")
        actions = candidate.get("actions")
        _require(isinstance(actions, list), "Candidate actions must be a list")
        active = set(self.availability(family)["active"])
        seen, normalized = set(), []
        for action in actions:
            _require(isinstance(action, dict) and set(action) <= {"rule", "parameters"}, "Each action needs rule and parameters fields")
            rid = action.get("rule")
            _require(isinstance(rid, str) and rid in self._rules, f"Unknown candidate rule {rid!r}")
            _require(rid not in seen, f"Duplicate candidate rule {rid}")
            _require(rid in active, f"Rule {rid} has no admitted fast/large bindings for this family")
            _require(self._rules[rid].get("kind") != "setting",
                     f"Setting {rid} requires the frontier runner; the staged policy runner cannot execute it")
            _require(rid != CATALOG_RULE, "The local catalog requires the frontier runner and its source integration")
            seen.add(rid)
            values = action.get("parameters", {})
            _require(isinstance(values, dict), f"Parameters for {rid} must be an object")
            specs = self._rules[rid]["parameters"]
            _require(not set(values) - set(specs), f"Unknown parameters for {rid}: {sorted(set(values) - set(specs))}")
            params = {name: _parameter(values.get(name, spec["default"]), spec, f"{rid}.{name}") for name, spec in specs.items()}
            _require(params.get("guards") is True, f"Rule {rid} guards must remain true")
            if rid in {"cleanup.inverse_and_measure", "cleanup.unread_write_measure", "base.phase_only_and", "base.classical_guard_sink"}:
                _require(params.get("phase_fix") is True, f"Rule {rid} phase_fix must remain true")
            normalized.append({"rule": rid, "parameters": params})
        if policy is None:
            policy = "".join(f for f in POLICY_ORDER if POLICY_RULES[f] in seen) or "none"
        _require(isinstance(policy, str) and policy in POLICIES, f"Unknown policy {policy!r}")
        expected = {POLICY_RULES[f] for f in ("" if policy == "none" else policy)}
        cancel_adjacent = CANCEL_RULE in seen
        if "cancel_adjacent" in candidate:
            _require(type(candidate["cancel_adjacent"]) is bool, "cancel_adjacent must be Boolean")
            _require(candidate["cancel_adjacent"] == cancel_adjacent, "cancel_adjacent must agree with its action")
        if cancel_adjacent:
            expected.add(CANCEL_RULE)
        _require(seen == expected, "Candidate actions must agree with its complete policy")
        normalized.sort(key=lambda a: len(POLICY_ORDER) if a["rule"] == CANCEL_RULE else POLICY_ORDER.index(_FLAGS[a["rule"]]))
        return {"family": family["id"], "backend": family["backend"], "target": target,
                "policy": policy, "cancel_adjacent": cancel_adjacent,
                "actions": normalized, "grammar_sha256": self.sha256}


def _load_bundle(raw: dict[str, Any], path: Path) -> CompiledGrammar:
    """Check a portable compiled grammar before accepting its rule snapshots."""
    fields = {"schema_version", "documents", "rules", "source_map", "compiler_order",
              "conditional_source_sha256", "sha256"}
    _require(fields <= set(raw) and not set(raw) - fields - {"availability"},
             f"Compiled grammar {path} has missing or unknown fields")
    _require(type(raw["schema_version"]) is int and raw["schema_version"] == 1,
             "Compiled grammar schema_version must be 1")
    _require(isinstance(raw["sha256"], str) and bool(re.fullmatch(r"[0-9a-f]{64}", raw["sha256"])),
             "Compiled grammar needs a valid SHA-256 hash")
    content = {k: v for k, v in raw.items() if k not in {"sha256", "availability"}}
    _require(hashlib.sha256(_canonical(content)).hexdigest() == raw["sha256"],
             f"Compiled grammar hash mismatch: {path}")
    _require(raw["conditional_source_sha256"] == COND_SHA256,
             "Compiled grammar uses a different conditional compiler")
    _require(raw["compiler_order"] == ["m", "u", "f", "s", "r"],
             "Compiled grammar uses a different compiler order")
    if "availability" in raw:
        _require(isinstance(raw["availability"], dict), "Compiled availability must be a report object")
    documents = raw["documents"]
    _require(isinstance(documents, list) and bool(documents), "Compiled grammar needs origin documents")
    origins = {}
    for document in documents:
        _require(isinstance(document, dict) and set(document) == {"id", "revision", "sha256"},
                 "A compiled origin needs an ID, revision, and hash")
        _require(isinstance(document["id"], str) and bool(document["id"]), "Compiled origin ID must be a name")
        _require(type(document["revision"]) is int and document["revision"] > 0,
                 "Compiled origin revision must be positive")
        _require(isinstance(document["sha256"], str) and bool(re.fullmatch(r"[0-9a-f]{64}", document["sha256"])),
                 "Compiled origin hash is invalid")
        identity = (document["id"], document["sha256"])
        _require(identity not in origins, "Compiled grammar repeats an origin document")
        origins[identity] = document
    _require(isinstance(raw["rules"], list), "Compiled rules must be a list")
    rules = {}
    for value in raw["rules"]:
        try:
            rule = _validate_rule(value, path)
        except (KeyError, TypeError) as exc:
            raise GrammarError(f"Malformed compiled rule in {path}") from exc
        _require(rule["id"] not in rules, f"Duplicate compiled rule ID {rule['id']}")
        rules[rule["id"]] = rule
    source_map = raw["source_map"]
    _require(isinstance(source_map, dict) and set(source_map) == set(rules),
             "Compiled source map must cover exactly the rule IDs")
    for rid, references in source_map.items():
        _require(isinstance(references, list) and bool(references), f"Compiled rule {rid} needs an origin")
        seen = set()
        for reference in references:
            _require(isinstance(reference, dict) and set(reference) == {"document", "sha256"}
                     and all(isinstance(v, str) for v in reference.values()),
                     f"Compiled rule {rid} has a malformed origin reference")
            identity = (reference["document"], reference["sha256"])
            _require(identity in origins, f"Compiled rule {rid} references an unknown origin")
            _require(identity not in seen, f"Compiled rule {rid} repeats an origin reference")
            seen.add(identity)
    compiled = CompiledGrammar(rules, documents, source_map)
    _require(compiled.sha256 == raw["sha256"], "Compiled grammar hash is not canonical")
    return compiled


def compose(paths: Iterable[str | Path], *, overrides: Mapping[str, str | Path] | Iterable[str] = ()) -> CompiledGrammar:
    """Merge grammar files without using their input order to resolve conflicts."""
    paths = sorted({Path(p).resolve() for p in paths}, key=str)
    _require(bool(paths), "At least one grammar file is required")
    _require(isinstance(overrides, Mapping) or not overrides, "Overrides must map each rule ID to its chosen definition file")
    choices = {rid: Path(path).resolve() for rid, path in dict(overrides).items()}
    by_rule, documents = {}, []
    seen_documents = set()
    for path in paths:
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise GrammarError(f"Cannot read grammar {path}: {exc}") from exc
        _require(isinstance(raw, dict), f"Grammar {path} must be an object")
        if set(raw) & {"sha256", "documents", "source_map"}:
            bundle = _load_bundle(raw, path)
            origin_map = {(d["id"], d["sha256"]): d for d in bundle.documents}
            for identity, document in origin_map.items():
                if identity not in seen_documents:
                    documents.append(document)
                    seen_documents.add(identity)
            for rid, rule in bundle.rules.items():
                refs = [origin_map[(r["document"], r["sha256"])] for r in bundle.source_map[rid]]
                by_rule.setdefault(rid, []).append((rule, path, refs))
            continue
        _require(not set(raw) - {"schema_version", "id", "revision", "title", "description", "definitions", "rules", "evidence"}, f"Unknown document fields in {path}")
        _require(raw.get("schema_version") == 1 and type(raw.get("schema_version")) is int, "Grammar schema_version must be 1")
        _require(isinstance(raw.get("id"), str) and bool(raw["id"]), "Grammar document needs an ID")
        _require(type(raw.get("revision")) is int and raw["revision"] > 0, "Grammar document revision must be positive")
        _require(isinstance(raw.get("rules"), list), "Grammar rules must be a list")
        digest = hashlib.sha256(_canonical(raw)).hexdigest()
        document = {"id": raw["id"], "revision": raw["revision"], "sha256": digest}
        identity = (raw["id"], digest)
        if identity not in seen_documents:
            documents.append(document)
            seen_documents.add(identity)
        local = set()
        for rule in raw["rules"]:
            rule = _validate_rule(rule, path)
            rid = rule["id"]
            _require(rid not in local, f"Duplicate rule ID {rid} within {path}")
            local.add(rid)
            by_rule.setdefault(rid, []).append((rule, path, [document]))
    rules, source_map = {}, {}
    used = set()
    for rid, definitions in by_rule.items():
        variants = {_canonical(rule) for rule, _, _ in definitions}
        if len(variants) > 1:
            _require(rid in choices, f"Rule {rid} has conflicting definitions; choose an explicit override file")
            selected = [entry for entry in definitions if entry[1] == choices[rid]]
            _require(len(selected) == 1, f"Override for {rid} does not select an input definition")
            rules[rid] = selected[0][0]
            used.add(rid)
        else:
            rules[rid] = definitions[0][0]
        refs = {(d["id"], d["sha256"]) for _, _, docs in definitions for d in docs}
        source_map[rid] = [{"document": d, "sha256": h} for d, h in sorted(refs)]
    _require(not set(choices) - used, f"Unused override IDs: {sorted(set(choices) - used)}")
    return CompiledGrammar(rules, documents, source_map)
