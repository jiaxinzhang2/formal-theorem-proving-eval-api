"""Load benchmark declarations and deserialize the checking policy they select.

Policy semantics belong to checking.policy; this module only reads settings.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from ..checking.policy import ContestPolicy


@dataclass
class BenchmarkManifest:
    """The benchmark-wide declaration, from ``benchmark.json``."""

    name: str = ""
    version: str = ""
    language: str = "lean4"
    description: str = ""
    #: What the benchmark says it was built against.
    lean: str = ""
    mathlib_rev: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def declares_toolchain(self) -> bool:
        return bool(self.lean or self.mathlib_rev)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "language": self.language,
            "description": self.description,
            "declared_toolchain": {**(self.raw.get("toolchain", {}) if isinstance(self.raw.get("toolchain", {}), dict) else {}),
                                   "lean": self.lean, "mathlib_rev": self.mathlib_rev},
        }


def load_benchmark_manifest(root: str | os.PathLike[str]) -> BenchmarkManifest:
    """Load ``benchmark.json`` from a benchmark folder, if present.

    Missing is not an error -- a folder of Lean files is a usable benchmark
    on its own. What is lost is the toolchain declaration, and the run
    report says so rather than inventing one.
    """
    path = Path(root)
    if path.is_dir():
        path = path / "benchmark.json"
    if not path.is_file():
        return BenchmarkManifest()
    data = json.loads(path.read_text(encoding="utf-8"))
    toolchain = data.get("toolchain") or {}
    return BenchmarkManifest(
        name=str(data.get("name") or ""),
        version=str(data.get("version") or ""),
        language=str(data.get("language") or "lean4"),
        description=str(data.get("description") or ""),
        lean=str(toolchain.get("lean") or data.get("lean") or ""),
        mathlib_rev=str(toolchain.get("mathlib_rev") or data.get("mathlib_rev") or ""),
        raw=data,
    )


def policy_from_manifest(manifest: BenchmarkManifest) -> ContestPolicy:
    """Load explicit benchmark policy; reject misspelled or malformed knobs."""
    data = manifest.raw.get("policy", {})
    if not isinstance(data, dict):
        raise ValueError("benchmark policy must be an object")
    unknown = set(data) - {"allowed_imports", "allowed_axioms", "allow_global_instances", "isolate_builds", "require_replay"}
    if unknown:
        raise ValueError("unknown policy fields: %s" % ", ".join(sorted(unknown)))
    fields = dict(data)
    for name in ("allowed_imports", "allowed_axioms"):
        if name in fields:
            values = fields[name]
            if not isinstance(values, list) or not all(isinstance(x, str) and x for x in values):
                raise ValueError("%s must be a list of non-empty names" % name)
            fields[name] = tuple(values) if name == "allowed_imports" else frozenset(values)
    for name in ("allow_global_instances", "isolate_builds", "require_replay"):
        if name in fields and not isinstance(fields[name], bool):
            raise ValueError("%s must be a boolean" % name)
    return ContestPolicy(**fields)
