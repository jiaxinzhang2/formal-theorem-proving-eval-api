"""The unified verifier interface every backend implements.

A backend has to answer exactly one question -- "does this proof close
this goal?" -- and answer it in the shared vocabulary of
:mod:`ftp_eval.types`. Everything that is the same for all provers
(source assembly, soundness screening, timing, error wrapping) lives
here so a new backend only has to implement :meth:`Verifier._verify`.
"""

from __future__ import annotations

import abc
import time
from typing import Any, Mapping, Sequence

from .modes import classify_failure, classify_success
from .proof_metrics import analyze_proof, analyze_statement
from .soundness import PATTERNS, HackClass, screen_source, strip_comments
from .tactics import extract_tactics
from .types import (
    Assembly,
    BackendInfo,
    Diagnostic,
    ErrorKind,
    ProbeKind,
    ProofAttempt,
    ProofTask,
    Severity,
    SoundnessReport,
    StatementTask,
    Status,
    VerificationResult,
)

__all__ = [
    "Verifier",
    "VerifierError",
    "BackendUnavailable",
    "RawVerdict",
    "assemble_source",
    "screen_soundness",
    "strip_comments",
    "PLACEHOLDER_PATTERNS",
]


class VerifierError(RuntimeError):
    """Raised for harness-side failures that are not the model's fault."""


class BackendUnavailable(VerifierError):
    """The prover or service this backend needs is not usable here."""


#: Kept for backwards compatibility; the live check set lives in
#: :mod:`ftp_eval.soundness`, which covers far more than placeholders.
PLACEHOLDER_PATTERNS: dict[str, tuple[str, ...]] = {
    language: tuple(
        p.pattern.pattern for p in soundness_patterns if p.hack_class is HackClass.PLACEHOLDER
    )
    for language, soundness_patterns in (
        (lang, [p for p in PATTERNS if lang in p.languages])
        for lang in ("lean4", "lean3", "coq", "isabelle", "axle")
    )
}


def assemble_source(task: ProofTask, attempt: ProofAttempt) -> str:
    """Build the source text to hand the prover, per the task's assembly."""
    proof = attempt.proof
    if task.assembly is Assembly.FULL_FILE:
        return proof
    header = task.header.rstrip()
    if task.assembly is Assembly.HEADER_PLUS_PROOF:
        parts = [header, proof]
    else:  # CONTINUE_STATEMENT
        statement = task.formal_statement.rstrip()
        # A model that emits `:= by ...` against a statement that already
        # ends in `:=` would otherwise produce `:= := by`.
        if statement.endswith(":=") and proof.lstrip().startswith(":="):
            proof = proof.lstrip()[2:]
        if proof.startswith("\n"):
            # The model chose to start on a new line; respect its layout,
            # since indentation is significant in tactic blocks.
            joined = statement + proof.rstrip()
        elif proof.strip():
            joined = statement + " " + proof.strip()
        else:
            joined = statement
        parts = [header, joined]
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


def screen_soundness(
    task: ProofTask,
    attempt: ProofAttempt,
    source: str,
    *,
    check_statement: bool = True,
) -> SoundnessReport:
    """Look for every known way a proof gets accepted without proving.

    Runs on the *assembled source*, before and independently of the
    prover, because passing the kernel is not the bar: ``sorry`` compiles
    with only a warning, and a declared ``axiom`` compiles with no
    complaint at all -- asserting the goal as an axiom is, to the kernel,
    a perfectly well-formed thing to do.

    Syntactic and therefore incomplete: see :mod:`ftp_eval.soundness` for
    the full check list and its limits. Backends that can interrogate the
    kernel add stronger evidence through
    :meth:`Verifier.extra_soundness_checks` -- the Lean backend's
    ``#print axioms`` audit catches a ``sorry`` reached through a helper
    lemma that no regex here would ever see.
    """
    # Under CONTINUE_STATEMENT the harness concatenated the statement
    # itself, so tampering and shadowing are impossible by construction.
    required = (
        task.formal_statement
        if check_statement and task.assembly is not Assembly.CONTINUE_STATEMENT
        else None
    )
    return screen_source(source, task.language, required_statement=required)


