"""What a benchmark is, and what a set of submissions is.

A benchmark is defined by what it can answer, not by how it is stored. The
reference implementation is a folder of Lean files -- which is the format
this package recommends, for the reasons in ``benchmarks/README.md`` -- but
anything that can produce problem ids, sources and metadata will grade.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Sequence

__all__ = ["BenchmarkProblem", "Benchmark", "SubmittedAnswer", "SubmissionSource"]


@dataclass(frozen=True)
class BenchmarkProblem:
    """One problem, as the grader sees it.

    ``problem_id`` is the identity used everywhere downstream -- in
    submission filenames, in the results directory, in every report -- so it
    must be stable across runs. For a folder benchmark it is the file stem.
    """

    problem_id: str
    #: The complete Lean source the setter published.
    source: str
    #: Fully-qualified name of the theorem under test, when it is known
    #: ahead of grading. ``None`` lets stage ① infer it, which is safe for
    #: the one-theorem-per-file convention.
    target: str | None = None
    #: Provenance and classification. ``ProblemMetadata`` in practice; typed
    #: loosely here so the contract does not depend on the grading package.
    metadata: Any = None
    #: Where it came from on disk, for error messages.
    origin: str = ""

    @property
    def content_hash(self) -> str:
        import hashlib

        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()


class Benchmark(abc.ABC):
    """A set of problems to be answered.

    Implementations must be **stable within a run**: the same ``problem_id``
    must return the same source every time it is asked, or two answers could
    be graded against different versions of the same problem.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """A short identifier for this benchmark, recorded in every run."""

    @abc.abstractmethod
    def problem_ids(self) -> Sequence[str]:
        """Every problem id, in a stable order."""

    @abc.abstractmethod
    def problem(self, problem_id: str) -> BenchmarkProblem:
        """One problem. Raises ``KeyError`` for an unknown id."""

    def manifest_fields(self) -> Mapping[str, Any]:
        """Benchmark-wide declaration: version, toolchain, conventions.

        Named ``manifest_fields`` rather than ``manifest`` so an
        implementation is free to expose a typed ``manifest`` attribute --
        which the folder-backed one does, and which a method of the same
        name would shadow.

        Empty is allowed. What it costs is the ability to tie results to a
        Lean/Mathlib version, and the run report says so rather than
        inventing one.
        """
        return {}

    # -- provided ------------------------------------------------------

    def iter_problems(self) -> Iterator[BenchmarkProblem]:
        """Every problem, in the order ``problem_ids`` gives.

        Named ``iter_problems`` for the same reason ``manifest_fields`` is
        not ``manifest``: an implementation may hold the problems in an
        attribute called ``problems``, and a method of that name would be
        shadowed by it. Wiring the folder-backed implementation to this ABC
        is what surfaced both collisions.
        """
        for problem_id in self.problem_ids():
            yield self.problem(problem_id)

    def __len__(self) -> int:
        return len(self.problem_ids())

    def __contains__(self, problem_id: object) -> bool:
        return problem_id in set(self.problem_ids())


@dataclass(frozen=True)
class SubmittedAnswer:
    """One participant's answer to one problem."""

    participant: str
    problem_id: str
    source: str
    origin: str = ""


class SubmissionSource(abc.ABC):
    """The answers to be graded.

    ``unrecognized`` is part of the contract, not an afterthought. A
    participant who misnames a file has done work that would otherwise
    vanish silently, and a grader that cannot report it is one nobody can
    appeal to.
    """

    @abc.abstractmethod
    def participants(self) -> Sequence[str]:
        """Every participant, in a stable order."""

    @abc.abstractmethod
    def answers(self, participant: str) -> Sequence[SubmittedAnswer]:
        """One participant's answers, in problem-id order."""

    def unrecognized(self, participant: str) -> Sequence[str]:
        """Files that matched no problem id. Reported, never dropped."""
        return ()

    # -- provided ------------------------------------------------------

    def all_answers(self) -> Iterator[SubmittedAnswer]:
        for participant in self.participants():
            yield from self.answers(participant)

    def __len__(self) -> int:
        return sum(len(self.answers(p)) for p in self.participants())
