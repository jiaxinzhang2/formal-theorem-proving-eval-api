"""Where conclusions go.

A grading run's output has to answer a dispute months later without
re-running anything, so the contract is about completeness rather than
format: every stage's conclusion, the exact source any prover saw, and
enough provenance to know which problem set and which toolchain produced
the numbers.
"""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = ["ArtifactWriter"]


class ArtifactWriter(abc.ABC):
    """Records a grading run.

    Implemented by :class:`~ftp_eval.proving.running.run_directory.RunDirectory`, which
    writes the folder layout documented in that module.
    """

    @property
    def directory(self) -> Path | None:
        """Filesystem location when applicable; remote/in-memory writers return None."""
        return None

    @abc.abstractmethod
    def write_inputs(self, problems: Mapping[str, str], submissions: Sequence[Any]) -> None:
        """Freeze inputs before execution."""

    @abc.abstractmethod
    def write_modules(self, answer: Any) -> None:
        """Record generated module sources before execution."""

    @abc.abstractmethod
    def write_stage(self, problem_id: str, participant: str, stage: str, event: Mapping[str, Any]) -> None:
        """Persist one answer checkpoint before the next operation."""

    @abc.abstractmethod
    def write_run_stage(self, stage: str, status: str, payload: Any = None) -> None:
        """Persist a run-level checkpoint."""

    @abc.abstractmethod
    def set_state(self, status: str, **fields: Any) -> None:
        """Persist lifecycle state and run-level observations."""

    @abc.abstractmethod
    def write_manifest(
        self, *, problems: Mapping[str, str], participants: Sequence[str],
        backend: str | None, judge: str | None = None,
        problem_metadata: Mapping[str, Any] | None = None, benchmark: Any = None,
        observed_toolchain: Mapping[str, Any] | None = None,
        toolchain_warnings: Sequence[str] = (), extra: Mapping[str, Any] | None = None,
    ) -> None:
        """What was graded, with what, when.

        Must record at minimum: the problem set with content hashes, the
        participants, the backend, and both the *declared* and the
        *observed* toolchain. Recording only the declared one hides the
        case where a benchmark pinned to one Mathlib revision was graded
        against another, which makes the results irreproducible.
        """

    @abc.abstractmethod
    def write_answers(self, graded: Sequence[Any]) -> None:
        """Every answer's conclusion at every stage it reached."""

    @abc.abstractmethod
    def write_report(
        self, statistics: Any, *, leaderboard: Sequence[Mapping[str, Any]],
        graded: Sequence[Any],
    ) -> None:
        """The aggregate, over correct and incorrect answers alike."""