class Verifier(abc.ABC):
    """Base class for all backends.

    Subclasses implement :meth:`_verify` (and usually :meth:`info`), and
    get source assembly, soundness screening, timing and error handling
    for free. ``verify`` is the only method callers should use, and it
    must never raise: a broken prover is reported as ``Status.ERROR`` so
    that one bad task cannot abort a 500-task evaluation.
    """

    #: Registry name, e.g. ``"lean4"``.
    name: str = "unnamed"
    #: Source language this backend consumes, e.g. ``"lean4"``.
    language: str = "unknown"
    #: Whether this backend is safe to call from several threads at once.
    thread_safe: bool = True

    def __init__(self, *, check_soundness: bool = True, **config: Any) -> None:
        self.check_soundness = check_soundness
        self.config: Mapping[str, Any] = dict(config)

    # -- interface for subclasses ------------------------------------

    @abc.abstractmethod
    def _verify(self, task: ProofTask, attempt: ProofAttempt, source: str, timeout_s: float) -> "RawVerdict":
        """Run the prover on ``source`` and report what it said.

        Implementations should not interpret soundness or do their own
        timing; they translate prover output into a :class:`RawVerdict`.
        Raising :class:`VerifierError` is fine -- ``verify`` turns it
        into a ``Status.ERROR`` result.
        """

    def info(self) -> BackendInfo:
        """Describe this backend and whether it can run right now."""
        return BackendInfo(name=self.name, language=self.language, available=True)

    def extra_soundness_checks(
        self, task: ProofTask, attempt: ProofAttempt, source: str, verdict: "RawVerdict"
    ) -> SoundnessReport:
        """Backend-specific soundness evidence, merged with the screen.

        Override where the prover can be interrogated directly -- an
        axiom listing is far stronger evidence than a regex.
        """
        return SoundnessReport()

    def close(self) -> None:  # noqa: B027 - an optional hook, not an abstract method
        """Release any long-lived process or session. Idempotent.

        Deliberately concrete and empty: most backends spawn a fresh
        process per attempt and have nothing to release, so requiring
        them to implement this would be noise.
        """

    # -- the method callers use ---------------------------------------

    def verify(
        self,
        task: ProofTask,
        attempt: ProofAttempt,
        *,
        timeout_s: float = 300.0,
    ) -> VerificationResult:
        budget = task.timeout_s if task.timeout_s is not None else timeout_s
        started = time.monotonic()
        # Measured before anything can return early, so that a failure, a
        # timeout and a harness error all carry the same structural record
        # as a success. Comparing the shape of failures against the shape
        # of passes is most of the analytical value here.
        tactics = extract_tactics(attempt.proof, task.language)
        structure = analyze_proof(attempt.proof, task.language, tactics=tactics).to_dict()
        # The statement's own complexity travels with the result so solve
        # rate can be correlated against it -- the closest thing to a
        # difficulty axis available without human labels.
        structure.update(
            {
                "statement_" + key: value
                for key, value in analyze_statement(
                    task.formal_statement, task.language
                ).to_dict().items()
            }
        )

        def finish(
            status: Status,
            error_kind: ErrorKind | None = None,
            soundness: SoundnessReport | None = None,
            diagnostics: Sequence[Diagnostic] = (),
            raw: Mapping[str, Any] | None = None,
            compile_time_s: float | None = None,
        ) -> VerificationResult:
            # Both directions are classified here, once, so no caller has
            # to re-derive them and a pass and a failure are described in
            # equal detail.
            report = soundness or SoundnessReport()
            if status is Status.VERIFIED:
                failure_mode = None
                success_mode = classify_success(tactics, structure).value
            else:
                failure_mode = classify_failure(
                    status,
                    error_kind,
                    tuple(diagnostics),
                    proof=attempt.proof,
                    soundness_ok=report.ok,
                )
                failure_mode = failure_mode.value if failure_mode else None
                success_mode = None
            return VerificationResult(
                task_id=task.task_id,
                attempt_id=attempt.attempt_id or task.task_id,
                backend=self.name,
                status=status,
                error_kind=error_kind,
                soundness=report,
                diagnostics=tuple(diagnostics),
                failure_mode=failure_mode,
                success_mode=success_mode,
                wall_time_s=time.monotonic() - started,
                compile_time_s=compile_time_s,
                model=attempt.model,
                sample_index=attempt.sample_index,
                split=task.split,
                tactics=tactics,
                structure=structure,
                raw=dict(raw or {}),
            )

        if task.language != self.language:
            return finish(
                Status.SKIPPED,
                ErrorKind.HARNESS,
                diagnostics=[
                    Diagnostic(
                        Severity.ERROR,
                        "task language %r does not match backend %r (%s)"
                        % (task.language, self.name, self.language),
                        kind=ErrorKind.HARNESS,
                    )
                ],
            )

        if not attempt.proof.strip():
            # An empty completion is a real, common model failure; it is a
            # FAILED attempt, not a harness error.
            return finish(
                Status.FAILED,
                ErrorKind.INCOMPLETE,
                diagnostics=[
                    Diagnostic(Severity.ERROR, "attempt is empty", kind=ErrorKind.INCOMPLETE)
                ],
            )

        try:
            source = assemble_source(task, attempt)
        except Exception as exc:  # pragma: no cover - defensive
            return finish(
                Status.ERROR,
                ErrorKind.HARNESS,
                diagnostics=[Diagnostic(Severity.ERROR, "assembly failed: %s" % exc, kind=ErrorKind.HARNESS)],
            )

        screen = (
            screen_soundness(task, attempt, source)
            if self.check_soundness
            else SoundnessReport()
        )

        try:
            verdict = self._verify(task, attempt, source, budget)
        except BackendUnavailable as exc:
            return finish(
                Status.ERROR,
                ErrorKind.TOOLCHAIN,
                diagnostics=[Diagnostic(Severity.ERROR, str(exc), kind=ErrorKind.TOOLCHAIN)],
            )
        except VerifierError as exc:
            return finish(
                Status.ERROR,
                ErrorKind.HARNESS,
                diagnostics=[Diagnostic(Severity.ERROR, str(exc), kind=ErrorKind.HARNESS)],
            )
        except Exception as exc:  # pragma: no cover - defensive
            return finish(
                Status.ERROR,
                ErrorKind.HARNESS,
                diagnostics=[
                    Diagnostic(
                        Severity.ERROR,
                        "%s: %s" % (type(exc).__name__, exc),
                        kind=ErrorKind.HARNESS,
                    )
                ],
            )

        soundness = screen
        if self.check_soundness:
            soundness = soundness.merge(
                self.extra_soundness_checks(task, attempt, source, verdict)
            )

        status = verdict.status
        error_kind = verdict.error_kind
        if status is Status.VERIFIED and not soundness.ok:
            # Accepted by the prover but not a proof of what was asked.
            status, error_kind = Status.REJECTED, ErrorKind.SOUNDNESS

        return finish(
            status,
            error_kind,
            soundness,
            verdict.diagnostics,
            verdict.raw,
            verdict.compile_time_s,
        )

    # -- statement probes ---------------------------------------------

    def build_probe(self, task: StatementTask, kind: ProbeKind) -> str | None:
        """Build a source file that asks the prover one thing about a statement.

        Return ``None`` when this backend cannot construct that probe --
        the language is unsupported, or the statement does not parse well
        enough to take apart. ``None`` makes the corresponding check
        report "did not run", which is honest; a guessed probe would
        report a confident wrong answer.

        Implemented by :class:`~ftp_eval.backends.lean4.Lean4Verifier`.
        """
        return None

    def probe(
        self,
        task: StatementTask,
        source: str,
        *,
        timeout_s: float = 120.0,
    ) -> VerificationResult:
        """Compile an arbitrary source, skipping soundness screening.

        Probes are questions, not proof attempts. The elaboration probe
        deliberately submits a ``sorry`` body and the vacuity probe
        deliberately tries to derive ``False``; screening either one would
        reject every probe and tell us nothing. Callers must therefore not
        treat a probe's ``VERIFIED`` as a proof of anything except the
        property the probe was built to test.
        """
        budget = task.timeout_s if task.timeout_s is not None else timeout_s
        started = time.monotonic()
        proof_task = ProofTask(
            task_id=task.task_id,
            formal_statement=task.formal_statement,
            header=task.header,
            language=task.language,
            assembly=Assembly.FULL_FILE,
            split=task.split,
        )
        attempt = ProofAttempt(task_id=task.task_id, proof=source, attempt_id="%s#probe" % task.task_id)

        def finish(
            status: Status,
            error_kind: ErrorKind | None,
            diagnostics: Sequence[Diagnostic] = (),
            raw: Mapping[str, Any] | None = None,
        ) -> VerificationResult:
            return VerificationResult(
                task_id=task.task_id,
                attempt_id=attempt.attempt_id or task.task_id,
                backend=self.name,
                status=status,
                error_kind=error_kind,
                diagnostics=tuple(diagnostics),
                wall_time_s=time.monotonic() - started,
                split=task.split,
                raw=dict(raw or {}),
            )

        if task.language != self.language:
            return finish(
                Status.SKIPPED,
                ErrorKind.HARNESS,
                [
                    Diagnostic(
                        Severity.ERROR,
                        "statement language %r does not match backend %r"
                        % (task.language, self.name),
                        kind=ErrorKind.HARNESS,
                    )
                ],
            )

        try:
            verdict = self._verify(proof_task, attempt, source, budget)
        except BackendUnavailable as exc:
            return finish(
                Status.ERROR,
                ErrorKind.TOOLCHAIN,
                [Diagnostic(Severity.ERROR, str(exc), kind=ErrorKind.TOOLCHAIN)],
            )
        except VerifierError as exc:
            return finish(
                Status.ERROR,
                ErrorKind.HARNESS,
                [Diagnostic(Severity.ERROR, str(exc), kind=ErrorKind.HARNESS)],
            )
        except Exception as exc:  # pragma: no cover - defensive
            return finish(
                Status.ERROR,
                ErrorKind.HARNESS,
                [
                    Diagnostic(
                        Severity.ERROR,
                        "%s: %s" % (type(exc).__name__, exc),
                        kind=ErrorKind.HARNESS,
                    )
                ],
            )

        return finish(verdict.status, verdict.error_kind, verdict.diagnostics, verdict.raw)

    # -- convenience --------------------------------------------------

    def __enter__(self) -> "Verifier":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return "<%s name=%r language=%r>" % (type(self).__name__, self.name, self.language)


