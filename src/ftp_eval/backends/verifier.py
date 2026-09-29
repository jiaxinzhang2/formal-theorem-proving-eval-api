"""The unified verifier interface every backend implements.

A backend has to answer exactly one question -- "does this proof close
this goal?" -- and answer it in the shared vocabulary of
:mod:`ftp_eval.backends.types`. Everything that is the same for all provers
(source assembly, soundness screening, timing, error wrapping) lives
here so a new backend only has to implement :meth:`Verifier._verify`.
"""

from __future__ import annotations

import abc
import time
from typing import Any, Callable, Mapping, Sequence

from .soundness import screen_source
from .types import (
    ModuleBuild,
    ModuleSource,
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
]


class VerifierError(RuntimeError):
    """Raised for harness-side failures that are not the model's fault."""


class BackendUnavailable(VerifierError):
    """The prover or service this backend needs is not usable here."""


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

    Syntactic and therefore incomplete: see :mod:`ftp_eval.backends.soundness` for
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
    for free. ``verify`` is the low-level proof-attempt entry point: a broken
    prover is reported as ``Status.ERROR`` so one bad task cannot abort an
    evaluation. Frozen benchmark grading uses ``build_modules`` instead.
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

        def finish(
            status: Status,
            error_kind: ErrorKind | None = None,
            soundness: SoundnessReport | None = None,
            diagnostics: Sequence[Diagnostic] = (),
            raw: Mapping[str, Any] | None = None,
            compile_time_s: float | None = None,
        ) -> VerificationResult:
            # Deliberately no metrics here. A backend reports what the
            # prover said; deciding what to measure about a proof belongs
            # to the API that consumes it, so a verdict is passed through
            # `proving.analysis.metrics` to be filled in.
            report = soundness or SoundnessReport()
            return VerificationResult(
                task_id=task.task_id,
                attempt_id=attempt.attempt_id or task.task_id,
                backend=self.name,
                status=status,
                error_kind=error_kind,
                soundness=report,
                diagnostics=tuple(diagnostics),
                wall_time_s=time.monotonic() - started,
                compile_time_s=compile_time_s,
                model=attempt.model,
                sample_index=attempt.sample_index,
                split=task.split,
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

    def build_modules(
        self,
        modules: "Sequence[ModuleSource]",
        *,
        audit_declaration: str = "",
        timeout_s: float = 300.0,
        on_stage: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> "ModuleBuild | None":
        """Compile several modules in order, each able to import the last.

        Return ``None`` when this backend cannot do it. That is not a
        failure -- it is "did not run", and a caller must report it as such
        rather than as a pass or a rejection.

        The ordering is what makes a sealed problem possible: the first
        module is elaborated before the later ones exist, so nothing they
        declare can change what its propositions mean. ``audit_declaration``
        names the constant whose ``#print axioms`` listing to read back,
        because a term can have the right type and still not be a proof.

        Implemented by :class:`~ftp_eval.backends.lean4.Lean4Verifier`.
        """
        return None

    def verify_attempt(
        self, task: ProofTask, attempt: ProofAttempt, *, timeout_s: float = 300.0
    ) -> VerificationResult:
        """Check a proof attempt; this is not a frozen benchmark verdict.

        ``verify`` remains available for existing callers. Final benchmark
        acceptance is determined by ``evaluate_submission`` / ``evaluate_benchmark``.
        """
        return self.verify(task, attempt, timeout_s=timeout_s)

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
