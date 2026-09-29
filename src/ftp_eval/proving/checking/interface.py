"""The frozen-target interface, screening report and authoritative check verdict."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from ...backends.soundness import SoundnessReport
from ...backends.types import ModuleBuild, Status


#: The sealed module's namespace. An answer may not declare into it.
PROBLEM_NAMESPACE = "Problem"


#: Where an answer puts everything, so two answers cannot collide.
SUBMISSION_NAMESPACE = "Submission"


#: The one declaration an answer must produce.
SOLUTION_NAME = "solution"


#: The frozen proposition it must inhabit.
TARGET_NAME = "Target"


#: The name Check.lean gives the ascription, and what the axiom audit reads.
CHECK_THEOREM = "ftp_eval_target"


class InterfaceFault(str, Enum):
    """Why an answer cannot be handed to the prover.

    Every one of these is decided from the text, before a compile. They are
    not judgements about the mathematics -- that is the kernel's job -- but
    about whether the answer even presents the interface.
    """

    #: No ``Submission.solution``. Nothing to check.
    SOLUTION_MISSING = "solution_missing"
    #: ``solution`` is there but left unproved. An honest miss, not a cheat.
    SOLUTION_UNPROVED = "solution_unproved"
    #: A declaration inside ``namespace Problem``: reopening a sealed module.
    PROBLEM_NAMESPACE_REUSED = "problem_namespace_reused"
    #: Declarations outside ``namespace Submission``, which can collide with
    #: another answer or with the problem.
    DECLARATION_OUTSIDE_NAMESPACE = "declaration_outside_namespace"
    #: A placeholder, a fresh axiom, or a kernel escape.
    REWARD_HACKING = "reward_hacking"
    #: An import the benchmark does not allow.
    IMPORT_NOT_ALLOWED = "import_not_allowed"
    #: The file could not be parsed as Lean at all.
    UNPARSED = "unparsed"
    INSTANCE_NOT_ALLOWED = "instance_not_allowed"
    SOLUTION_BINDERS = "solution_binders"
    SYNTAX_EXTENSION = "syntax_extension"
    IMPORTED_ATTRIBUTE = "imported_attribute"


@dataclass(frozen=True)
class InterfaceProblem:
    """The sealed side: a module name, and the target's arity.

    ``target_arguments`` is what an answer must supply, in order. Empty for
    a plain "prove this" problem; one entry for a problem that asks for a
    value, which is how ``answer(...)`` is expressed here -- the value lands
    in ``solution``'s *type*, so the kernel checks it and the report can read
    it off.
    """

    problem_id: str
    #: The module an answer imports, e.g. ``FtpEvalBench.P001``.
    module: str
    #: Names of Target's parameters, for the error messages only.
    target_parameters: tuple[str, ...] = ()
    #: The expected values, when the benchmark records a gold answer.
    gold_arguments: tuple[str, ...] = ()

    @property
    def target(self) -> str:
        return "%s.%s" % (PROBLEM_NAMESPACE, TARGET_NAME)

    @property
    def wants_value(self) -> bool:
        return bool(self.target_parameters)


@dataclass
class InterfaceReport:
    """What the text can say about an answer, before any compile."""

    problem_id: str
    faults: tuple[tuple[InterfaceFault, str], ...] = ()
    #: The arguments the answer applied Target to, read off its own type.
    #: Reported, never compared as text -- see `check_arguments`.
    submitted_arguments: tuple[str, ...] = ()
    #: Helper declarations the answer brought. Counted, never penalised.
    helper_count: int = 0
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether the answer is worth compiling."""
        return not self.faults

    @property
    def honest_miss(self) -> bool:
        """Right shape, no proof. Not a failure of integrity."""
        return [f for f, _ in self.faults] == [InterfaceFault.SOLUTION_UNPROVED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "ok": self.ok,
            "honest_miss": self.honest_miss,
            "faults": [{"fault": f.value, "detail": d} for f, d in self.faults],
            "submitted_arguments": list(self.submitted_arguments),
            "helper_count": self.helper_count,
            "raw": dict(self.raw),
        }

    def format_text(self) -> str:
        if self.ok:
            return "interface ok: Submission.%s declared, %d helper(s)" % (
                SOLUTION_NAME,
                self.helper_count,
            )
        return "\n".join(
            "  %-30s %s" % (fault.value, detail[:88]) for fault, detail in self.faults
        )


@dataclass
class InterfaceVerdict:
    """One answer, through all three checks.

    ``solved`` is true only when every one of them said so, and each is
    reported separately because they fail for different reasons and a setter
    needs to tell them apart.
    """

    problem_id: str
    participant: str = ""
    #: Stage 1: does the answer present the interface? Text only.
    report: InterfaceReport | None = None
    #: Stage 2: does the kernel accept the ascription? None = did not run.
    build: "ModuleBuild | None" = None
    #: Stage 3: what does the proof actually depend on?
    axiom_audit: "SoundnessReport | None" = None
    #: Whether the target was pinned to the benchmark's gold value. When
    #: false the verdict is the weaker "proved the proposition it claimed".
    against_gold: bool = False
    stages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def solved(self) -> bool:
        return bool(
            self.report is not None
            and self.report.ok
            and self.build is not None
            and self.build.verified
            and self.axiom_audit is not None
            and self.axiom_audit.ok
        )

    @property
    def refused_at(self) -> str:
        """Which check refused it, or "" when none did."""
        if self.report is not None and not self.report.ok:
            return "interface"
        for event in self.stages:
            if event["stage"] in ("kernel", "replay", "dependencies") and event["status"] == "failed":
                return str(event["stage"])
        if self.build is None:
            return ""  # not run, which is not a refusal
        if not self.build.verified:
            return str(self.build.raw.get("failed_stage") or "kernel")
        if self.axiom_audit is not None and not self.axiom_audit.ok:
            return "axioms"
        return ""

    @property
    def kernel_checked(self) -> bool:
        kernel_events = [e for e in self.stages if e["stage"] == "kernel" and e["status"] != "running"]
        if kernel_events:
            return kernel_events[-1]["status"] in ("passed", "failed")
        return self.build is not None and self.build.status not in (Status.ERROR, Status.SKIPPED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "participant": self.participant,
            "solved": self.solved,
            "refused_at": self.refused_at,
            "kernel_checked": self.kernel_checked,
            "against_gold": self.against_gold,
            "stages": list(self.stages),
            "interface": self.report.to_dict() if self.report else None,
            "build": self.build.to_dict() if self.build else None,
            "axioms": self.axiom_audit.to_dict() if self.axiom_audit else None,
        }

    def format_text(self) -> str:
        if self.solved:
            note = "" if self.against_gold else "  (value self-declared, not gold)"
            return "SOLVED   %-14s%s" % (self.problem_id[:14], note)
        if not self.kernel_checked and self.report is not None and self.report.ok:
            return "NOT RUN  %-14s  the prover was not asked, so nothing was proved" % (
                self.problem_id[:14]
            )
        where = self.refused_at or "?"
        detail = ""
        if where == "interface" and self.report is not None and self.report.faults:
            detail = self.report.faults[0][1]
        elif where == "kernel" and self.build is not None:
            first = [d.message for d in self.build.diagnostics if d.message]
            detail = first[0] if first else self.build.status.value
        elif where == "axioms" and self.axiom_audit is not None:
            detail = "; ".join(self.axiom_audit.violations)
        return "REFUSED  %-14s  at %-9s %s" % (self.problem_id[:14], where, detail[:66])