class RawVerdict:
    """A backend's report before soundness and timing are folded in."""

    __slots__ = ("status", "error_kind", "diagnostics", "raw", "compile_time_s")

    def __init__(
        self,
        status: Status,
        error_kind: ErrorKind | None = None,
        diagnostics: Sequence[Diagnostic] = (),
        raw: Mapping[str, Any] | None = None,
        compile_time_s: float | None = None,
    ) -> None:
        self.status = status
        self.error_kind = error_kind
        self.diagnostics = tuple(diagnostics)
        self.raw: Mapping[str, Any] = dict(raw or {})
        #: The prover's own time, excluding harness overhead. Backends that
        #: shell out should report the subprocess duration here; it is
        #: usually the dominant cost of a run and the thing worth tuning.
        self.compile_time_s = compile_time_s

    @classmethod
    def verified(
        cls, raw: Mapping[str, Any] | None = None, compile_time_s: float | None = None
    ) -> "RawVerdict":
        return cls(Status.VERIFIED, raw=raw, compile_time_s=compile_time_s)

    @classmethod
    def failed(
        cls,
        error_kind: ErrorKind = ErrorKind.UNKNOWN,
        diagnostics: Sequence[Diagnostic] = (),
        raw: Mapping[str, Any] | None = None,
        compile_time_s: float | None = None,
    ) -> "RawVerdict":
        return cls(Status.FAILED, error_kind, diagnostics, raw, compile_time_s)

    @classmethod
    def timeout(
        cls, raw: Mapping[str, Any] | None = None, compile_time_s: float | None = None
    ) -> "RawVerdict":
        return cls(
            Status.TIMEOUT,
            ErrorKind.TIMEOUT,
            [Diagnostic(Severity.ERROR, "verification timed out", kind=ErrorKind.TIMEOUT)],
            raw,
            compile_time_s,
        )

    def __repr__(self) -> str:
        return "RawVerdict(%s, %s)" % (self.status.value, self.error_kind)
