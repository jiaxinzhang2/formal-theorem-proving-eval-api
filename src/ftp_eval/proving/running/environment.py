"""Observe the running toolchain and validate or reconcile the declared pins."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping
from .manifest import BenchmarkManifest


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
    if getattr(verifier, "language", None) == "lean4":
        observed["compiler_options"] = {
            key: getattr(verifier, key)
            for key in ("memory_mb", "max_heartbeats", "extra_args")
            if hasattr(verifier, key)
        }
    project = getattr(verifier, "project_dir", None)
    if project is not None:
        observed["project_dir"] = str(project)
        observed.update(_lean_project_versions(Path(project)))
    observed.update(getattr(verifier, "environment_metadata", {}))
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
            manifest_bytes = manifest.read_bytes()
            out["lake_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
            data = json.loads(manifest_bytes)
        except (OSError, json.JSONDecodeError):
            return out
        for package in data.get("packages") or ():
            if str(package.get("name", "")).lower() == "mathlib":
                out["mathlib_rev"] = package.get("rev") or package.get("inputRev") or ""
                break
    return out


def _lean_version(value: str) -> str:
    match = re.search(r"(?<![\w.])v?(\d+\.\d+\.\d+(?:-rc\d+)?)(?![\w.-])", value)
    return match.group(1) if match else ""


def validate_environment(declared: BenchmarkManifest, observed: Mapping[str, Any]) -> list[str]:
    """Exact, fail-closed preflight for official evaluation; no prefix revision matches."""
    errors = []
    if not observed.get("available"):
        errors.append("verification backend unavailable")
    expected_lean = _lean_version(declared.lean)
    if not re.fullmatch(r"(?:leanprover/lean4:)?v?\d+\.\d+\.\d+(?:-rc\d+)?", declared.lean):
        errors.append("toolchain.lean must pin an exact Lean release")
    actual_lean = _lean_version(str(observed.get("version") or ""))
    if not actual_lean or actual_lean != expected_lean:
        errors.append("running Lean version does not match the declared release")
    project_lean = _lean_version(str(observed.get("lean_toolchain") or ""))
    if not project_lean or project_lean != expected_lean:
        errors.append("project lean-toolchain is missing or mismatched")
    actual_rev = str(observed.get("mathlib_rev") or "")
    if declared.mathlib_rev or actual_rev:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", declared.mathlib_rev):
            errors.append("toolchain.mathlib_rev must be a full commit SHA")
        if not actual_rev or actual_rev.lower() != declared.mathlib_rev.lower():
            errors.append("Mathlib revision is missing or mismatched")
    toolchain = declared.raw.get("toolchain", {})
    if not isinstance(toolchain, dict):
        errors.append("toolchain must be an object")
        return errors
    expected_manifest = str(toolchain.get("lake_manifest_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest):
        errors.append("toolchain.lake_manifest_sha256 must pin the complete dependency manifest")
    if observed.get("lake_manifest_sha256") != expected_manifest:
        errors.append("dependency manifest is missing or mismatched")
    if observed.get("image_digest"):
        if toolchain.get("image_digest") != observed["image_digest"]:
            errors.append("toolchain.image_digest must match the worker's immutable image")
    return errors


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
