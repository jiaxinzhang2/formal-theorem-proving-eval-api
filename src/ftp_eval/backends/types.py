"""The backend contract: what a prover is asked, and what it answers.

This is the one thing the two APIs genuinely share, and the only reason
this folder exists. ``proving/`` hands a backend a statement and a proof;
``autoformalization/`` hands the same backend a statement and a probe.
Same prover, same vocabulary, so both live here.

Everything belonging to only one API lives with that API instead --
statement-side verdicts and judge records in
``autoformalization/types.py``, the screening report in ``soundness.py``.

Backend-specific detail is never lost: it is pushed into the ``metadata``
/ ``raw`` escape hatches rather than distorting these fields.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

# The only thing prover/ takes from source/: a verdict carries the
# screening report for the source it was given. The arrow points this
# way only -- source/ imports nothing from here.
from .soundness import SoundnessReport

__all__ = [
    # what a backend is asked
    "ProofTask",
    "ProofAttempt",
    "StatementTask",
    "ProbeKind",
    "Assembly",
    # what a backend answers
    "VerificationResult",
    "Status",
    "Diagnostic",
    "ErrorKind",
    "Severity",
    "BackendInfo",
    "index_attempts",
]


class Assembly(str, Enum):
    """How an attempt's text combines with its task to form a source file.

    Benchmarks disagree about what a model is asked to emit, and getting
    this wrong silently turns every result into a syntax error, so it is
    recorded per task rather than assumed globally.
    """

    #: The attempt is a complete, standalone source file.
    FULL_FILE = "full_file"
    #: header + formal_statement + attempt text. The miniF2F convention:
    #: the model continues the statement with ``:= by ...``.
    CONTINUE_STATEMENT = "continue_statement"
    #: header + attempt text; the attempt restates the theorem itself.
    #: Statement tampering is then checked, not assumed away.
    HEADER_PLUS_PROOF = "header_plus_proof"


class Status(str, Enum):
    """Outcome of a single verification, in decreasing order of goodness."""

    #: The prover accepted the proof and no soundness violation was found.
    VERIFIED = "verified"
    #: The prover accepted the text, but it does not count: ``sorry``, a
    #: new axiom, a mutated statement. See ``SoundnessReport``.
    REJECTED = "rejected"
    #: The prover ran to completion and did not accept the proof.
    FAILED = "failed"
    #: The prover was still working when the budget ran out.
    TIMEOUT = "timeout"
    #: The prover or the harness broke. Our fault, not the model's.
    ERROR = "error"
    #: Never attempted (no matching attempt, backend down, filtered out).
    SKIPPED = "skipped"

    @property
    def is_success(self) -> bool:
        return self is Status.VERIFIED

    @property
    def counts_toward_pass_rate(self) -> bool:
        """Whether this attempt is a fair denominator entry for pass@k.

        ``ERROR`` and ``SKIPPED`` mean we never got a verdict, so folding
        them in as failures would understate a model. They are reported
        separately instead -- see :func:`ftp_eval.metrics.summarize`.
        """
        return self in (Status.VERIFIED, Status.REJECTED, Status.FAILED, Status.TIMEOUT)


class ErrorKind(str, Enum):
    """Coarse, cross-prover classification of why a verification failed."""

    SYNTAX = "syntax"                      # the file did not parse
    TYPE = "type"                          # type / elaboration error
    UNKNOWN_IDENTIFIER = "unknown_identifier"
    UNSOLVED_GOALS = "unsolved_goals"      # proof ended with goals remaining
    TACTIC_FAILED = "tactic_failed"
    INCOMPLETE = "incomplete"              # placeholder left in the proof
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"      # OOM, deterministic timeout, depth
    SOUNDNESS = "soundness"                # cheated; see SoundnessReport
    TOOLCHAIN = "toolchain"                # prover missing / misconfigured
    HARNESS = "harness"                    # bug or I/O failure on our side
    UNKNOWN = "unknown"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Diagnostic:
    """One message from the prover, normalized to a common shape."""

    severity: Severity
    message: str
    line: int | None = None
    column: int | None = None
    kind: ErrorKind | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "kind": self.kind.value if self.kind else None,
        }


@dataclass(frozen=True)
class ProofTask:
    """A single theorem to be proved."""

    task_id: str
    formal_statement: str
    header: str = ""
    language: str = "lean4"
    assembly: Assembly = Assembly.CONTINUE_STATEMENT
    informal_statement: str | None = None
    split: str | None = None
    #: Optional per-task wall-clock budget, overriding the runner default.
    timeout_s: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must be non-empty")
        if not self.formal_statement.strip() and self.assembly is Assembly.CONTINUE_STATEMENT:
            raise ValueError(
                "task %r: assembly=continue_statement needs a formal_statement" % self.task_id
            )

    @property
    def fingerprint(self) -> str:
        """Stable hash of everything that defines the problem itself."""
        payload = json.dumps(
            [self.language, self.assembly.value, self.header, self.formal_statement],
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["assembly"] = self.assembly.value
        d["metadata"] = dict(self.metadata)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ProofTask":
        known = set(cls.__dataclass_fields__)
        extra = {k: v for k, v in d.items() if k not in known}
        kwargs: dict[str, Any] = {k: v for k, v in d.items() if k in known}
        if kwargs.get("assembly") is not None:
            kwargs["assembly"] = Assembly(kwargs["assembly"])
        metadata = dict(kwargs.get("metadata") or {})
        # Unrecognized columns are kept rather than dropped: benchmark
        # files carry provenance we have no business deleting.
        metadata.update(extra)
        kwargs["metadata"] = metadata
        return cls(**kwargs)


@dataclass(frozen=True)
class ProofAttempt:
    """One candidate proof produced for one task."""

    task_id: str
    proof: str
    attempt_id: str | None = None
    model: str | None = None
    #: Index of this sample among the k drawn for the task; pass@k needs it.
    sample_index: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("attempt is missing task_id")
        if self.sample_index < 0:
            raise ValueError("sample_index must be >= 0")
        if self.attempt_id is None:
            object.__setattr__(self, "attempt_id", "%s#%d" % (self.task_id, self.sample_index))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.proof.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["metadata"] = dict(self.metadata)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ProofAttempt":
        known = set(cls.__dataclass_fields__)
        extra = {k: v for k, v in d.items() if k not in known}
        kwargs: dict[str, Any] = {k: v for k, v in d.items() if k in known}
        metadata = dict(kwargs.get("metadata") or {})
        metadata.update(extra)
        kwargs["metadata"] = metadata
        return cls(**kwargs)


@dataclass(frozen=True)
class VerificationResult:
    """What a backend reports back for one (task, attempt) pair."""

    task_id: str
    attempt_id: str
    backend: str
    status: Status
    error_kind: ErrorKind | None = None
    soundness: SoundnessReport = field(default_factory=SoundnessReport)
    diagnostics: tuple[Diagnostic, ...] = ()
    #: Total time inside ``verify``: screening, the prover, and any extra
    #: soundness passes (for Lean, the axiom audit is a second compile).
    wall_time_s: float = 0.0
    #: The prover's own time, when the backend reports it. Subtracting it
    #: from ``wall_time_s`` gives the harness overhead, and it is the
    #: figure to use when comparing provers or tuning a Lean setup.
    #: ``None`` means the backend did not measure it.
    compile_time_s: float | None = None
    model: str | None = None
    sample_index: int = 0
    split: str | None = None
    cached: bool = False
    #: Tactic names found in the proof, in order, repeats kept. Empty
    #: means none were recognized (a term-mode proof, or an unsupported
    #: language) -- it does not mean the proof was empty.
    tactics: tuple[str, ...] = ()
    #: Structural metrics for the proof text (see
    #: :func:`ftp_eval.proof_metrics.analyze_proof`). Recorded for *every*
    #: attempt, including failures and timeouts: how a model fails is as
    #: informative as how it succeeds, and comparing the two is the point.
    structure: Mapping[str, Any] = field(default_factory=dict)
    #: Fine-grained failure classification (see :mod:`ftp_eval.modes`).
    #: Set for every non-verified attempt, ``None`` for a pass. Finer than
    #: ``error_kind``: it separates a hallucinated lemma name from a real
    #: type error, and a truncated completion from bad Lean.
    failure_mode: str | None = None
    #: The *shape* of a verified proof (one-liner automation, induction,
    #: structured steps, ...). ``None`` for a failure. Exists because
    #: "solved 40%" with one-line automation is a different result from
    #: "solved 40%" with multi-step arguments.
    success_mode: str | None = None
    #: Untouched backend payload (stdout, exit code, HTTP body, ...).
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.status.is_success

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "backend": self.backend,
            "status": self.status.value,
            "error_kind": self.error_kind.value if self.error_kind else None,
            "soundness": self.soundness.to_dict(),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "wall_time_s": round(self.wall_time_s, 4),
            "compile_time_s": (
                None if self.compile_time_s is None else round(self.compile_time_s, 4)
            ),
            "model": self.model,
            "sample_index": self.sample_index,
            "split": self.split,
            "cached": self.cached,
            "tactics": list(self.tactics),
            "structure": dict(self.structure),
            "failure_mode": self.failure_mode,
            "success_mode": self.success_mode,
            "raw": dict(self.raw),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "VerificationResult":
        sound = d.get("soundness") or {}
        diags = tuple(
            Diagnostic(
                severity=Severity(x.get("severity", "error")),
                message=x.get("message", ""),
                line=x.get("line"),
                column=x.get("column"),
                kind=ErrorKind(x["kind"]) if x.get("kind") else None,
            )
            for x in d.get("diagnostics") or ()
        )
        return cls(
            task_id=d["task_id"],
            attempt_id=d["attempt_id"],
            backend=d["backend"],
            status=Status(d["status"]),
            error_kind=ErrorKind(d["error_kind"]) if d.get("error_kind") else None,
            soundness=SoundnessReport(
                ok=bool(sound.get("ok", True)),
                violations=tuple(sound.get("violations") or ()),
            ),
            diagnostics=diags,
            wall_time_s=float(d.get("wall_time_s") or 0.0),
            compile_time_s=(
                float(d["compile_time_s"]) if d.get("compile_time_s") is not None else None
            ),
            model=d.get("model"),
            sample_index=int(d.get("sample_index") or 0),
            split=d.get("split"),
            cached=bool(d.get("cached", False)),
            tactics=tuple(d.get("tactics") or ()),
            structure=dict(d.get("structure") or {}),
            failure_mode=d.get("failure_mode"),
            success_mode=d.get("success_mode"),
            raw=dict(d.get("raw") or {}),
        )


@dataclass(frozen=True)
class BackendInfo:
    """Self-description of a backend, for ``ftp-eval backends`` / ``doctor``."""

    name: str
    language: str
    available: bool
    version: str | None = None
    #: Human-readable reason when ``available`` is False.
    detail: str | None = None
    supports: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "language": self.language,
            "available": self.available,
            "version": self.version,
            "detail": self.detail,
            "supports": list(self.supports),
        }


# ---------------------------------------------------------------------
# Statement-level vocabulary
#
# A dataset of (natural language, formal statement, formal proof) has two
# links in it, not one. The proof-level types above answer "does this
# proof close this goal?"; the types below answer "does this goal say
# what the problem said?". Keeping them separate matters because the two
# failures are opposite: a wrong formalization with a valid proof is a
# false positive, while a right formalization with no proof is an honest
# miss, and a single pass/fail number hides the difference.
# ---------------------------------------------------------------------


class ProbeKind(str, Enum):
    """A question a prover can be asked *about a statement*.

    Each probe is a small synthetic source file the backend builds from
    the statement, compiled the same way a proof would be. They are not
    proof attempts, so soundness screening does not apply to them -- the
    elaboration probe deliberately submits a ``sorry`` body.
    """

    #: statement + a placeholder body. Does the statement itself typecheck?
    ELABORATES = "elaborates"
    #: statement + a cheap tactic. Is the goal trivially closable?
    TRIVIAL = "trivial"
    #: hypotheses + goal ``False``. Are the hypotheses contradictory,
    #: making the theorem vacuously true whatever it claims?
    VACUOUS = "vacuous"
    #: ``candidate ↔ gold``. Does it agree with a reference formalization?
    GOLD_EQUIVALENT = "gold_equivalent"


@dataclass(frozen=True)
class StatementTask:
    """A natural-language problem paired with a candidate formalization."""

    task_id: str
    informal_statement: str
    formal_statement: str
    header: str = ""
    language: str = "lean4"
    #: A reference formalization, when the dataset has one.
    gold_formal_statement: str | None = None
    split: str | None = None
    timeout_s: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must be non-empty")
        if not self.formal_statement.strip():
            raise ValueError("task %r: formal_statement is empty" % self.task_id)

    def to_proof_task(self, *, assembly: Assembly = Assembly.CONTINUE_STATEMENT) -> ProofTask:
        """The proof-level task this statement defines."""
        return ProofTask(
            task_id=self.task_id,
            formal_statement=self.formal_statement,
            header=self.header,
            language=self.language,
            assembly=assembly,
            informal_statement=self.informal_statement,
            split=self.split,
            timeout_s=self.timeout_s,
            metadata=self.metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["metadata"] = dict(self.metadata)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "StatementTask":
        known = set(cls.__dataclass_fields__)
        extra = {k: v for k, v in d.items() if k not in known}
        kwargs: dict[str, Any] = {k: v for k, v in d.items() if k in known}
        metadata = dict(kwargs.get("metadata") or {})
        metadata.update(extra)
        kwargs["metadata"] = metadata
        return cls(**kwargs)


def index_attempts(attempts: Iterable[ProofAttempt]) -> dict[str, list[ProofAttempt]]:
    """Group attempts by ``task_id``, preserving order within a task."""
    out: dict[str, list[ProofAttempt]] = {}
    for a in attempts:
        out.setdefault(a.task_id, []).append(a)
    return out
