"""Where conclusions go.

A grading run's output has to answer a dispute months later without
re-running anything, so the contract is about completeness rather than
format: every stage's conclusion, the exact source any prover saw, and
enough provenance to know which problem set and which toolchain produced
the numbers.
"""

from __future__ import annotations

import abc
from typing import Any, Sequence

__all__ = ["ArtifactWriter"]


class ArtifactWriter(abc.ABC):
    """Records a grading run.

    Implemented by :class:`~ftp_eval.proving.grading.artifacts.RunDirectory`, which
    writes the folder layout documented in that module.
    """

    @abc.abstractmethod
    def write_manifest(self, **fields: Any) -> None:
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
    def write_report(self, statistics: Any, **fields: Any) -> None:
        """The aggregate, over correct and incorrect answers alike."""

    def write_probe(  # noqa: B027 - an optional hook, not an abstract method
        self, answer: Any, source: str, log: str = ""
    ) -> None:
        """The exact text a prover was given, and what it said back.

        Concrete and empty rather than abstract: a writer that records no
        probes is still a valid writer, and making every implementation
        stub this out would be noise. Strongly recommended all the same --
        a verdict nobody can reproduce is a verdict nobody can appeal.
        """
