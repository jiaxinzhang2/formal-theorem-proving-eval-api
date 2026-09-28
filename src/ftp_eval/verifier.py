"""The unified verifier interface every backend implements.

A backend has to answer exactly one question -- "does this proof close
this goal?" -- and answer it in the shared vocabulary of
:mod:`ftp_eval.types`. Everything that is the same for all provers
(source assembly, soundness screening, timing, error wrapping) lives
here so a new backend only has to implement :meth:`Verifier._verify`.
"""

from __future__ import annotations

import abc
import re
import time
from typing import Any, Mapping, Sequence

from .types import (
    Assembly,
    BackendInfo,
    Diagnostic,
    ErrorKind,
    ProofAttempt,
    ProofTask,
    Severity,
    SoundnessReport,
    Status,
    VerificationResult,
)

__all__ = [
    "Verifier",
    "VerifierError",
    "BackendUnavailable",
    "assemble_source",
    "screen_soundness",
    "PLACEHOLDER_PATTERNS",
]


class VerifierError(RuntimeError):
    """Raised for harness-side failures that are not the model's fault."""


class BackendUnavailable(VerifierError):
    """The prover or service this backend needs is not usable here."""


#: Tokens that mean "I did not actually finish this proof", per language.
#: A prover will happily accept most of these with only a warning, which
#: is precisely why they need their own check.
PLACEHOLDER_PATTERNS: dict[str, tuple[str, ...]] = {
    "lean4": (r"\bsorry\b", r"\bsorryAx\b", r"\badmit\b", r"\bnative_decide\b"),
    "lean3": (r"\bsorry\b", r"\bsorryAx\b"),
    "coq": (r"\bAdmitted\b", r"\badmit\b", r"\bgive_up\b"),
    "isabelle": (r"\bsorry\b", r"\boops\b"),
    "axle": (r"\bsorry\b", r"\badmit\b", r"\bTODO\b"),
}

#: Declarations that let a proof assume its way out of work.
_AXIOM_PATTERNS: dict[str, tuple[str, ...]] = {
    "lean4": (r"^\s*(?:@\[[^\]]*\]\s*)?axiom\b", r"^\s*unsafe\s+axiom\b"),
    "lean3": (r"^\s*axiom\b", r"^\s*constant\b"),
    "coq": (r"^\s*(?:Axiom|Parameter|Hypothesis|Variable)\b",),
    "isabelle": (r"^\s*axiomatization\b", r"^\s*consts\b"),
    "axle": (r"^\s*axiom\b",),
}

#: Pragmas that turn the prover's own safety rails off.
_UNSAFE_PATTERNS: dict[str, tuple[str, ...]] = {
    "lean4": (
        r"set_option\s+maxHeartbeats\s+0\b",
        r"set_option\s+checkBinderAnnotations\s+false\b",
        r"set_option\s+debug\.skipKernelTC\s+true\b",
        r"^\s*partial\s+def\b",
    ),
    "coq": (r"Unset\s+Guard\s+Checking", r"Unset\s+Positivity\s+Checking"),
}

_COMMENT_STRIPPERS: dict[str, tuple[tuple[str, str], ...]] = {
    # (regex, replacement) applied in order; block comments first.
    "lean4": ((r"/-(?:.|\n)*?-/", " "), (r"--[^\n]*", " ")),
    "lean3": ((r"/-(?:.|\n)*?-/", " "), (r"--[^\n]*", " ")),
    "coq": ((r"\(\*(?:.|\n)*?\*\)", " "),),
    "isabelle": ((r"\(\*(?:.|\n)*?\*\)", " "),),
}


def strip_comments(text: str, language: str) -> str:
    """Remove comments so that soundness checks cannot be commented past.

    Without this, ``-- sorry`` reads as a violation and ``sorry`` hidden
    inside a ``/- -/`` block reads as clean. Both are wrong.
    """
    out = text
    for pattern, repl in _COMMENT_STRIPPERS.get(language, ()):
        out = re.sub(pattern, repl, out)
    return out


