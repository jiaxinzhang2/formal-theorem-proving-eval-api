"""Stage 2: does the prover accept the proof?

Not "does ``answer.lean`` compile". A participant's file compiling proves
only that the participant wrote valid Lean -- it says nothing about which
proposition got proved, and an answer that restated the theorem compiles
perfectly.

So what is compiled is a **probe**: the *theorem file's* proposition, with
the *answer's* proof term attached, over the *theorem file's* definitions.
If that typechecks, the answer proves the theorem. This is the only stage
that can say so definitively, and it is why stage 1's text comparison can
afford to be conservative: a provably equivalent restatement that stage 1
flags is settled here.

Two compiles are available and they answer different questions:

* ``confirm`` (the default) -- the probe above. The one that grades.
* ``standalone`` -- the participant's file as submitted. Useful for
  telling a participant *why* their file is broken, but it must never be
  the grading verdict, for the reason above.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..matching import SubmissionMatcher
from ...types import StatementTask, Status
from .stages import StageStatus

__all__ = ["CompileMode", "CompileOutcome", "compile_answer", "compile_standalone"]


class CompileMode(str, Enum):
    #: The theorem's proposition + the answer's proof. Grades.
    CONFIRM = "confirm"
    #: The participant's file as submitted. Diagnostic only.
    STANDALONE = "standalone"


@dataclass
class CompileOutcome:
    """What the prover said."""

    status: StageStatus
    detail: str = ""
    mode: CompileMode = CompileMode.CONFIRM
    compile_time_s: float | None = None
    #: The exact source handed to the prover, kept so a disputed verdict
    #: can be reproduced by hand.
    source: str = ""
    diagnostics: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status is StageStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "mode": self.mode.value,
            "compile_time_s": self.compile_time_s,
            "diagnostics": list(self.diagnostics[:5]),
        }


def compile_answer(
    verifier: Any,
    problem_source: str,
    answer_source: str,
    *,
    target: str | None = None,
    timeout_s: float = 300.0,
) -> CompileOutcome:
    """Compile the confirmation probe. Never raises.

    ``StageStatus.NOT_RUN`` when the probe could not be assembled or the
    prover could not run -- which must be read as "not established", never
    as either verdict.
    """
    if verifier is None:
        return CompileOutcome(StageStatus.NOT_RUN, "no prover configured")

    matcher = SubmissionMatcher(verifier, timeout_s=timeout_s)
    source = matcher.build_confirmation_source(problem_source, answer_source, target=target)
    if source is None:
        return CompileOutcome(
            StageStatus.NOT_RUN,
            "could not assemble the probe: no proof term to test, or the "
            "answer(...) holes do not line up",
        )

    task = StatementTask(
        task_id=target or "probe",
        informal_statement="",
        formal_statement="theorem ftp_eval_match_probe : True",
        header="",
        language=getattr(verifier, "language", "lean4"),
    )
    started = time.monotonic()
    result = verifier.probe(task, source, timeout_s=timeout_s)
    elapsed = time.monotonic() - started
    diagnostics = tuple(d.message for d in result.diagnostics if d.message)[:5]

    if result.status is Status.VERIFIED:
        return CompileOutcome(
            StageStatus.PASSED,
            "the theorem's proposition typechecks against the answer's proof",
            compile_time_s=elapsed,
            source=source,
        )
    if result.status in (Status.ERROR, Status.SKIPPED):
        # The prover broke, or was not applicable. Not the answer's fault,
        # and not evidence either way.
        return CompileOutcome(
            StageStatus.NOT_RUN,
            "the probe could not run (%s)" % result.status.value,
            compile_time_s=elapsed,
            source=source,
            diagnostics=diagnostics,
        )
    return CompileOutcome(
        StageStatus.FAILED,
        "the answer's proof does not close the theorem's proposition (%s)"
        % (diagnostics[0][:160] if diagnostics else result.status.value),
        compile_time_s=elapsed,
        source=source,
        diagnostics=diagnostics,
    )


def compile_standalone(
    verifier: Any, answer_source: str, *, timeout_s: float = 300.0
) -> CompileOutcome:
    """Compile the participant's file as submitted. Diagnostic only.

    Tells a participant why their file is broken. It is **not** a grading
    verdict: a file that compiles has proved whatever it happens to state,
    which is exactly the confusion this pipeline exists to remove.
    """
    if verifier is None:
        return CompileOutcome(StageStatus.NOT_RUN, "no prover configured", CompileMode.STANDALONE)

    task = StatementTask(
        task_id="standalone",
        informal_statement="",
        formal_statement="theorem ftp_eval_standalone : True",
        header="",
        language=getattr(verifier, "language", "lean4"),
    )
    started = time.monotonic()
    result = verifier.probe(task, answer_source, timeout_s=timeout_s)
    elapsed = time.monotonic() - started
    diagnostics = tuple(d.message for d in result.diagnostics if d.message)[:5]

    status = {
        Status.VERIFIED: StageStatus.PASSED,
        Status.ERROR: StageStatus.NOT_RUN,
        Status.SKIPPED: StageStatus.NOT_RUN,
    }.get(result.status, StageStatus.FAILED)
    return CompileOutcome(
        status,
        "the submitted file compiles" if status is StageStatus.PASSED
        else (diagnostics[0][:160] if diagnostics else result.status.value),
        CompileMode.STANDALONE,
        compile_time_s=elapsed,
        source=answer_source,
        diagnostics=diagnostics,
    )
