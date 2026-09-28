"""Core data types shared by every verifier backend.

The point of this package is that a *task*, an *attempt* and a *result*
mean the same thing no matter which prover ran underneath, so that
pass@k numbers from Lean 4 and from another system are at least
structurally comparable. Backend-specific detail is never lost: it is
pushed into the ``metadata`` / ``raw`` escape hatches instead of
distorting the shared fields.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Assembly",
    "Status",
    "ErrorKind",
    "Severity",
    "Diagnostic",
    "SoundnessReport",
    "ProofTask",
    "ProofAttempt",
    "VerificationResult",
    "BackendInfo",
    "index_attempts",
    "dedupe_by_id",
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
class SoundnessReport:
    """Why a syntactically accepted proof may still not count.

    Formal verification is only a trustworthy reward signal if the thing
    that was proved is the thing that was asked. Every check that can
    fire here corresponds to a known way of gaming a prover: leaving
    ``sorry`` in, asserting the goal as a fresh axiom, weakening the
    hypotheses, or proving a different theorem entirely.
    """

    ok: bool = True
    violations: tuple[str, ...] = ()

    def with_violation(self, violation: str) -> "SoundnessReport":
        return SoundnessReport(ok=False, violations=self.violations + (violation,))

    def merge(self, other: "SoundnessReport") -> "SoundnessReport":
        violations = self.violations + other.violations
        return SoundnessReport(ok=not violations, violations=violations)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "violations": list(self.violations)}


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
    wall_time_s: float = 0.0
    model: str | None = None
    sample_index: int = 0
    split: str | None = None
    cached: bool = False
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
            "model": self.model,
            "sample_index": self.sample_index,
            "split": self.split,
            "cached": self.cached,
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
            model=d.get("model"),
            sample_index=int(d.get("sample_index") or 0),
            split=d.get("split"),
            cached=bool(d.get("cached", False)),
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


def index_attempts(attempts: Iterable[ProofAttempt]) -> dict[str, list[ProofAttempt]]:
    """Group attempts by ``task_id``, preserving order within a task."""
    out: dict[str, list[ProofAttempt]] = {}
    for a in attempts:
        out.setdefault(a.task_id, []).append(a)
    return out


def dedupe_by_id(items: Sequence[ProofTask]) -> list[ProofTask]:
    """Drop duplicate task ids, keeping the first occurrence."""
    seen: set[str] = set()
    out: list[ProofTask] = []
    for t in items:
        if t.task_id in seen:
            continue
        seen.add(t.task_id)
        out.append(t)
    return out