def _normalize_statement(text: str, language: str) -> str:
    """Collapse a statement to a form that ignores only harmless edits."""
    out = strip_comments(text, language)
    out = re.sub(r"\s+", " ", out)
    # A trailing `:= by`, `:=`, `:= by sorry` etc. is the seam where the
    # proof begins, not part of the claim, so it is cut before comparing.
    out = re.sub(r":=\s*(by\b.*)?$", "", out.strip()).strip()
    return out.rstrip(":= ").strip()


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
    """Look for ways the proof could be accepted without being a proof.

    This runs on the *assembled source*, before and independently of the
    prover, because several of these tricks produce a clean exit code.
    It is deliberately syntactic and therefore conservative: it can miss
    a novel trick, so it is a screen, not a guarantee. Backends that can
    ask the kernel directly (Lean's ``#print axioms``) should add that
    evidence on top via :meth:`Verifier.extra_soundness_checks`.
    """
    language = task.language
    report = SoundnessReport()
    body = strip_comments(source, language)

    for pattern in PLACEHOLDER_PATTERNS.get(language, ()):
        if re.search(pattern, body):
            token = pattern.strip("\\b")
            report = report.with_violation("proof contains placeholder %r" % token)

    for pattern in _AXIOM_PATTERNS.get(language, ()):
        if re.search(pattern, body, flags=re.MULTILINE):
            report = report.with_violation(
                "proof declares a new axiom/assumption, which can assume the goal"
            )
            break

    for pattern in _UNSAFE_PATTERNS.get(language, ()):
        if re.search(pattern, body, flags=re.MULTILINE):
            report = report.with_violation(
                "proof disables a prover safety check (%s)" % pattern
            )

    if check_statement and task.assembly is not Assembly.CONTINUE_STATEMENT:
        # Under CONTINUE_STATEMENT the statement is ours by construction;
        # in the other modes the model supplied it and may have changed it.
        wanted = _normalize_statement(task.formal_statement, language)
        if wanted and wanted not in _normalize_statement(source, language):
            report = report.with_violation(
                "submitted source does not contain the required statement verbatim"
            )

    return report


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

    def close(self) -> None:
        """Release any long-lived process or session. Idempotent."""

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
        ) -> VerificationResult:
            return VerificationResult(
                task_id=task.task_id,
                attempt_id=attempt.attempt_id or task.task_id,
                backend=self.name,
                status=status,
                error_kind=error_kind,
                soundness=soundness or SoundnessReport(),
                diagnostics=tuple(diagnostics),
                wall_time_s=time.monotonic() - started,
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

        return finish(status, error_kind, soundness, verdict.diagnostics, verdict.raw)

    # -- convenience --------------------------------------------------

    def __enter__(self) -> "Verifier":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return "<%s name=%r language=%r>" % (type(self).__name__, self.name, self.language)


class RawVerdict:
    """A backend's report before soundness and timing are folded in."""

    __slots__ = ("status", "error_kind", "diagnostics", "raw")

    def __init__(
        self,
        status: Status,
        error_kind: ErrorKind | None = None,
        diagnostics: Sequence[Diagnostic] = (),
        raw: Mapping[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.error_kind = error_kind
        self.diagnostics = tuple(diagnostics)
        self.raw: Mapping[str, Any] = dict(raw or {})

    @classmethod
    def verified(cls, raw: Mapping[str, Any] | None = None) -> "RawVerdict":
        return cls(Status.VERIFIED, raw=raw)

    @classmethod
    def failed(
        cls,
        error_kind: ErrorKind = ErrorKind.UNKNOWN,
        diagnostics: Sequence[Diagnostic] = (),
        raw: Mapping[str, Any] | None = None,
    ) -> "RawVerdict":
        return cls(Status.FAILED, error_kind, diagnostics, raw)

    @classmethod
    def timeout(cls, raw: Mapping[str, Any] | None = None) -> "RawVerdict":
        return cls(
            Status.TIMEOUT,
            ErrorKind.TIMEOUT,
            [Diagnostic(Severity.ERROR, "verification timed out", kind=ErrorKind.TIMEOUT)],
            raw,
        )

    def __repr__(self) -> str:
        return "RawVerdict(%s, %s)" % (self.status.value, self.error_kind)
