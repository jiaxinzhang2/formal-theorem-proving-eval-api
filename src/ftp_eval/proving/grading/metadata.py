"""Problem and benchmark metadata: provenance, and the version it was checked against.

Two levels, because the fields belong at different levels and putting them
in one place guarantees one of them drifts:

* **Per problem** -- where it came from: the MathDB number, the source
  paper, the locator inside it, difficulty, who checked it and when. This
  lives *in the Lean file*, as a module doc comment, so that one file is
  one self-contained problem. A sidecar could drift from the statement it
  describes; a comment in the same file cannot.

* **Per benchmark** -- the toolchain: which Lean, which Mathlib revision,
  the benchmark's own name and version. These are the same for every
  problem in a set, so they live once in ``benchmark.json``.

And one thing is recorded at *grading* time rather than declared:
:func:`observed_toolchain` asks the backend what it actually is. A
benchmark that claims Mathlib ``abc123`` and a grader running ``def456``
will produce results nobody can reproduce, so the claim and the observation
are both written to ``run.json`` and a mismatch is reported.

The metadata block is a Lean module doc comment, which keeps the file valid
Lean::

    /-!
    # P001
    - mathdb_id: 392323
    - source: https://arxiv.org/abs/2608.13251
    - source_locator: Theorem 2.1
    - difficulty: research
    - checked: 2026-09-20
    -/
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..lean_file import parse_lean_file

__all__ = [
    "ProblemMetadata",
    "BenchmarkManifest",
    "parse_problem_metadata",
    "load_benchmark_manifest",
    "observed_toolchain",
    "METADATA_KEYS",
]

#: Keys recognized in a problem's metadata block. Unknown keys are kept in
#: ``extra`` rather than dropped -- a setter's own bookkeeping field is not
#: ours to discard.
METADATA_KEYS = (
    "mathdb_id",
    "prose",
    "problem_id",
    "source",
    "source_locator",
    "source_version",
    "difficulty",
    "author",
    "checked",
    "contamination",
    "notes",
)

#: `/-! ... -/` module doc comment, which is where the block lives.
_MODULE_DOC_RE = re.compile(r"/-!(?P<body>(?:.|\n)*?)-/", re.DOTALL)
#: `- key: value` or `key: value`, one per line.
_FIELD_RE = re.compile(r"^\s*[-*]?\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class ProblemMetadata:
    """One problem's provenance."""

    problem_id: str = ""
    #: The corresponding MathDB problem number, when the problem came from
    #: there. Kept as a string: identifiers are not arithmetic.
    mathdb_id: str = ""
    #: The natural-language problem this file formalizes. Optional for
    #: grading -- a proof check never reads it -- but `ftp-eval audit`
    #: cannot judge faithfulness without it.
    prose: str = ""
    source: str = ""
    source_locator: str = ""
    source_version: str = ""
    difficulty: str = ""
    author: str = ""
    checked: str = ""
    contamination: str = ""
    notes: str = ""
    #: From the Lean attributes rather than the comment block.
    categories: tuple[str, ...] = ()
    ams_tags: tuple[str, ...] = ()
    #: The theorem this file sets, fully qualified.
    theorem_name: str = ""
    #: Whether the statement has an ``answer(...)`` hole to fill.
    answer_holes: int = 0
    #: Keys the setter used that this does not recognize. Preserved.
    extra: Mapping[str, str] = field(default_factory=dict)

    @property
    def has_provenance(self) -> bool:
        """Whether the problem records where it came from at all."""
        return bool(self.mathdb_id or self.source)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "mathdb_id": self.mathdb_id,
            "source": self.source,
            "source_locator": self.source_locator,
            "source_version": self.source_version,
            "difficulty": self.difficulty,
            "author": self.author,
            "checked": self.checked,
            "contamination": self.contamination,
            "notes": self.notes,
            "categories": list(self.categories),
            "ams_tags": list(self.ams_tags),
            "theorem_name": self.theorem_name,
            "answer_holes": self.answer_holes,
            "extra": dict(self.extra),
        }


