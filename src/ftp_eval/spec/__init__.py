"""The contracts. Read this first.

Three abstract base classes and the shapes they exchange. Each one is
actually subclassed by real code -- an ABC nobody implements is not a
contract, it is a second place to edit -- and
``tests/test_layering.py`` fails if that stops being true.

The picture
--------------------------------------------------------------------------

::

    Benchmark                    a folder per participant
    (a folder of Lean files)     (plain Submission objects)
        │                             │
        └──────────┬──────────────────┘
                   ▼
              grade_contest        runs every answer through the stages
                   │
      ┌────────────┼────────────┐
      ▼            ▼            ▼
   Stage ①      Stage ②      Stage ③
   MATCH        COMPILE      REPORT
   text only    the prover   statistics
      │            │            │
      └────────────┴────────────┘
                   ▼
            ArtifactWriter
            (the results folder)

--------------------------------------------------------------------------
The three contracts
--------------------------------------------------------------------------

:class:`~ftp_eval.spec.stage.GradingStage`
    The uniform shape of a stage: it declares its ``id``, what it
    ``requires``, and returns a :class:`StageResult` rather than raising.
    That uniformity is what makes the pipeline a loop instead of three
    special cases, and it is why ``NOT_RUN`` is a first-class outcome --
    a stage that could not run is not evidence either way.

    Implemented by :class:`~ftp_eval.proving.grading.pipeline.MatchStage`
    and :class:`~ftp_eval.proving.grading.pipeline.CompileStage`. This is
    the contract with more than one implementation, which is what an ABC
    is really for.

:class:`~ftp_eval.spec.benchmark.Benchmark`
    What a problem set provides: ids in a stable order, sources, a content
    hash per problem, per-problem metadata, a manifest. Implemented by
    :class:`~ftp_eval.proving.grading.contest.ProblemSet` -- a folder of
    Lean files, one theorem per file.

:class:`~ftp_eval.spec.artifacts.ArtifactWriter`
    What a grading run must record. The requirements live in the method
    docstrings because they are design decisions, not formatting: both the
    *declared* and the *observed* toolchain, every answer's conclusion at
    every stage it reached, and the exact source any prover was given.
    Implemented by
    :class:`~ftp_eval.proving.grading.artifacts.RunDirectory`.

Submissions have no ABC. A submission is one participant's answers keyed
by problem id -- :class:`~ftp_eval.proving.grading.contest.Submission`, a
plain dataclass. There was an ABC for it and nothing ever implemented it,
so it is gone; a dataclass that carries no behaviour needs no contract.

What is deliberately *not* here
--------------------------------------------------------------------------

The verifier interface. A prover is not part of the grading pipeline's
contract -- both APIs talk to one, so it lives a layer down in
``ftp_eval.backends``.
"""

from __future__ import annotations

from .artifacts import ArtifactWriter
from .benchmark import Benchmark, BenchmarkProblem
from .stage import GradingStage, StageContext, StageId, StageResult, StageStatus

__all__ = [
    "Benchmark",
    "BenchmarkProblem",
    "GradingStage",
    "StageContext",
    "StageResult",
    "StageStatus",
    "StageId",
    "ArtifactWriter",
]
