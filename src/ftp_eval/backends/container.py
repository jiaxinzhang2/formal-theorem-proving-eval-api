"""Digest-pinned container jobs with read-only inputs and bounded output transfer.

An AWS dispatcher can implement ContainerExecutor without changing the grading
API. Workers never receive writable host mounts, credentials or a Docker socket.
"""
from __future__ import annotations

import abc
import io
import re
import shutil
import shlex
import subprocess
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from .verifier import BackendUnavailable, VerifierError


def pinned_image(image: str) -> bool:
    return bool(re.fullmatch(r"(?:[A-Za-z0-9./:_-]+@)?sha256:[0-9a-f]{64}", image))


@dataclass(frozen=True)
class ContainerJob:
    image: str
    command: tuple[str, ...]
    mounts: Mapping[str, Path] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    outputs: Mapping[str, str] = field(default_factory=dict)
    timeout_s: float = 300.0
    memory_mb: int = 8192
    cpus: float = 2.0
    pids: int = 128
    output_limit_mb: int = 128
    log_limit_mb: int = 8

    def __post_init__(self) -> None:
        if not pinned_image(self.image):
            raise ValueError("container image must be pinned by sha256 digest")
        if not self.command or min(self.timeout_s, self.memory_mb, self.cpus, self.pids,
                                   self.output_limit_mb, self.log_limit_mb) <= 0:
            raise ValueError("container command and positive resource limits are required")
        for target in self.mounts:
            if not re.fullmatch(r"/modules/[0-9]+", target):
                raise ValueError("input mounts must use /modules/<index>")
        if any(not re.fullmatch(r"/tmp/[A-Za-z0-9_.-]+", p) for p in self.outputs.values()):
            raise ValueError("output files must be directly under /tmp")
        if len(set(self.outputs.values())) != len(self.outputs):
            raise ValueError("output filenames must be unique")


@dataclass
class ContainerResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    outputs: Mapping[str, bytes] = field(default_factory=dict)


class ContainerExecutor(abc.ABC):
    """Enforce job isolation/limits and transfer validated files only.

    A cloud implementation stages one job's inputs and removes the worker on
    timeout/interruption. It must not grant the submission cloud credentials.
    """

    @abc.abstractmethod
    def run(self, job: ContainerJob) -> ContainerResult | None:
        """None means the wall-clock deadline expired."""


class DockerExecutor(ContainerExecutor):
    def __init__(self, docker: str = "docker") -> None:
        self.docker = docker

    def _binary(self) -> str:
        binary = shutil.which(self.docker)
        if binary is None:
            raise BackendUnavailable("Docker CLI not found: %s" % self.docker)
        return binary

    def command(self, job: ContainerJob, name: str) -> list[str]:
        cmd = [self._binary(), "run", "--name", name, "--pull=never",
               "--network=none", "--read-only", "--cap-drop=ALL",
               "--security-opt=no-new-privileges", "--user=65532:65532",
               "--init", "--no-healthcheck", "--pids-limit=%d" % job.pids,
               "--memory=%dm" % job.memory_mb, "--memory-swap=%dm" % job.memory_mb,
               "--cpus=%s" % job.cpus, "--ulimit=fsize=%d:%d" % (
                   job.output_limit_mb * 1024 * 1024, job.output_limit_mb * 1024 * 1024),
               "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=%dm,mode=1777" % job.output_limit_mb,
               "--workdir=/project", "--entrypoint=/usr/bin/env"]
        for target, source in job.mounts.items():
            source = source.resolve(strict=True)
            if not source.is_dir() or any(c in str(source) for c in ",\r\n"):
                raise ValueError("invalid container input directory")
            cmd.extend(["--mount", "type=bind,src=%s,dst=%s,readonly" % (source, target)])
        for key, value in job.environment.items():
            if key != "LEAN_PATH":
                raise ValueError("unapproved worker environment variable: %s" % key)
            cmd.extend(["--env", "%s=%s" % (key, value)])
        worker_command = list(job.command)
        if job.outputs:
            # Transfer tmpfs files before the process exits: Docker discards
            # tmpfs contents when the container stops, so docker cp is too late.
            files = shlex.join([Path(path).name for path in job.outputs.values()])
            wrapper = ('"$@" > /tmp/ftp-eval.log 2>&1; ftp_status=$?; '
                       'cat /tmp/ftp-eval.log >&2; '
                       'if [ "$ftp_status" -eq 0 ]; then tar -cf - -C /tmp -- '
                       + files + '; fi; exit "$ftp_status"')
            worker_command = ["/bin/sh", "-c", wrapper, "ftp-eval-worker", *worker_command]
        return [*cmd, job.image, *worker_command]

    def _capture(self, command: Sequence[str], timeout_s: float, limit: int,
                 error_limit: int) -> tuple[int, bytes, bytes] | None:
        """Bound untrusted output on disk and in memory."""
        with tempfile.TemporaryFile() as log, tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(command, stdout=log, stderr=errors)
            deadline = time.monotonic() + timeout_s
            try:
                while proc.poll() is None:
                    if log.seek(0, 2) > limit or errors.seek(0, 2) > error_limit:
                        raise VerifierError("container output exceeded its resource limit")
                    if time.monotonic() >= deadline:
                        return None
                    time.sleep(0.05)
                if log.seek(0, 2) > limit or errors.seek(0, 2) > error_limit:
                    raise VerifierError("container output exceeded its resource limit")
                log.seek(0)
                errors.seek(0)
                return proc.returncode, log.read(), errors.read()
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()

    @staticmethod
    def read_outputs(archive: bytes, limit: int, requested: Mapping[str, str]) -> dict[str, bytes]:
        """Validate the worker's tar stream without extracting paths or links."""
        if len(archive) > limit + 1024 * 1024:
            raise VerifierError("container output exceeded its resource limit")
        outputs: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            expected = {Path(path).name: key for key, path in requested.items()}
            for member in tar:
                if member.name not in expected or not member.isfile() or member.size > limit:
                    raise VerifierError("invalid container output artifact")
                handle = tar.extractfile(member)
                if handle is None:
                    raise VerifierError("container output artifact missing")
                payload = handle.read(limit + 1)
                if len(payload) != member.size:
                    raise VerifierError("invalid container output artifact")
                outputs[expected.pop(member.name)] = payload
            if expected:
                raise VerifierError("container did not produce the requested artifact")
        return outputs

    def run(self, job: ContainerJob) -> ContainerResult | None:
        name = "ftp-eval-" + uuid.uuid4().hex
        binary = self._binary()
        try:
            limit = job.output_limit_mb * 1024 * 1024
            log_limit = job.log_limit_mb * 1024 * 1024
            captured = self._capture(self.command(job, name), job.timeout_s,
                                     limit + 1024 * 1024 if job.outputs else log_limit,
                                     log_limit)
            if captured is None:
                return None
            status, log, errors = captured
            error_text = errors.decode("utf-8", errors="replace")
            text = "" if job.outputs else log.decode("utf-8", errors="replace")
            if status in (125, 126, 127):
                raise BackendUnavailable("Docker worker could not start: " + (text + error_text)[-2000:])
            outputs = {}
            if status == 0 and job.outputs:
                outputs = self.read_outputs(log, limit, job.outputs)
            return ContainerResult(status, stdout=text, stderr=error_text, outputs=outputs)
        finally:
            cleanup = subprocess.run([binary, "rm", "--force", name], capture_output=True,
                                     timeout=30, check=False)
            if cleanup.returncode and b"No such container" not in cleanup.stderr:
                raise VerifierError("Docker worker cleanup failed: " + cleanup.stderr.decode(errors="replace")[-1000:])
