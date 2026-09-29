"""Container policy, artifact validation and prevention of native Lean fallback."""
from __future__ import annotations

import io
import tarfile
from dataclasses import replace
import pytest
from ftp_eval.backends.execution import ContainerExecutor, ContainerJob, ContainerResult, DockerExecutor
from ftp_eval.backends.lean4 import Lean4Verifier
from ftp_eval.backends.lean4_docker import Lean4DockerVerifier
from ftp_eval.backends.verifier import VerifierError
from ftp_eval.proving.checking import read_interface_problem, grade_interface


PROBLEM = "namespace Problem\ndef Target (n : Nat) : Prop := n = 1\nend Problem\n"


ANSWER = "import FtpEvalBench.P001\nnamespace Submission\nvariable (k : Nat)\ntheorem solution : Problem.Target 1 := rfl\nend Submission\n"


def test_container_rejects_mutable_image_names():
    with pytest.raises(ValueError, match="pinned"):
        ContainerJob("ubuntu:latest", ("true",))


def test_container_output_transfer_rejects_symlinks():
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        member = tarfile.TarInfo("result.olean")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        tar.addfile(member)
    with pytest.raises(VerifierError, match="invalid"):
        DockerExecutor.read_outputs(archive.getvalue(), 100, {"olean": "/tmp/result.olean"})


def test_docker_module_builds_never_execute_native_lean(tmp_path, monkeypatch):
    class Worker(ContainerExecutor):
        def __init__(self):
            self.jobs = []
        def run(self, job):
            self.jobs.append(job)
            if job.command == ("cat", "/project/lean-toolchain"):
                return ContainerResult(0, stdout="leanprover/lean4:v4.29.0-rc2\n")
            if job.command == ("cat", "/project/lake-manifest.json"):
                return ContainerResult(0, stdout='{"packages": []}\n')
            if job.command[:2] == ("sh", "-c"):
                return ContainerResult(0, stdout='name = "worker"\n')
            assert job.command[:2] == ("lake", "env")
            if job.command[-1] == "--version":
                return ContainerResult(0, stdout="Lean (version 4.29.0-rc2)\n")
            if job.outputs:
                return ContainerResult(0, outputs={"olean": b"synthetic compiled module"})
            if job.command[2] == "leanchecker":
                return ContainerResult(0)
            return ContainerResult(0, stdout="'ftp_eval_target' does not depend on any axioms\n")
    worker = Worker()
    verifier = Lean4DockerVerifier(image="sha256:" + "a" * 64, executor=worker, workspace=str(tmp_path))
    monkeypatch.setattr("ftp_eval.backends.lean4.subprocess.run", lambda *args, **kwargs: pytest.fail("native subprocess must not execute a submission"))
    monkeypatch.setattr(Lean4Verifier, "_run_tool", lambda *args, **kwargs: pytest.fail("native tool runner must not be used"))
    monkeypatch.setattr(Lean4Verifier, "_lake_path", lambda *args, **kwargs: pytest.fail("host Lake must not be resolved"))
    problem = replace(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"), gold_arguments=("1",))
    verdict = grade_interface(problem, PROBLEM, ANSWER, verifier=verifier, answer_module="FtpSubmission.Test")
    assert verdict.solved
    assert any(job.command[:3] == ("lake", "env", "leanchecker") for job in worker.jobs)
    assert len([job for job in worker.jobs if job.outputs]) == 8  # four probes + four grading modules
    tools = [job for job in worker.jobs if job.command[:2] == ("lake", "env") and job.command[-1] != "--version"]
    assert all(job.mounts and all(name.startswith("/modules/") for name in job.mounts) for job in tools)
    assert verifier._module_capabilities == {"FtpEvalBench": True, "FtpSubmission": True}


def test_docker_command_has_only_read_only_host_mounts(tmp_path, monkeypatch):
    executor = DockerExecutor()
    monkeypatch.setattr(executor, "_binary", lambda: "docker")
    job = ContainerJob("sha256:" + "a" * 64, ("lake", "env", "lean"), mounts={"/modules/0": tmp_path})
    command = executor.command(job, "ftp-eval-test")
    assert "--read-only" in command and "--network=none" in command
    assert "--cap-drop=ALL" in command and "--security-opt=no-new-privileges" in command
    assert all(command[index + 1].endswith(",readonly") for index, value in enumerate(command) if value == "--mount")
