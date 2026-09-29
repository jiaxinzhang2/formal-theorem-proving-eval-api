"""Grading against a frozen proposition, not against a text comparison.

The problem is a module that is compiled **before any answer exists**::

    -- Problem.lean
    import Mathlib

    namespace Problem

    abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop := ...

    lemma givenLemma : ... := ...

    def Target (a₀ : ℕ) : Prop :=
      IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } a₀

    end Problem

An answer imports it and must produce one declaration with one type::

    -- Answer.lean
    import Problem

    namespace Submission

    def myDef := ...
    lemma myLemma : ... := by ...
    instance : SomeClass X := ...

    theorem solution : Problem.Target 4 := by
      ...

    end Submission

And the verdict is a third module the kernel checks::

    -- Check.lean
    import Problem
    import Answer

    theorem ftp_eval_target : Problem.Target 4 := Submission.solution

    #print axioms ftp_eval_target

Why this and not text comparison
--------------------------------------------------------------------------
``Problem.Target`` is elaborated inside a module that is sealed before the
answer is written. The answer can add a thousand declarations, an
``instance``, a ``notation``, a ``set_option`` -- and none of it can reach
back and change what ``Problem.Target`` means. The boundary makes an entire
class of attack impossible rather than detected:

* a definition the statement rests on cannot be redefined -- ``Problem.Foo``
  is already compiled, and a ``Submission.Foo`` is a different constant;
* a ``variable`` binding cannot be injected into the target, because the
  target has no binders left to inject into;
* an ``instance`` cannot change how the target elaborates, because the
  target is past elaboration. It can only help *prove* it, which is
  legitimate;
* the statement cannot be restated, weakened or reworded, because the answer
  never states it. It cites it.

So there is nothing to compare, and **helpers need no correspondence at
all**. Lean walks the dependency closure itself: if
``ftp_eval_target : Problem.Target 4`` typechecks, every helper the proof
used really does combine into a proof of the asked proposition.

What is left to check
--------------------------------------------------------------------------
Two things the type ascription does not cover, because a term can have the
right type and still not be a proof:

1. **The axiom listing.** ``sorry`` typechecks -- it elaborates to
   ``sorryAx`` and Lean reports a warning, not an error. A declared ``axiom``
   typechecks with no complaint at all. ``#print axioms`` is the only check
   that sees through both, and through any amount of indirection.
2. **A static screen**, before the prover is asked: an answer with an
   obvious ``sorry`` or ``axiom`` need not cost a compile, and the
   compiler-trusting escapes (``native_decide``, ``@[implemented_by]``,
   ``unsafe``, ``partial def``) must be refused even when the kernel is
   satisfied, since they are what the kernel was told to skip.

Plus the shape of the interface itself: the answer has to declare
``Submission.solution``, in that namespace, and must not declare anything
inside ``namespace Problem`` -- which would be reopening a sealed module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from ..backends.soundness import (
    DEFAULT_ALLOWED_IMPORTS,
    LEGITIMATE_AXIOMS,
    HackClass,
    SoundnessReport,
    audit_axioms,
    label,
    parse_label,
    screen_source,
)
from ..backends.types import ModuleBuild, Status, Diagnostic, Severity
from .lean_file import LeanFile, parse_lean_file

__all__ = [
    "PROBLEM_NAMESPACE",
    "SUBMISSION_NAMESPACE",
    "SOLUTION_NAME",
    "TARGET_NAME",
    "CHECK_THEOREM",
    "InterfaceProblem",
    "InterfaceFault",
    "InterfaceReport",
    "read_interface_problem",
    "check_interface",
    "check_arguments",
    "build_check_source",
    "ContestPolicy",
    "InterfaceVerdict",
    "grade_interface",
]

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
    #: The module an answer imports, e.g. ``Bench.P001``.
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


def read_interface_problem(
    problem_source: str, *, problem_id: str = "", module: str = ""
) -> InterfaceProblem:
    """Read the sealed module's interface out of its source.

    Only the shape is read: the module name and ``Target``'s parameters.
    Nothing here interprets the mathematics -- the problem file is trusted,
    and the setter wrote ``Target`` by hand precisely so that no tool has to
    transform it.
    """
    parsed = parse_lean_file(problem_source)
    target = next(
        (d for d in parsed.declarations if d.qualified_name == "Problem.Target"), None
    )
    if target is None:
        raise ValueError("%s: expected a frozen Problem.Target definition" % problem_id)
    parameters: tuple[str, ...] = ()
    if target is not None:
        parameters = _binder_names(target.signature)
    return InterfaceProblem(
        problem_id=problem_id, module=module, target_parameters=parameters
    )


def _binder_names(signature: str) -> tuple[str, ...]:
    """Parameter names from ``(a₀ : ℕ) (a₁ : ℕ) : Prop``.

    Stops at the top-level ``:`` that introduces the result type, so the
    ``ℕ`` in a binder is never mistaken for a parameter.
    """
    names: list[str] = []
    depth = 0
    current = ""
    for char in signature:
        if char in "([{⟨⦃":
            depth += 1
            if depth == 1:
                current = ""
                continue
        elif char in ")]}⟩⦄":
            depth -= 1
            if depth == 0:
                head = current.split(":")[0].strip()
                names.extend(part for part in head.split() if part)
                current = ""
                continue
        elif depth == 0 and char == ":":
            break
        if depth >= 1:
            current += char
    return tuple(names)


def check_interface(
    problem: InterfaceProblem,
    answer_source: str,
    *,
    allowed_imports: Sequence[str] | None = None,
) -> InterfaceReport:
    """Everything the text can settle about an answer. Never raises.

    Deliberately short. With the target frozen there is no statement to
    compare, no vocabulary to diff and no restatement to catch -- the type
    ascription in Check.lean settles all of that. What is left is whether
    the answer presents the interface, and whether it is trying to skip the
    kernel.
    """
    parsed = parse_lean_file(answer_source)
    faults: list[tuple[InterfaceFault, str]] = []

    if parsed.unparsed:
        faults.append(
            (
                InterfaceFault.UNPARSED,
                "%d declaration(s) could not be parsed: %s"
                % (len(parsed.unparsed), parsed.unparsed[0][:80]),
            )
        )

    solution = _find_solution(parsed)
    if solution is None:
        faults.append(
            (
                InterfaceFault.SOLUTION_MISSING,
                "no `%s.%s` in the answer. That one declaration is the whole "
                "interface: name it exactly, inside `namespace %s`, and give it "
                "type `%s%s`"
                % (
                    SUBMISSION_NAMESPACE,
                    SOLUTION_NAME,
                    SUBMISSION_NAMESPACE,
                    problem.target,
                    " <value>" if problem.wants_value else "",
                ),
            )
        )
    elif solution.is_unproved:
        faults.append(
            (
                InterfaceFault.SOLUTION_UNPROVED,
                "`%s.%s` is declared but left unproved. An honest non-answer, "
                "not a violation" % (SUBMISSION_NAMESPACE, SOLUTION_NAME),
            )
        )

    faults.extend(_namespace_faults(parsed))
    faults.extend(_screen(answer_source, solution))
    if allowed_imports is not None:
        faults.extend(_import_faults(parsed, allowed_imports, problem.module))

    arguments = _target_arguments(solution) if solution is not None else ()
    helpers = sum(
        1 for d in parsed.declarations if solution is None or d is not solution
    )
    return InterfaceReport(
        problem_id=problem.problem_id,
        faults=tuple(faults),
        submitted_arguments=arguments,
        helper_count=helpers,
        raw={"imports": list(parsed.imports())},
    )


def _find_solution(parsed: LeanFile) -> Any:
    wanted = "%s.%s" % (SUBMISSION_NAMESPACE, SOLUTION_NAME)
    for declaration in parsed.declarations:
        if declaration.qualified_name == wanted:
            return declaration
    return None


def _namespace_faults(parsed: LeanFile) -> list[tuple[InterfaceFault, str]]:
    """Declarations that escape ``namespace Submission``.

    Two different problems. Declaring into ``namespace Problem`` is
    reopening a sealed module -- Lean would let the answer add constants
    that look like the benchmark's. Declaring at top level risks colliding
    with the problem's own names and, across a contest, with another
    answer's.
    """
    out: list[tuple[InterfaceFault, str]] = []
    into_problem = [
        d.qualified_name
        for d in parsed.declarations
        if d.namespace == PROBLEM_NAMESPACE
        or d.namespace.startswith(PROBLEM_NAMESPACE + ".")
    ]
    if into_problem:
        out.append(
            (
                InterfaceFault.PROBLEM_NAMESPACE_REUSED,
                "the answer declares %d name(s) inside `namespace %s` (%s). That "
                "namespace belongs to the sealed problem module; put everything in "
                "`namespace %s`"
                % (
                    len(into_problem),
                    PROBLEM_NAMESPACE,
                    ", ".join(into_problem[:3]),
                    SUBMISSION_NAMESPACE,
                ),
            )
        )
    outside = [
        d.qualified_name
        for d in parsed.declarations
        if d.name
        and d.namespace != SUBMISSION_NAMESPACE
        and not d.namespace.startswith(SUBMISSION_NAMESPACE + ".")
        and d.namespace != PROBLEM_NAMESPACE
        and not d.namespace.startswith(PROBLEM_NAMESPACE + ".")
    ]
    if outside:
        out.append(
            (
                InterfaceFault.DECLARATION_OUTSIDE_NAMESPACE,
                "%d declaration(s) are outside `namespace %s` (%s). Everything an "
                "answer adds goes inside it, so two answers can never collide"
                % (len(outside), SUBMISSION_NAMESPACE, ", ".join(outside[:3])),
            )
        )
    return out


def _screen(answer_source: str, solution: Any) -> list[tuple[InterfaceFault, str]]:
    """The static screen, at file scope.

    File scope because a proof can cite anything in the file: an ``axiom``
    declared beside the solution is as good as one inside it. The one
    exception is the solution itself being unproved, already reported as an
    honest miss -- calling that reward hacking would turn "did not solve it"
    into "cheated".
    """
    report = screen_source(
        answer_source, "lean4", required_statement=None, allowed_imports=None
    )
    violations = list(report.violations)
    if solution is not None and solution.is_unproved:
        violations = [
            v
            for v in violations
            if parse_label(v)[0] != HackClass.PLACEHOLDER.value
        ]
    if not violations:
        return []
    return [(InterfaceFault.REWARD_HACKING, "; ".join(violations))]


def _import_faults(
    parsed: LeanFile, allowed: Sequence[str], problem_module: str
) -> list[tuple[InterfaceFault, str]]:
    """Imports outside the allowlist.

    The problem's own module is always allowed -- importing it is how an
    answer reaches the target at all.
    """
    bad = [
        module
        for module in parsed.imports()
        if module != problem_module and not any(
            module == ok or module.startswith(ok + ".") for ok in allowed if ok
        )
    ]
    if not bad:
        return []
    return [
        (
            InterfaceFault.IMPORT_NOT_ALLOWED,
            "the answer imports %s, which the benchmark does not allow. A "
            "self-supplied module can carry its own axioms" % ", ".join(bad[:4]),
        )
    ]


def _target_arguments(solution: Any) -> tuple[str, ...]:
    """What the answer applied ``Problem.Target`` to, from its own signature.

    ``theorem solution : Problem.Target 4`` yields ``("4",)``. The value is
    read for reporting and for comparing against a gold answer; it is the
    kernel that decides whether the proof actually inhabits that type.
    """
    signature = solution.signature
    marker = "%s.%s" % (PROBLEM_NAMESPACE, TARGET_NAME)
    index = signature.find(marker)
    if index < 0:
        return ()
    tail = signature[index + len(marker) :].strip()
    return tuple(_split_arguments(tail))


def _split_arguments(tail: str) -> list[str]:
    """Top-level whitespace-separated arguments, brackets respected."""
    out: list[str] = []
    depth = 0
    current = ""
    for char in tail:
        if char in "([{⟨⦃":
            depth += 1
        elif char in ")]}⟩⦄":
            depth -= 1
            if depth < 0:
                break
        if depth == 0 and char.isspace():
            if current:
                out.append(current)
                current = ""
            continue
        current += char
    if current:
        out.append(current)
    return out


def check_arguments(
    problem: InterfaceProblem, report: InterfaceReport
) -> tuple[tuple[str, ...], bool]:
    """Which values Check.lean should demand, and whether they are the gold.

    Returns ``(arguments, against_gold)``.

    When the benchmark records a gold answer, Check.lean is built against
    **it**, so the ascription itself rejects a wrong value -- the kernel
    compares, on definitional equality, and nothing has to decide whether
    ``4`` and ``(2 + 2)`` are the same string.

    When it does not -- an open conjecture, where the setter does not know
    the answer either -- the answer's own value is used, and the caller must
    record that the verdict is "proved the proposition it claimed", which is
    a weaker statement than "answered the question".
    """
    if problem.gold_arguments:
        return problem.gold_arguments, True
    if not problem.wants_value:
        return (), True
    return report.submitted_arguments, False


def build_check_source(
    problem: InterfaceProblem,
    answer_module: str,
    arguments: Sequence[str] = (),
) -> str:
    """The third module: the ascription the kernel decides, and the audit.

    Nothing about the answer appears here except its module name and the
    arguments. That is the point -- the check does not re-elaborate the
    answer's text, it cites one constant and states the type it must have.

    Pass the *gold* arguments when the benchmark knows them: the ascription
    then rejects a wrong value by itself. See :func:`check_arguments`.
    """
    applied = " ".join("(%s)" % argument for argument in arguments)
    target = problem.target + (" " + applied if applied else "")
    return "\n".join(
        [
            "import %s" % problem.module,
            "import %s" % answer_module,
            "",
            "-- The whole verdict. If this typechecks, the answer's proof term",
            "-- inhabits the proposition the problem froze before the answer",
            "-- existed -- whatever helpers it went through to get there.",
            "theorem %s : %s :=" % (CHECK_THEOREM, target),
            "  %s.%s" % (SUBMISSION_NAMESPACE, SOLUTION_NAME),
            "",
            "-- Typechecking is not enough: `sorry` elaborates to `sorryAx` and",
            "-- a declared `axiom` passes the kernel with no complaint at all.",
            "-- This listing is the only place either one shows up.",
            "#print axioms %s" % CHECK_THEOREM,
            "",
        ]
    )


@dataclass(frozen=True)
class ContestPolicy:
    """Where "what counts as a proof" is a decision rather than a fact.

    Four knobs. Each one is a place a setter could reasonably choose
    differently, so each is data with its argument attached rather than a
    default buried in the code.
    """

    #: Module prefixes an answer may import, besides the problem's own.
    #: An unlisted import is refused because a self-supplied module can
    #: carry its own axioms -- the whole audit is worthless if the answer
    #: can bring a file that declares `axiom cheat : False`.
    allowed_imports: tuple[str, ...] = DEFAULT_ALLOWED_IMPORTS

    #: Axioms a proof may depend on. The three defaults are Lean's own
    #: classical foundations, which essentially all of Mathlib rests on;
    #: refusing them would refuse mathematics. Anything else in the listing
    #: was introduced by the answer, and a declared axiom passes the kernel
    #: with no complaint at all -- which is why this listing, not the
    #: kernel's verdict, is the real check.
    allowed_axioms: frozenset[str] = LEGITIMATE_AXIOMS

    #: Whether an answer may declare file-level `instance`s.
    #:
    #: Allowed, and that is a consequence of freezing the target rather
    #: than a concession. An instance can change how a proposition
    #: *elaborates*, which is why it had to be policed when the target was
    #: re-elaborated alongside the answer. `Problem.Target` is elaborated
    #: inside a sealed module, so an answer's instances can only help it
    #: *prove* the target -- and plenty of legitimate Lean proofs need one.
    #: The dependency closure is still audited, so an instance cannot
    #: smuggle anything past the axiom listing.
    allow_global_instances: bool = True

    #: Whether each answer must build in its own directory, with only its
    #: own artifacts and the problem's on the search path.
    #:
    #: On by default and there is no good reason to turn it off: sharing a
    #: build directory would let one participant's `.olean` be importable by
    #: another's, which makes a verdict depend on grading order.
    isolate_builds: bool = True

    def __post_init__(self) -> None:
        if not self.isolate_builds:
            raise ValueError("non-isolated builds are not supported")

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_imports": list(self.allowed_imports),
            "allowed_axioms": sorted(self.allowed_axioms),
            "allow_global_instances": self.allow_global_instances,
            "isolate_builds": self.isolate_builds,
        }

    def audit(self, axioms: "Sequence[str] | None") -> "SoundnessReport":
        """Judge an axiom listing. ``None`` means the audit did not run.

        A missing listing is never a pass: the whole point of the audit is
        that a term can typecheck without being a proof, so "we did not
        look" has to be distinguishable from "we looked and it was clean".
        """
        if axioms is None:
            return SoundnessReport().with_violation(
                label(
                    HackClass.PLACEHOLDER,
                    "kernel.audit_missing",
                    "no `#print axioms` listing was obtained, so nothing here is "
                    "evidence that the proof is free of `sorry` or of a declared "
                    "axiom",
                )
            )
        return audit_axioms(axioms, allowed=self.allowed_axioms)


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
        if self.build is None:
            return ""  # not run, which is not a refusal
        if not self.build.verified:
            return "kernel"
        if self.axiom_audit is not None and not self.axiom_audit.ok:
            return "axioms"
        return ""

    @property
    def kernel_checked(self) -> bool:
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


def grade_interface(
    problem: InterfaceProblem,
    problem_source: str,
    answer_source: str,
    *,
    verifier: Any = None,
    participant: str = "",
    policy: ContestPolicy | None = None,
    answer_module: str = "",
    timeout_s: float = 300.0,
    on_stage: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> InterfaceVerdict:
    """Text screen, then the kernel, then the axiom listing. Never raises.

    The order is cheapest-first, and the first two are gates: an answer that
    does not present the interface is not compiled, and one the kernel
    rejects is not audited. What is *not* a gate is the audit -- it runs on
    an accepted proof precisely because acceptance is not the bar.
    """
    policy = policy or ContestPolicy()
    verdict = InterfaceVerdict(problem_id=problem.problem_id, participant=participant)

    def record(stage: str, status: str, payload: Any = None, detail: str = "") -> None:
        event = {"stage": stage, "status": status, "payload": payload, "detail": detail}
        verdict.stages.append(event)
        if on_stage is not None:
            on_stage(stage, event)

    record("interface", "running")
    try:
        report = check_interface(problem, answer_source, allowed_imports=policy.allowed_imports)
        if not policy.allow_global_instances:
            instances = [d for d in parse_lean_file(answer_source).declarations if d.kind == "instance"]
            if instances:
                report.faults += ((InterfaceFault.INSTANCE_NOT_ALLOWED, "benchmark policy forbids global instances"),)
    except Exception as exc:
        report = InterfaceReport(problem.problem_id, faults=((InterfaceFault.UNPARSED, str(exc)),))
    verdict.report = report
    record("interface", "passed" if report.ok else "failed", report.to_dict())
    if not report.ok or verifier is None:
        why = "interface refused" if not report.ok else "no prover configured"
        record("kernel", "not_run", detail=why)
        record("axioms", "not_run", detail="kernel check did not pass")
        return verdict

    arguments, against_gold = check_arguments(problem, report)
    verdict.against_gold = against_gold
    module = answer_module or "%s.Submission.%s" % (
        problem.module.split(".")[0] or "Bench", participant or "answer",
    )
    check_source = build_check_source(problem, module, arguments)
    from ..backends.types import ModuleSource

    record("kernel", "running")
    try:
        build = verifier.build_modules(
            [ModuleSource(problem.module, problem_source, cacheable=True),
             ModuleSource(module, answer_source),
             ModuleSource("%s.Check" % module, check_source)],
            audit_declaration=CHECK_THEOREM, timeout_s=timeout_s,
        )
    except Exception as exc:
        build = ModuleBuild(Status.ERROR, diagnostics=(Diagnostic(Severity.ERROR, str(exc)),))
    verdict.build = build
    status = "not_run" if build is None or build.status in (Status.ERROR, Status.SKIPPED) else (
        "passed" if build.verified else "failed"
    )
    record("kernel", status, build.to_dict() if build else None,
           "module builds unavailable" if build is None else "")
    if build is not None and build.verified:
        record("axioms", "running")
        verdict.axiom_audit = policy.audit(build.axioms)
        record("axioms", "passed" if verdict.axiom_audit.ok else "failed", verdict.axiom_audit.to_dict())
    else:
        record("axioms", "not_run", detail="kernel check did not pass")
    return verdict
