"""Read the frozen interface and refuse malformed or forbidden answers before compilation."""
from __future__ import annotations

import re
from typing import Any, Sequence
from ...backends.comments import strip_comments
from ...backends.soundness import ANSWER_HACK_CLASSES, HackClass, parse_label, screen_source
from ..lean_file import LeanFile, parse_lean_file
from .interface import PROBLEM_NAMESPACE, SUBMISSION_NAMESPACE, SOLUTION_NAME, TARGET_NAME, InterfaceFault, InterfaceProblem, InterfaceReport


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
    targets = [d for d in parsed.declarations if d.qualified_name == "Problem.Target"]
    if len(targets) != 1:
        raise ValueError("%s: expected a frozen Problem.Target definition" % problem_id)
    target = targets[0]
    if target.kind not in ("def", "abbrev"):
        raise ValueError("%s: Problem.Target must be a def or abbrev returning Prop" % problem_id)
    depth = 0
    result_type = ""
    for index, char in enumerate(target.signature):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ":" and depth == 0:
            result_type = target.signature[index + 1:].strip()
            break
    if result_type != "Prop":
        raise ValueError("%s: Problem.Target requires an explicit result type : Prop" % problem_id)
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
    if solution is not None and solution.signature.strip() and not solution.signature.lstrip().startswith(":"):
        faults.append((InterfaceFault.SOLUTION_BINDERS,
                       "Submission.solution must have no binders before its result type; "
                       "put helper parameters on separate declarations and submit a closed solution"))
    faults.extend(_screen(answer_source, solution))
    faults.extend(_extension_faults(answer_source))
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
        if PROBLEM_NAMESPACE in d.namespace.split(".")
        or PROBLEM_NAMESPACE in d.qualified_name.split(".")[:-1]
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

    File scope because a proof can cite anything in the file. Placeholders
    in partial proofs and helpers are incomplete proofs, independently of
    new axioms or kernel escapes. Every such fault still refuses the answer.
    """
    report = screen_source(
        answer_source, "lean4", required_statement=None, allowed_imports=None,
        classes=ANSWER_HACK_CLASSES,
    )
    placeholders = [v for v in report.violations
                    if parse_label(v)[0] == HackClass.PLACEHOLDER.value]
    violations = [v for v in report.violations
                  if parse_label(v)[0] != HackClass.PLACEHOLDER.value]
    faults = []
    # Preserve the established bare-sorry fault; partial and helper holes
    # use the new name. Both belong to InterfaceReport.incomplete_proof.
    if placeholders and not (solution is not None and solution.is_unproved):
        faults.append((InterfaceFault.INCOMPLETE_PROOF, "; ".join(placeholders)))
    if violations:
        faults.append((InterfaceFault.REWARD_HACKING, "; ".join(violations)))
    return faults


def _extension_faults(source: str) -> list[tuple[InterfaceFault, str]]:
    """Contest policy: answers may prove, but cannot extend the checker language."""
    body = re.sub(r'"(?:\\.|[^"\\])*"', '""', strip_comments(source, "lean4"))
    faults: list[tuple[InterfaceFault, str]] = []
    extension = re.search(r"\b(notation|infixl|infixr|infix|prefix|postfix|macro_rules|macro|syntax|declare_syntax_cat|elab_rules|elab|run_tac)\b", body)
    if extension:
        faults.append((InterfaceFault.SYNTAX_EXTENSION,
                       "answers must not extend Lean syntax or execute elaborator code (%s); inline local notation" % extension.group(1)))
    namespace: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("namespace "):
            namespace.append(stripped.split()[1])
        elif stripped == "end" or stripped.startswith("end "):
            if namespace:
                namespace.pop()
        if re.search(r"\battribute\b", stripped):
            match = re.fullmatch(r"(?:local\s+)?attribute\s+\[[^\]]+\]\s+(.+)", stripped)
            names = match.group(1).split() if match else []
            scope = ".".join(namespace)
            local_scope = scope == "Submission" or scope.startswith("Submission.")
            if not names or any(not (name.removeprefix("_root_.").startswith("Submission.")
                                     or (local_scope and "." not in name)) for name in names):
                faults.append((InterfaceFault.IMPORTED_ATTRIBUTE,
                               "attribute commands may target only Submission declarations"))
    return faults


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
            "self-supplied module can carry its own axioms. Import %s for this problem; "
            "other allowed import prefixes: %s"
            % (", ".join(bad[:4]), problem_module, ", ".join(allowed) or "none"),
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

    Without gold, the trusted Goal freezes an existential over Target's
    parameters. Check.lean infers the witness from Submission.solution;
    the returned submitted arguments are for reporting only. This fully
    validates construction tasks with multiple or infinitely many valid
    answers. ``against_gold=False`` records that no fixed value was required.
    """
    if problem.gold_arguments:
        return problem.gold_arguments, True
    if not problem.wants_value:
        return (), True
    return report.submitted_arguments, False
