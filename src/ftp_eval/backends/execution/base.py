"""Digest-pinned jobs, results and the execution contract for local or cloud workers."""
from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


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
