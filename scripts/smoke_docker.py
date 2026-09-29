"""Validate a pinned organizer image against real Lean and Docker boundaries.

Run on the trusted Linux supervisor: PYTHONPATH=src python scripts/smoke_docker.py IMAGE.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from ftp_eval.backends.execution.base import ContainerJob
from ftp_eval.backends.execution.docker import DockerExecutor
from ftp_eval.backends.lean4_docker import Lean4DockerVerifier
from ftp_eval.backends.types import StatementTask
from ftp_eval.proving.running.inputs import ProblemSet, Submission
from ftp_eval.proving.running.pipeline import grade_contest
from ftp_eval.proving.running.manifest import BenchmarkManifest
from ftp_eval.proving.running.problem_health import check_problem_health


def main(image: str) -> None:
    executor = DockerExecutor()
    artifact = executor.run(ContainerJob(image, ("sh", "-c", "printf proof-bytes > /tmp/result.olean"),
                                         outputs={"olean": "/tmp/result.olean"}, timeout_s=30))
    assert artifact and artifact.outputs["olean"] == b"proof-bytes"
    isolation = executor.run(ContainerJob(image, ("sh", "-c",
        'test "$(id -u)" = 65532 && ! touch /project/ftp-cannot-write && '
        'test ! -e /var/run/docker.sock && test ! -e /root/.aws && '
        'grep -q "NoNewPrivs:[[:space:]]*1" /proc/self/status'), timeout_s=30))
    assert isolation and isolation.returncode == 0
    assert executor.run(ContainerJob(image, ("sleep", "5"), timeout_s=0.2)) is None
    with tempfile.TemporaryDirectory(prefix="ftp-eval-smoke-") as storage:
        verifier = Lean4DockerVerifier(image=image, workspace=storage)
        assert verifier.info().available, verifier.info().detail
        assert verifier.supports_module_builds()
        env = verifier.environment_metadata
        manifest = BenchmarkManifest(lean=env["lean_toolchain"], raw={
            "toolchain": {"lean": env["lean_toolchain"], "lake_manifest_sha256": env["lake_manifest_sha256"], "image_digest": image},
            "problems": {"P001": {"gold_arguments": ["2 + 2"]}},
        })
        problem = "namespace Problem\ndef Target (n : Nat) : Prop := n = 4\naxiom oracle : (4 : Nat) = 4\nend Problem\n"
        good = "import FtpEvalBench.P001\nnamespace Submission\ndef value : Nat := 4\ntheorem helper : value = 4 := rfl\ntheorem solution : Problem.Target value := helper\nend Submission\n"
        submissions = [Submission("good", {"P001": good}),
                       Submission("wrong", {"P001": good.replace("value : Nat := 4", "value : Nat := 0")}),
                       Submission("axiom", {"P001": good.replace("value = 4 := rfl", "value = 4 := Problem.oracle")})]
        result = grade_contest(ProblemSet({"P001": problem}, manifest=manifest), submissions,
                               verifier=verifier, output_dir=Path(storage) / "runs", run_id="smoke")
        assert result.grades()[0].solved, result.grades()[0].to_dict()
        assert result.grades()[1].refused_at == "kernel"
        assert result.grades()[2].refused_at == "axioms"
        assert any(e["stage"] == "replay" and e["status"] == "passed" for e in result.grades()[0].stages)
        assert any(e["stage"] == "dependencies" and e["status"] == "passed" for e in result.grades()[0].stages)
        # Rewriting addition after the trusted Goal is built must not rewrite gold.
        attack_problem = problem.replace("n = 4", "n = 0")
        attack = ('import FtpEvalBench.P001\nnamespace Submission\n'
                  'macro_rules | `(term| $a + $b) => `(term| (0 : Nat))\n'
                  'theorem solution : Problem.Target (2 + 2) := rfl\nend Submission\n')
        attacked = grade_contest(ProblemSet({"P001": attack_problem}, manifest=manifest),
                                 [Submission("macro", {"P001": attack})], verifier=verifier)
        assert not attacked.grades()[0].solved
        assert attacked.grades()[0].refused_at == "interface", attacked.to_dict()
        # Exercise Goal isolation independently of the earlier syntax refusal:
        # bypass the text screen only in this trusted regression harness.
        from unittest.mock import patch
        from ftp_eval.proving.checking.interface import InterfaceReport
        with patch("ftp_eval.proving.checking.evaluator.check_interface", return_value=InterfaceReport("P001", submitted_arguments=("(2 + 2)",))):
            isolated = grade_contest(ProblemSet({"P001": attack_problem}, manifest=manifest),
                                    [Submission("macro", {"P001": attack})], verifier=verifier)
        assert not isolated.grades()[0].solved
        assert isolated.grades()[0].build and isolated.grades()[0].build.failed_module.endswith(".Check"), isolated.to_dict()
        health_results = []
        for predicate in ("True", "n = 1"):
            source = "namespace Problem\ndef Target (n : Nat) : Prop := %s\nend Problem\n" % predicate
            health = check_problem_health(verifier, StatementTask("PHealth", "", source), module="FtpEvalBench.PHealth")
            assert health.probed
            assert health.ungradeable == (predicate == "True"), health.to_dict()
            health_results.append({"target": predicate, "ungradeable": health.ungradeable})
        print(json.dumps({"grades": [{"participant": g.participant, "solved": g.solved, "refused_at": g.refused_at} for g in result.grades()],
                          "gold_macro_attack_refused_at": attacked.grades()[0].refused_at,
                          "gold_isolation_refused_at": isolated.grades()[0].refused_at,
                          "health": health_results, "isolation": "passed", "artifact_transfer": "passed", "timeout": "passed"}))


if __name__ == "__main__":
    main(sys.argv[1])
