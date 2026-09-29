"""Generate trusted Problem/Goal modules and untrusted Submission/Check modules in order."""
from __future__ import annotations

import hashlib
from typing import Sequence
from ...backends.types import ModuleSource
from .interface import CHECK_THEOREM, SUBMISSION_NAMESPACE, SOLUTION_NAME, InterfaceProblem


def build_check_source(
    problem: InterfaceProblem,
    answer_module: str,
    arguments: Sequence[str] = (),
) -> str:
    """Check a frozen goal constant and audit its proof dependencies.

    Gold expressions elaborate in the preceding trusted Goal module. For an
    open value problem the Goal is existential; Lean infers the witness from
    Submission.solution without re-parsing the answer's local value expression.
    """
    applied = " ".join("(%s)" % argument for argument in arguments)
    goal = build_goal_module(problem)
    target = ("_root_." + _goal_namespace(problem) + ".goal" if goal else
              "_root_." + problem.target + (" " + applied if applied else ""))
    proof = "%s.%s" % (SUBMISSION_NAMESPACE, SOLUTION_NAME)
    if problem.wants_value and not problem.gold_arguments:
        for _ in problem.target_parameters:
            proof = "⟨_, %s⟩" % proof
    return "\n".join(
        [
            "import %s" % problem.module,
            *(["import %s" % goal.module] if goal else []),
            "import %s" % answer_module,
            "",
            "-- The whole verdict. If this typechecks, the answer's proof term",
            "-- inhabits the proposition the problem froze before the answer",
            "-- existed -- whatever helpers it went through to get there.",
            "theorem %s : %s :=" % (CHECK_THEOREM, target),
            "  " + proof,
            "",
            "-- Typechecking is not enough: `sorry` elaborates to `sorryAx` and",
            "-- a declared `axiom` passes the kernel with no complaint at all.",
            "-- This listing is the only place either one shows up.",
            "#print axioms %s" % CHECK_THEOREM,
            "",
        ]
    )


def _goal_namespace(problem: InterfaceProblem) -> str:
    digest = hashlib.sha256((problem.module + "\0" + "\0".join(problem.gold_arguments)).encode("utf-8")).hexdigest()[:20]
    return "FtpEvalGoal.G" + digest


def build_goal_module(problem: InterfaceProblem) -> ModuleSource | None:
    """Freeze gold, or an existential value goal, before importing an answer."""
    if not problem.gold_arguments and not problem.wants_value:
        return None
    namespace = _goal_namespace(problem)
    applied = " ".join("(%s)" % value for value in problem.gold_arguments)
    if problem.gold_arguments:
        proposition = "_root_.Problem.Target " + applied
    else:
        parameters = ["ftp_value_%d" % i for i in range(len(problem.target_parameters))]
        proposition = "∃ " + " ".join(parameters) + ", @_root_.Problem.Target " + " ".join(parameters)
    source = ("import %s\nnamespace %s\ndef goal : Prop := %s\nend %s\n"
              % (problem.module, namespace, proposition, namespace))
    return ModuleSource(problem.module + ".Goal", source, cacheable=True)


def build_submission_modules(problem: InterfaceProblem, problem_source: str,
                             answer_source: str, answer_module: str,
                             arguments: Sequence[str]) -> list[ModuleSource]:
    modules = [ModuleSource(problem.module, problem_source, cacheable=True)]
    goal = build_goal_module(problem)
    if goal is not None:
        modules.append(goal)
    modules.extend([ModuleSource(answer_module, answer_source),
                    ModuleSource(answer_module + ".Check", build_check_source(problem, answer_module, arguments))])
    return modules
