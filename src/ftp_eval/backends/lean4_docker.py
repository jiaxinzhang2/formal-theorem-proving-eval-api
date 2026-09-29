"""Frozen-module grading in Linux containers with separate read-only kernel replay."""
from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from .execution.base import ContainerExecutor, ContainerJob, pinned_image
from .execution.docker import DockerExecutor
from .lean4 import Lean4Verifier
from .types import BackendInfo, ProofAttempt, ProofTask
from .verifier import BackendUnavailable, RawVerdict


class Lean4DockerVerifier(Lean4Verifier):
    """Organizer image: built Lake project at /project, tools on PATH.

    Inherited module methods only stage files and orchestrate calls. All tool
    execution, including capability probes, replay and dependency extraction,
    dispatches through this class's ``_run_tool`` and the container executor.
    ``project_dir`` is host artifact storage, never a native Lake working dir.
    """
    name = "lean4-docker"
    requires_strict_environment = True
    requires_replay = True

    def __init__(self, *, image: str = "", executor: ContainerExecutor | None = None,
                 docker: str = "docker", workspace: str | None = None,
                 memory_mb: int = 8192, cpus: float = 2.0, pids: int = 128,
                 max_heartbeats: int = 400_000, **config: Any) -> None:
        if image and not pinned_image(image):
            raise ValueError("lean4-docker image must be pinned by sha256 digest")
        if min(memory_mb, cpus, pids, max_heartbeats) <= 0:
            raise ValueError("positive worker resource limits are required")
        if set(config) - {"keep_sources"}:
            raise ValueError("unsupported container backend options: %s" % ", ".join(sorted(config)))
        storage = Path(workspace) if workspace else Path(tempfile.gettempdir()) / ("ftp-eval-" + uuid.uuid4().hex)
        storage.mkdir(parents=True, exist_ok=True)
        super().__init__(project_dir=storage, memory_mb=memory_mb, max_heartbeats=max_heartbeats,
                         recheck=True, **config)
        self.image = image
        self.executor = executor or DockerExecutor(docker)
        self.cpus, self.pids = cpus, pids
        self.environment_metadata: dict[str, Any] = {}
        self._container_info: BackendInfo | None = None

    def _job(self, command: Sequence[str], *, mounts: Mapping[str, Path] | None = None,
             environment: Mapping[str, str] | None = None, outputs: Mapping[str, str] | None = None,
             timeout_s: float = 300.0) -> ContainerJob:
        return ContainerJob(self.image, tuple(command), mounts or {}, environment or {}, outputs or {},
                            timeout_s=timeout_s, memory_mb=self.memory_mb or 8192,
                            cpus=self.cpus, pids=self.pids)

    def info(self) -> BackendInfo:
        if not self.image:
            return BackendInfo(self.name, self.language, False, detail="supply image=<immutable sha256 digest>")
        if self._container_info is not None:
            return self._container_info
        try:
            version = self.executor.run(self._job(["lake", "env", "lean", "--version"], timeout_s=120))
            toolchain = self.executor.run(self._job(["cat", "/project/lean-toolchain"], timeout_s=30))
            manifest = self.executor.run(self._job(["cat", "/project/lake-manifest.json"], timeout_s=30))
            if (version is None or version.returncode or toolchain is None or toolchain.returncode
                    or manifest is None or manifest.returncode):
                raise BackendUnavailable("worker image must contain a built /project, toolchain and dependency manifest")
            data = json.loads(manifest.stdout)
            configuration = self.executor.run(self._job(["sh", "-c", "if test -f /project/lakefile.toml; then cat /project/lakefile.toml; else cat /project/lakefile.lean; fi"], timeout_s=30))
            self._image_library_prefixes: set[str] = set()
            if configuration is not None and configuration.returncode == 0:
                import re
                text = configuration.stdout
                self._image_library_prefixes.update(re.findall(r"\blean_lib\s+([A-Za-z_][A-Za-z0-9_]*)", text))
                for block in re.split(r"(?m)^\s*\[\[lean_lib\]\]\s*", text)[1:]:
                    match = re.search(r'(?m)^\s*name\s*=\s*"([^".]+)', re.split(r"(?m)^\s*\[", block)[0])
                    if match:
                        self._image_library_prefixes.add(match.group(1))
            self.environment_metadata = {
                "image_digest": self.image, "lean_toolchain": toolchain.stdout.strip(),
                "lake_manifest_sha256": hashlib.sha256(manifest.stdout.encode("utf-8")).hexdigest(),
                "sandbox": "read-only-linux-container", "kernel_replay": "leanchecker --fresh",
                "worker_limits": {"memory_mb": self.memory_mb, "cpus": self.cpus, "pids": self.pids},
            }
            for package in data.get("packages", []):
                if str(package.get("name", "")).lower() == "mathlib":
                    self.environment_metadata["mathlib_rev"] = str(package.get("rev") or "")
            self._container_info = BackendInfo(self.name, self.language, True, version=version.stdout.strip(),
                                                supports=("module_builds", "kernel_replay", "axiom_audit", "os_sandbox"))
            return self._container_info
        except Exception as exc:
            return BackendInfo(self.name, self.language, False, detail=str(exc))

    def cache_environment(self) -> Mapping[str, Any]:
        return {"image_digest": self.image}

    def library_prefixes(self) -> set[str]:
        self.info()
        return getattr(self, "_image_library_prefixes", set())

    def _verify(self, task: ProofTask, attempt: ProofAttempt, source: str,
                timeout_s: float) -> RawVerdict:
        raise BackendUnavailable("lean4-docker supports frozen module grading; use evaluate_submission or evaluate_benchmark")

    def _run_tool(self, tool: str, arguments: Sequence[str], search: Sequence[Path], timeout_s: float) -> Any:
        mounts = {"/modules/%d" % i: home for i, home in enumerate(search)}

        def container_path(path: str) -> str:
            for target, home in mounts.items():
                try:
                    relative = Path(path).relative_to(home)
                except ValueError:
                    continue
                return target + "/" + relative.as_posix()
            raise ValueError("tool input is outside this answer's module directories")

        args, outputs = [], {}
        output_path = None
        index = 0
        while index < len(arguments):
            arg = arguments[index]
            if arg == "-o":
                output_path = Path(arguments[index + 1])
                args.extend(["-o", "/tmp/result.olean"])
                outputs["olean"] = "/tmp/result.olean"
                index += 2
                continue
            args.append(container_path(arg) if arg.endswith(".lean") else arg)
            index += 1
        if tool == "lean":
            args.insert(0, "--memory=%d" % (self.memory_mb or 8192))
            source = next((arg for arg in args if arg.endswith(".lean")), None)
            if source is not None:
                args.insert(0, "--root=" + "/".join(source.split("/")[:3]))
        result = self.executor.run(self._job(["lake", "env", tool, *args], mounts=mounts,
                                            environment={"LEAN_PATH": ":".join(mounts)},
                                            outputs=outputs, timeout_s=timeout_s))
        if result is not None and result.returncode == 0 and output_path is not None:
            payload = result.outputs.get("olean")
            if payload is None:
                raise BackendUnavailable("worker did not return a compiled module artifact")
            output_path.write_bytes(payload)
        return result
