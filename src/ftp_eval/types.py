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
    # statement-level vocabulary
    "StatementTask",
    "ProbeKind",
    "CheckKind",
    "Check",
    "StatementStatus",
    "StatementVerdict",
    "JudgeLabel",
    "JudgeUsage",
    "JudgeVerdict",
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


class CheckKind(str, Enum):
    """One piece of evidence about whether a formalization is faithful."""

    ELABORATES = "elaborates"
    NO_PLACEHOLDER = "no_placeholder"
    NON_TRIVIAL = "non_trivial"
    NON_VACUOUS = "non_vacuous"
    GOLD_EQUIVALENT = "gold_equivalent"
    JUDGE_FAITHFUL = "judge_faithful"


class StatementStatus(str, Enum):
    """Verdict on a formalization."""

    #: Every check that ran passed.
    OK = "ok"
    #: Definitely broken: it does not typecheck, or contains a placeholder.
    MALFORMED = "malformed"
    #: Typechecks, but something is off -- trivially true, vacuous, or a
    #: judge read it as not matching the problem.
    SUSPICIOUS = "suspicious"
    #: Nothing conclusive could be established (no judge configured, no
    #: prover available, backend cannot build probes for this language).
    INCONCLUSIVE = "inconclusive"
    #: The harness broke.
    ERROR = "error"

    @property
    def usable_as_ground_truth(self) -> bool:
        """Whether a proof against this statement means anything.

        ``INCONCLUSIVE`` is deliberately included: not having checked is
        not the same as having found a problem, and refusing to score
        every unjudged task would make the tool unusable without an API
        key. What it is not is evidence of faithfulness, which is why
        :class:`StatementVerdict` keeps ``checked_faithfulness`` separate.
        """
        return self in (StatementStatus.OK, StatementStatus.INCONCLUSIVE)


class JudgeLabel(str, Enum):
    """What an LLM judge concluded about a formalization.

    ``UNSURE`` is a first-class answer, not a failure. A judge forced to
    choose between faithful and unfaithful on a statement it cannot read
    produces confident noise, and confident noise is worse than an
    abstention you can route to a human.
    """

    FAITHFUL = "faithful"
    UNFAITHFUL = "unfaithful"
    UNSURE = "unsure"


@dataclass(frozen=True)
class JudgeUsage:
    """What a judge call consumed.

    Token counts are **measured** -- they come from the provider's own
    usage report. The dollar figure is an **estimate** computed from a
    local price table, and is labelled that way everywhere it surfaces,
    because a hardcoded price table drifts from real billing and a
    self-computed cost has no authority next to the provider's invoice.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0
    estimated_cost_usd: float | None = None
    model: str | None = None

    def merge(self, other: "JudgeUsage") -> "JudgeUsage":
        costs = [c for c in (self.estimated_cost_usd, other.estimated_cost_usd) if c is not None]
        return JudgeUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            calls=self.calls + other.calls,
            estimated_cost_usd=sum(costs) if costs else None,
            model=self.model or other.model,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "calls": self.calls,
            "estimated_cost_usd": self.estimated_cost_usd,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "JudgeUsage":
        return cls(
            input_tokens=int(d.get("input_tokens") or 0),
            output_tokens=int(d.get("output_tokens") or 0),
            cache_read_input_tokens=int(d.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(d.get("cache_creation_input_tokens") or 0),
            calls=int(d.get("calls") or 0),
            estimated_cost_usd=d.get("estimated_cost_usd"),
            model=d.get("model"),
        )


@dataclass(frozen=True)
class JudgeVerdict:
    """An LLM judge's answer, with everything needed to audit it."""

    label: JudgeLabel
    reasoning: str = ""
    #: The judge's own stated confidence in [0, 1], when it reports one.
    confidence: float | None = None
    #: Individual labels when several samples were drawn, in order.
    samples: tuple[JudgeLabel, ...] = ()
    #: How the final label was reached ("single", "majority", "tie -> unsure").
    aggregation: str = "single"
    usage: JudgeUsage = field(default_factory=JudgeUsage)
    #: Raw provider payloads, kept so a surprising verdict can be read back.
    raw: tuple[Mapping[str, Any], ...] = ()

    @property
    def is_faithful(self) -> bool:
        return self.label is JudgeLabel.FAITHFUL

    @property
    def abstained(self) -> bool:
        return self.label is JudgeLabel.UNSURE

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label.value,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "samples": [s.value for s in self.samples],
            "aggregation": self.aggregation,
            "usage": self.usage.to_dict(),
            "raw": [dict(r) for r in self.raw],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "JudgeVerdict":
        return cls(
            label=JudgeLabel(d["label"]),
            reasoning=d.get("reasoning") or "",
            confidence=d.get("confidence"),
            samples=tuple(JudgeLabel(s) for s in d.get("samples") or ()),
            aggregation=d.get("aggregation") or "single",
            usage=JudgeUsage.from_dict(d.get("usage") or {}),
            raw=tuple(dict(r) for r in d.get("raw") or ()),
        )


@dataclass(frozen=True)
class Check:
    """The outcome of one statement check.

    ``passed is None`` means the check did not run or could not decide.
    That is distinct from ``False`` and is never silently read as a pass.
    """

    kind: CheckKind
    passed: bool | None
    detail: str = ""
    #: True when failing this check proves the formalization is broken,
    #: rather than merely suspicious.
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "passed": self.passed,
            "detail": self.detail,
            "fatal": self.fatal,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Check":
        return cls(
            kind=CheckKind(d["kind"]),
            passed=d.get("passed"),
            detail=d.get("detail") or "",
            fatal=bool(d.get("fatal", False)),
        )


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


@dataclass(frozen=True)
class StatementVerdict:
    """Everything concluded about one formalization."""

    task_id: str
    status: StatementStatus
    checks: tuple[Check, ...] = ()
    judge: JudgeVerdict | None = None
    backend: str | None = None
    wall_time_s: float = 0.0
    split: str | None = None
    #: Untouched probe payloads, for auditing a surprising verdict.
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.passed is False)

    @property
    def checked_faithfulness(self) -> bool:
        """Whether semantic faithfulness was actually assessed.

        The prover-decidable checks catch malformed and degenerate
        statements; none of them can tell you the formalization means the
        same thing as the prose. Only a judge (or a gold comparison) can,
        so a report distinguishes "passed the checks" from "checked".
        """
        return any(
            c.kind in (CheckKind.JUDGE_FAITHFUL, CheckKind.GOLD_EQUIVALENT)
            and c.passed is not None
            for c in self.checks
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "checks": [c.to_dict() for c in self.checks],
            "judge": self.judge.to_dict() if self.judge else None,
            "backend": self.backend,
            "wall_time_s": round(self.wall_time_s, 4),
            "split": self.split,
            "checked_faithfulness": self.checked_faithfulness,
            "raw": dict(self.raw),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "StatementVerdict":
        return cls(
            task_id=d["task_id"],
            status=StatementStatus(d["status"]),
            checks=tuple(Check.from_dict(c) for c in d.get("checks") or ()),
            judge=JudgeVerdict.from_dict(d["judge"]) if d.get("judge") else None,
            backend=d.get("backend"),
            wall_time_s=float(d.get("wall_time_s") or 0.0),
            split=d.get("split"),
            raw=dict(d.get("raw") or {}),
        )


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
