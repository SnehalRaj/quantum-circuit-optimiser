"""Validate family definitions against implemented backend contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any


class FamilyError(ValueError):
    """A family lacks a valid implementation or source contract."""


_TARGETS = [f"q{q}" for q in [799, 800, 801, 802, 803, 808, 809, 810, 811,
                                 812, 813, 814, 816, 839]]

_BUILTINS = {
    "froghop": {
        "schema_version": 1, "id": "froghop", "name": "Froghop point addition",
        "backend": "froghop", "targets": _TARGETS,
        "capabilities": ["conditional-op-ir", "phase-tracking", "measured-reset"],
        "stage_bindings": {"fast": "conditional16", "large": "froghop-conditional"},
        "provenance": {
            "description": "Frozen source derivatives from the 25 October 2026 Pareto campaign.",
            "authors": ["See each target's upstream source and dependency declarations"],
            "upstream_family": "froghop",
        },
        "mapping_limit": "The fast constructor uses Kaliski arithmetic. Shared compiler rules transfer; the Froghop schedule does not.",
    },
    "qualified-leapfrog": {
        "schema_version": 1, "id": "qualified-leapfrog", "name": "Qualified Leapfrog point addition",
        "backend": "qualified-leapfrog", "targets": ["ap1270"],
        "capabilities": ["conditional-op-ir", "phase-tracking", "measured-reset"],
        "stage_bindings": {"fast": "conditional16", "large": "qualified-leapfrog-conditional"},
        "provenance": {
            "description": "The source-bound repaired and qualified Leapfrog AP1270 derivative.",
            "authors": ["See the AP1270 source and dependency declarations"],
            "upstream_family": "qualified-leapfrog",
        },
        "mapping_limit": "The shared conditional wrapper maps between stages. Leapfrog arithmetic parameters have no implemented small-stage mapping.",
    },
    "skywalk-cof": {
        "schema_version": 1, "id": "skywalk-cof", "name": "Skywalk with shared cofactors",
        "backend": "skywalk-cof", "targets": [], "capabilities": [],
        "stage_bindings": {},
        "provenance": {
            "description": "Source-documented mechanisms await a matched fast and large adapter.",
            "authors": ["See the source catalog"], "upstream_family": "skywalk-cof",
        },
        "mapping_limit": "The grammar records these mechanisms. This registry provides no executable Skywalk/COF adapter.",
    },
}


def builtin_families() -> list[dict[str, Any]]:
    """Return independent copies of all built-in family contracts."""
    return [copy.deepcopy(_BUILTINS[k]) for k in sorted(_BUILTINS)]


def _source_files(data: dict[str, Any], base_dir: Path) -> Path:
    """Check a pinned contestant closure without trusting its build files."""
    directory = data.get("source_dir")
    if not isinstance(directory, str) or not directory:
        raise FamilyError("A source family requires source_dir")
    directory = Path(directory)
    if not directory.is_absolute():
        directory = base_dir / directory
    directory = directory.resolve()
    hashes = data.get("files_sha256")
    if not isinstance(hashes, dict) or not {"src/point_add/mod.rs", "NOTICE"} <= set(hashes):
        raise FamilyError("Source files_sha256 must pin src/point_add/mod.rs and NOTICE")
    for name, expected in hashes.items():
        if not isinstance(name, str):
            raise FamilyError("Each source file name must be a relative POSIX path")
        relative = PurePosixPath(name)
        if relative.is_absolute() or any(p in {"..", "."} for p in relative.parts) or str(relative) != name:
            raise FamilyError(f"Invalid source file path {name}")
        if name != "NOTICE" and not name.startswith("src/point_add/"):
            raise FamilyError(f"Source closure contains an uneditable file: {name}")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise FamilyError(f"Invalid source hash for {name}")
        path = directory.joinpath(*relative.parts)
        if any(p.is_symlink() for p in [path, *path.parents] if p != directory and directory in p.parents):
            raise FamilyError(f"Source closure contains a symlink: {name}")
        if not path.is_file() or directory not in path.resolve().parents:
            raise FamilyError(f"Missing or escaped source file: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise FamilyError(f"Source hash mismatch: {name}")
    return directory


def _validate(data: Any, base_dir: Path | None = None) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise FamilyError("A family must be a JSON object")
    allowed = {"schema_version", "id", "name", "backend", "targets", "provenance",
               "description", "grammar_files", "mapping_limit", "source_dir", "files_sha256"}
    unknown = set(data) - allowed
    if unknown:
        raise FamilyError(f"Unknown family fields: {sorted(unknown)}")
    if data.get("schema_version") != 1 or isinstance(data.get("schema_version"), bool):
        raise FamilyError("Family schema_version must be 1")
    if not isinstance(data.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", data["id"]):
        raise FamilyError("Family id must contain lowercase letters, digits, and hyphens")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise FamilyError("Family name is required")
    backend = data.get("backend")
    source_family = backend == "source-conditional"
    if backend not in _BUILTINS and not source_family:
        raise FamilyError(f"Unknown backend {backend!r}; implement both native bindings before registration")
    parent = _BUILTINS.get(backend)
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets or not all(isinstance(t, str) for t in targets):
        raise FamilyError("Family targets must be a nonempty list of target IDs")
    if len(set(targets)) != len(targets) or not all(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", t) for t in targets):
        raise FamilyError("Family targets must be unique lowercase IDs")
    if not source_family and not set(targets) <= set(parent["targets"]):
        raise FamilyError("Family targets must belong to its implemented backend")
    provenance = data.get("provenance")
    if not isinstance(provenance, dict):
        raise FamilyError("Family provenance is required")
    if set(provenance) - {"description", "authors", "upstream_family", "source", "source_sha256",
                          "parent_target", "parent_commit", "dependency_ids", "notice"}:
        raise FamilyError("Unknown provenance fields")
    if not isinstance(provenance.get("description"), str) or not provenance["description"].strip():
        raise FamilyError("A source description is required")
    upstream = provenance.get("upstream_family")
    if (not source_family and upstream != backend) or (source_family and upstream is not None and upstream not in _BUILTINS):
        raise FamilyError("Preserve upstream_family as the implemented backend name")
    authors = provenance.get("authors")
    if not isinstance(authors, list) or not authors or not all(isinstance(a, str) and a.strip() for a in authors):
        raise FamilyError("Provenance authors must be a nonempty list")
    if "source_sha256" in provenance and not re.fullmatch(r"[0-9a-f]{64}", str(provenance["source_sha256"])):
        raise FamilyError("source_sha256 must contain 64 lowercase hex digits")
    grammar_files = data.get("grammar_files", [])
    if not isinstance(grammar_files, list) or not all(isinstance(p, str) and p for p in grammar_files):
        raise FamilyError("grammar_files must contain file paths")
    resolved_grammars = []
    for name in grammar_files:
        path = Path(name)
        if not path.is_absolute():
            path = (base_dir or Path.cwd()) / path
        if not path.is_file():
            raise FamilyError(f"Missing declared grammar file: {name}")
        resolved_grammars.append(str(path.resolve()))
    if source_family:
        source_root = _source_files(data, base_dir or Path.cwd())
        result = {
            "capabilities": ["conditional-op-ir", "phase-tracking", "measured-reset"],
            "stage_bindings": {"fast": "conditional16", "large": "source-conditional"},
            "mapping_limit": "The fast stage tests the shared conditional compiler. Custom arithmetic and schedules require their own small-stage bindings.",
        }
    else:
        if "source_dir" in data or "files_sha256" in data:
            raise FamilyError("Pinned custom source requires backend source-conditional")
        result = copy.deepcopy(parent)
    result.update(copy.deepcopy(data))
    if "grammar_files" in data:
        result["grammar_files"] = resolved_grammars
    result["targets"] = sorted(targets)
    if source_family:
        result["source_dir"] = str(source_root)
    result["derived_from"] = upstream
    result["definition_sha256"] = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def load_family(value: str | Path | dict[str, Any], registry: str | Path | None = None) -> dict[str, Any]:
    """Resolve a built-in family, definition file, or validated custom definition."""
    if isinstance(value, dict):
        if value.get("id") in _BUILTINS and value == _BUILTINS[value["id"]]:
            return copy.deepcopy(value)
        # Revalidate only the source fields in an already resolved definition.
        derived = {k: v for k, v in value.items() if k not in {"capabilities", "stage_bindings", "derived_from", "definition_sha256"}}
        return _validate(derived)
    if str(value) in _BUILTINS:
        return copy.deepcopy(_BUILTINS[str(value)])
    path = Path(value)
    if registry is not None and not path.is_file():
        path = Path(registry) / f"{value}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise FamilyError(f"Cannot read family {value}: {exc}") from exc
    return _validate(data, path.resolve().parent)


def register_family(source: str | Path, registry: str | Path) -> Path:
    """Copy a checked definition into a local registry without overwriting changes."""
    source = Path(source)
    try:
        raw = json.loads(source.read_text())
    except (OSError, ValueError) as exc:
        raise FamilyError(f"Cannot read family {source}: {exc}") from exc
    resolved = _validate(raw, source.resolve().parent)
    if raw["id"] in _BUILTINS:
        raise FamilyError("A custom family cannot replace a built-in family")
    directory = Path(registry)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{raw['id']}.json"
    if raw["backend"] == "source-conditional":
        raw["source_dir"] = os.path.relpath(resolved["source_dir"], directory.resolve())
    if "grammar_files" in raw:
        raw["grammar_files"] = [os.path.relpath(path, directory.resolve()) for path in resolved["grammar_files"]]
    text = json.dumps(raw, sort_keys=True, indent=2) + "\n"
    if target.exists() and target.read_text() != text:
        raise FamilyError(f"A different family definition already exists at {target}")
    target.write_text(text)
    return target


get_family = load_family
list_families = builtin_families
