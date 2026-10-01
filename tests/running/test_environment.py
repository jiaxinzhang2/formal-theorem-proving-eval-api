"""Fail preflight before grading when module support or exact environment pins are missing."""
from __future__ import annotations

import pytest
from ftp_eval.proving.running import ProblemSet, Submission, grade_contest
from ftp_eval.proving.running.manifest import BenchmarkManifest
from ftp_eval.proving.running.environment import validate_environment
from ftp_eval.spec.stage import StageId


PROBLEM = "namespace Problem\ndef Target (n : Nat) : Prop := n = 1\nend Problem\n"


ANSWER = "import FtpEvalBench.P001\nnamespace Submission\nvariable (k : Nat)\ntheorem solution : Problem.Target 1 := rfl\nend Submission\n"


def test_module_capability_failure_aborts_before_answers(tmp_path):
    class Unsupported:
        def supports_module_builds(self):
            return False
        def build_modules(self, *args, **kwargs):
            raise AssertionError("must not grade an answer")
    with pytest.raises(ValueError, match="module"):
        grade_contest(ProblemSet({"P001": PROBLEM}), [Submission("alice", {"P001": ANSWER})],
                      verifier=Unsupported(), output_dir=tmp_path, run_id="r")
    assert (tmp_path / "r" / StageId.ENVIRONMENT.directory / "stage.json").exists()


def test_strict_environment_checks_all_pins_exactly():
    observed = {"available": True, "version": "Lean (version 4.29.0-rc2)",
                "lean_toolchain": "leanprover/lean4:v4.29.0-rc2", "mathlib_rev": "a" * 40,
                "lake_manifest_sha256": "b" * 64, "image_digest": "sha256:" + "c" * 64}
    manifest = BenchmarkManifest(lean="v4.29.0-rc2", mathlib_rev="a" * 40,
                                 raw={"toolchain": {"lake_manifest_sha256": "b" * 64, "image_digest": observed["image_digest"]}})
    assert validate_environment(manifest, observed) == []
    changed = {**observed, "mathlib_rev": "a" * 7, "lake_manifest_sha256": "d" * 64,
               "image_digest": "sha256:" + "e" * 64}
    errors = validate_environment(manifest, changed)
    assert len(errors) == 3


# -- what a score is worth ---------------------------------------------


def test_the_run_records_what_contained_the_answers():
    """A number produced without a sandbox is still a number; it means
    something different, and the difference has to travel with it."""
    from ftp_eval.backends.lean4 import Lean4Verifier
    from ftp_eval.backends.lean4_docker import Lean4DockerVerifier
    from ftp_eval.proving.running.environment import observed_toolchain

    native = observed_toolchain(Lean4Verifier(project_dir=None))
    assert (native["sandbox"], native["safe"]) == ("none", "none")

    container = Lean4DockerVerifier(image="", workspace=None)
    assert (container.sandbox, container.safe) == ("read-only-linux-container", "adversarial")


def test_an_unknown_backend_is_recorded_as_unsafe_rather_than_unstated():
    from ftp_eval.proving.running.environment import observed_toolchain

    class Bare:
        def info(self):
            from ftp_eval.backends.types import BackendInfo
            return BackendInfo("bare", "lean4", True)

    observed = observed_toolchain(Bare())
    assert (observed["sandbox"], observed["safe"]) == ("none", "none")