def parse_problem_metadata(source: str, *, problem_id: str = "") -> ProblemMetadata:
    """Read a problem file's metadata.

    Fields come from the ``/-! ... -/`` block; ``categories`` and
    ``ams_tags`` come from the Lean attributes, where formal-conjectures
    already puts them. Absent metadata yields empty fields rather than an
    error: a problem without provenance is still a problem, and the setter
    is told which ones are missing it rather than being blocked.
    """
    metadata = ProblemMetadata(problem_id=problem_id)

    blocks = _MODULE_DOC_RE.findall(source)
    fields: dict[str, str] = {}
    for block in blocks:
        for key, value in _FIELD_RE.findall(block):
            fields.setdefault(key.lower(), value.strip())

    extra: dict[str, str] = {}
    for key, value in fields.items():
        if key in METADATA_KEYS:
            # problem_id from the file block does not override the caller's,
            # which comes from the filename and is authoritative.
            if key == "problem_id" and problem_id:
                extra.setdefault("declared_problem_id", value)
                continue
            setattr(metadata, key, value)
        else:
            extra[key] = value
    metadata.extra = extra

    parsed = parse_lean_file(source)
    statements = parsed.statements()
    if statements:
        target = statements[0]
        metadata.theorem_name = target.qualified_name
        metadata.categories = target.categories
        metadata.ams_tags = target.ams_tags
        metadata.answer_holes = target.answer_holes
    return metadata


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
            "declared_toolchain": {"lean": self.lean, "mathlib_rev": self.mathlib_rev},
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


def observed_toolchain(verifier: Any) -> dict[str, Any]:
    """What the grader is actually running, asked of the backend.

    Declared versions are a claim; this is the observation. Recording both
    is what makes a result reproducible -- a benchmark pinned to one Mathlib
    revision and graded against another produces numbers nobody can
    repeat, and without this the discrepancy is invisible.
    """
    if verifier is None:
        return {"available": False, "detail": "no prover configured"}
    try:
        info = verifier.info()
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
    observed: dict[str, Any] = {
        "backend": info.name,
        "language": info.language,
        "available": info.available,
        "version": info.version,
        "detail": info.detail,
    }
    project = getattr(verifier, "project_dir", None)
    if project is not None:
        observed["project_dir"] = str(project)
        observed.update(_lean_project_versions(Path(project)))
    return observed


def _lean_project_versions(project_dir: Path) -> dict[str, Any]:
    """Read the pinned toolchain and Mathlib revision out of a Lean project.

    ``lean-toolchain`` and ``lake-manifest.json`` are where Lake records
    them, so this is the project's own answer rather than a guess.
    """
    out: dict[str, Any] = {}
    toolchain = project_dir / "lean-toolchain"
    if toolchain.is_file():
        try:
            out["lean_toolchain"] = toolchain.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    manifest = project_dir / "lake-manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return out
        for package in data.get("packages") or ():
            if str(package.get("name", "")).lower() == "mathlib":
                out["mathlib_rev"] = package.get("rev") or package.get("inputRev") or ""
                break
    return out


def reconcile_toolchain(
    declared: BenchmarkManifest, observed: Mapping[str, Any]
) -> list[str]:
    """Discrepancies between what the benchmark claims and what ran.

    Returned as warnings rather than raised: a mismatch is usually a
    deliberate re-run against a newer Mathlib, and the setter should be the
    one to decide. What must not happen is it going unrecorded.
    """
    warnings: list[str] = []
    if not declared.declares_toolchain:
        warnings.append(
            "the benchmark does not declare a toolchain, so these results cannot be "
            "tied to a Lean/Mathlib version. Add toolchain.lean and "
            "toolchain.mathlib_rev to benchmark.json."
        )
        return warnings
    if not observed.get("available"):
        # The benchmark pinned a toolchain and nothing checked it. Silence
        # here would read as agreement.
        warnings.append(
            "the benchmark declares Lean %r / Mathlib %r, but no prover ran, so the "
            "toolchain was not verified"
            % (declared.lean or "unset", declared.mathlib_rev or "unset")
        )
        return warnings
    actual_lean = str(observed.get("lean_toolchain") or observed.get("version") or "")
    if declared.lean and actual_lean and declared.lean not in actual_lean:
        warnings.append(
            "benchmark declares Lean %r but the grader ran %r" % (declared.lean, actual_lean)
        )
    actual_rev = str(observed.get("mathlib_rev") or "")
    if declared.mathlib_rev and actual_rev and not actual_rev.startswith(
        declared.mathlib_rev[:8]
    ):
        warnings.append(
            "benchmark declares Mathlib %r but the grader ran %r"
            % (declared.mathlib_rev, actual_rev)
        )
    return warnings
