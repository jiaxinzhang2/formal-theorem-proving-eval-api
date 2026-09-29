"""The contracts. Read this package to understand the system.

Pure interface: abstract base classes and the data shapes they exchange, no
logic. Every implementation in the package plugs into one of these, so this
is the short version of the whole design.

--------------------------------------------------------------------------
The picture
--------------------------------------------------------------------------

::

    Benchmark                    SubmissionSource
    (a folder of Lean files)     (a folder per participant)
        │                             │
        └──────────┬──────────────────┘
                   ▼
            ┌──────────────┐
            │  GradingRun  │   runs every answer through the stages
            └──────┬───────┘
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
The four contracts
--------------------------------------------------------------------------

:class:`~ftp_eval.spec.benchmark.Benchmark`
    What a problem set provides: ids, sources, per-problem metadata, a
    manifest. Implemented by
    :class:`~ftp_eval.proving.grading.sources.DirectoryBenchmark` -- a folder of
    Lean files, one theorem per file. Anything else that can answer those
    questions works too.

:class:`~ftp_eval.spec.benchmark.SubmissionSource`
    What a set of submissions provides: participants, and each one's
    answers keyed by problem id, plus the files that matched no problem.

:class:`~ftp_eval.spec.stage.GradingStage`
    The uniform shape of a stage. All three have it, which is what makes
    the pipeline a loop rather than three special cases. A stage declares
    its number and name, says whether it can run, and returns a
    :class:`~ftp_eval.spec.stage.StageResult`.

:class:`~ftp_eval.spec.artifacts.ArtifactWriter`
    Where conclusions go. Implemented by
    :class:`~ftp_eval.proving.grading.artifacts.RunDirectory`.

--------------------------------------------------------------------------
Two rules the contracts encode
--------------------------------------------------------------------------

**Stages run in order and stop early.** ``GradingStage.requires`` names the
stage that must have passed first. Compiling an answer that does not state
the theorem is wasted work, and a compile that *succeeds* on the wrong
theorem reads like a pass -- which is the confusion the whole pipeline
exists to remove.

**A stage that could not run says so.** :class:`StageStatus` has
``NOT_RUN`` as well as ``PASSED`` and ``FAILED``, and it is never collapsed
into either. "No prover was configured" and "the prover rejected it" are
different facts, and a grader that conflated them would report unverified
answers as correct.
"""

from __future__ import annotations

from .artifacts import ArtifactWriter
from .benchmark import Benchmark, BenchmarkProblem, SubmissionSource, SubmittedAnswer
from .stage import GradingStage, StageContext, StageResult, StageStatus, StageId

__all__ = [
    "Benchmark",
    "BenchmarkProblem",
    "SubmissionSource",
    "SubmittedAnswer",
    "GradingStage",
    "StageContext",
    "StageResult",
    "StageStatus",
    "StageId",
    "ArtifactWriter",
]
