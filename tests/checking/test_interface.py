"""Grading against a frozen proposition.

The design under test: the problem is a module compiled before any answer
exists, and an answer must export one declaration with one type,
``Submission.solution : Problem.Target``. Helpers need no correspondence to
anything -- Lean walks the dependency closure itself.

What these tests pin is mostly *absence*: the whole family of attacks that
the previous text-matching design had to detect one by one -- redefining a
term the statement rests on, injecting a `variable`, swapping an
`instance`, restating the goal -- cannot be expressed here, because the
answer never states the proposition. It cites it.

Nothing here runs a real prover. The three-module build is exercised only
to the point of confirming that a backend which cannot do it says so, and
that "did not run" never reads as a pass.
"""

from __future__ import annotations

from ftp_eval import create
from ftp_eval.backends.types import ModuleSource, Status
from ftp_eval.proving.checking.interface import CHECK_THEOREM, SOLUTION_NAME, SUBMISSION_NAMESPACE, InterfaceFault, InterfaceProblem
from ftp_eval.proving.checking.policy import ContestPolicy
from ftp_eval.proving.checking.modules import build_check_source
from ftp_eval.proving.checking.screening import check_arguments, check_interface, read_interface_problem
from ftp_eval.proving.checking.evaluator import grade_interface

PROBLEM = """\
import Mathlib

namespace Problem

abbrev Good (n : Nat) : Prop := 0 < n

lemma givenLemma : Good 1 := by decide

def Target (a0 : Nat) : Prop :=
  IsLeast { n | Good n } a0

end Problem
"""

ANSWER = """\
import FtpEvalBench.P001

namespace Submission

def myDef : Nat := 1

lemma myLemma : Problem.Good 1 := by decide

instance : Inhabited Nat := ⟨0⟩

theorem solution : Problem.Target 1 := by
  exact ⟨myLemma, fun n hn => hn⟩

end Submission
"""


def problem(gold: tuple[str, ...] = ()) -> InterfaceProblem:
    read = read_interface_problem(PROBLEM, problem_id="P001", module="FtpEvalBench.P001")
    return InterfaceProblem("P001", "FtpEvalBench.P001", read.target_parameters, gold)


def faults(report) -> set[str]:
    return {f.value for f, _ in report.faults}


# -- reading the sealed side -------------------------------------------


def test_the_targets_parameters_are_read_not_guessed():
    read = read_interface_problem(PROBLEM, problem_id="P001", module="FtpEvalBench.P001")
    assert read.target == "Problem.Target"
    assert read.target_parameters == ("a0",)
    assert read.wants_value


def test_a_plain_prove_this_target_takes_no_value():
    source = PROBLEM.replace(
        "def Target (a0 : Nat) : Prop :=\n  IsLeast { n | Good n } a0",
        "def Target : Prop :=\n  ∀ n : Nat, Good n → 0 < n",
    )
    read = read_interface_problem(source, problem_id="P002", module="FtpEvalBench.P002")
    assert read.target_parameters == ()
    assert not read.wants_value


# -- the interface an answer must present ------------------------------


def test_an_answer_may_bring_as_many_helpers_as_it_likes():
    """The point of the design. No correspondence to anything is required."""
    report = check_interface(problem(), ANSWER, allowed_imports=["Mathlib"])
    assert report.ok, report.format_text()
    assert report.helper_count == 3  # def, lemma, instance -- none penalised


def test_a_file_level_instance_is_allowed():
    """A consequence of freezing the target, not a concession.

    An instance can change how a proposition *elaborates*, which is why it
    had to be policed when the target was re-elaborated next to the answer.
    `Problem.Target` is elaborated inside a sealed module, so an answer's
    instances can only help prove it.
    """
    assert ContestPolicy().allow_global_instances
    report = check_interface(problem(), ANSWER, allowed_imports=["Mathlib"])
    assert InterfaceFault.REWARD_HACKING.value not in faults(report)


def test_the_solution_must_be_named_exactly():
    report = check_interface(
        problem(), ANSWER.replace("theorem solution", "theorem mySolution")
    )
    assert InterfaceFault.SOLUTION_MISSING.value in faults(report)
    detail = next(d for f, d in report.faults if f is InterfaceFault.SOLUTION_MISSING)
    assert SOLUTION_NAME in detail and SUBMISSION_NAMESPACE in detail


def test_an_unproved_solution_is_an_honest_miss_not_a_cheat():
    report = check_interface(
        problem(), ANSWER.replace("  exact ⟨myLemma, fun n hn => hn⟩", "  sorry")
    )
    assert not report.ok
    assert report.honest_miss, faults(report)
    assert InterfaceFault.REWARD_HACKING.value not in faults(report)


def test_a_sorry_in_a_helper_is_not_an_honest_miss():
    report = check_interface(
        problem(), ANSWER.replace("lemma myLemma : Problem.Good 1 := by decide",
                                  "lemma myLemma : Problem.Good 1 := by sorry")
    )
    assert InterfaceFault.REWARD_HACKING.value in faults(report)
    assert not report.honest_miss


def test_declaring_into_the_problem_namespace_is_refused():
    """Reopening a sealed module.

    It cannot actually change `Problem.Target` -- that module is already
    compiled -- but a `Problem.Good` in the answer is a confusing second
    constant with the benchmark's name on it, so it is refused outright.
    """
    hostile = ANSWER.replace(
        "namespace Submission",
        "namespace Problem\nabbrev Good (n : Nat) : Prop := True\nend Problem\n\nnamespace Submission",
    )
    report = check_interface(problem(), hostile)
    assert InterfaceFault.PROBLEM_NAMESPACE_REUSED.value in faults(report)


def test_declarations_must_stay_inside_the_submission_namespace():
    escaped = ANSWER.replace(
        "def myDef : Nat := 1", "end Submission\n\ndef myDef : Nat := 1\n\nnamespace Submission"
    )
    report = check_interface(problem(), escaped)
    assert InterfaceFault.DECLARATION_OUTSIDE_NAMESPACE.value in faults(report)


def test_an_unlisted_import_is_refused():
    """A self-supplied module can carry its own axioms."""
    report = check_interface(
        problem(),
        ANSWER.replace("import FtpEvalBench.P001", "import FtpEvalBench.P001\nimport MyOwnModule"),
        allowed_imports=["Mathlib"],
    )
    assert InterfaceFault.IMPORT_NOT_ALLOWED.value in faults(report)


def test_the_problems_own_module_is_always_importable():
    report = check_interface(problem(), ANSWER, allowed_imports=["Mathlib"])
    assert InterfaceFault.IMPORT_NOT_ALLOWED.value not in faults(report)


# -- the value, compared by the kernel rather than as text -------------


def test_the_check_is_built_against_the_gold_value_when_there_is_one():
    """So the ascription itself rejects a wrong answer.

    Nothing compares `1` to `4` as a string, and nothing has to decide
    whether `4` and `2 + 2` are the same text: the kernel does it, on
    definitional equality.
    """
    with_gold = problem(gold=("4",))
    report = check_interface(with_gold, ANSWER, allowed_imports=["Mathlib"])
    assert report.submitted_arguments == ("1",)
    arguments, against_gold = check_arguments(with_gold, report)
    assert arguments == ("4",) and against_gold
    from ftp_eval.proving.checking.modules import build_goal_module
    assert "Problem.Target (4)" in build_goal_module(with_gold).source
    check = build_check_source(with_gold, "FtpEvalBench.Sub.a", arguments)
    assert "import FtpEvalBench.P001.Goal" in check
    assert "Problem.Target (4)" not in check


def test_without_gold_the_witness_is_reported_without_a_fixed_value():
    """The frozen predicate, rather than a canonical witness, defines validity."""
    open_problem = problem()
    report = check_interface(open_problem, ANSWER, allowed_imports=["Mathlib"])
    arguments, against_gold = check_arguments(open_problem, report)
    assert arguments == ("1",) and not against_gold


def test_the_check_module_cites_the_solution_and_audits_it():
    source = build_check_source(problem(), "FtpEvalBench.Sub.alice", ("4",))
    assert "import FtpEvalBench.P001" in source
    assert "import FtpEvalBench.Sub.alice" in source
    assert "%s.%s" % (SUBMISSION_NAMESPACE, SOLUTION_NAME) in source
    assert "#print axioms %s" % CHECK_THEOREM in source
    # It cites one constant. The answer's text is never re-elaborated here.
    assert "lemma" not in source and "myDef" not in source


# -- the axiom audit ---------------------------------------------------


def test_classical_foundations_are_allowed_and_anything_else_is_not():
    policy = ContestPolicy()
    assert policy.audit(["propext", "Classical.choice", "Quot.sound"]).ok
    assert not policy.audit(["sorryAx"]).ok
    assert not policy.audit(["Submission.cheat"]).ok
    assert not policy.audit(["Lean.ofReduceBool"]).ok


def test_a_missing_axiom_listing_is_never_a_pass():
    """"We did not look" must be distinguishable from "we looked and it was clean"."""
    report = ContestPolicy().audit(None)
    assert not report.ok
    assert "no `#print axioms` listing" in report.violations[0]


# -- the whole verdict -------------------------------------------------


def test_solved_requires_all_three_checks():
    verdict = grade_interface(problem(), PROBLEM, ANSWER, verifier=None)
    assert verdict.report is not None and verdict.report.ok
    assert not verdict.solved, "solved without the kernel"
    assert not verdict.kernel_checked
    assert "NOT RUN" in verdict.format_text()


def test_a_backend_that_cannot_build_modules_says_so_rather_than_passing():
    verdict = grade_interface(
        problem(), PROBLEM, ANSWER, verifier=create("mock"), participant="alice"
    )
    assert verdict.build is None, "the mock claimed a multi-module build"
    assert not verdict.solved
    assert not verdict.kernel_checked


def test_an_interface_fault_stops_before_the_prover():
    verdict = grade_interface(
        problem(),
        PROBLEM,
        ANSWER.replace("theorem solution", "theorem mine"),
        verifier=create("mock"),
    )
    assert verdict.refused_at == "interface"
    assert verdict.build is None
    assert not verdict.solved


def test_the_verdict_round_trips():
    import json

    verdict = grade_interface(problem(gold=("1",)), PROBLEM, ANSWER, verifier=None)
    restored = json.loads(json.dumps(verdict.to_dict()))
    assert restored["problem_id"] == "P001"
    assert restored["solved"] is False
    assert restored["interface"]["ok"] is True
    assert restored["build"] is None


# -- isolation ---------------------------------------------------------


def test_only_the_problem_module_is_cacheable():
    """One participant's artifacts must never be importable by another's.

    The problem is identical for everyone and trusted, so caching it is both
    safe and where the time goes. An answer is neither.
    """
    assert ModuleSource("FtpEvalBench.P001", "x", cacheable=True).cacheable
    assert not ModuleSource("FtpEvalBench.Sub.alice", "x").cacheable
    assert ContestPolicy().isolate_builds


def test_a_module_name_maps_to_a_path():
    assert ModuleSource("FtpEvalBench.Sub.alice", "x").path_parts == ("FtpEvalBench", "Sub", "alice")


def test_the_mock_backend_reports_no_module_support():
    backend = create("mock")
    assert backend.build_modules([ModuleSource("A", "x")]) is None
    # ...and its single-file path still works, so this is a missing
    # capability rather than a broken backend.
    assert backend.info().available
    assert backend.info().language == "lean4"


def test_status_verified_is_still_the_only_pass():
    from ftp_eval.backends.types import ModuleBuild

    assert ModuleBuild(Status.VERIFIED).verified
    for status in (Status.FAILED, Status.TIMEOUT, Status.ERROR, Status.SKIPPED):
        assert not ModuleBuild(status).verified


# -- the committed example stays honest --------------------------------


def test_the_example_check_file_is_what_the_generator_produces():
    """`examples/frozen-target/Check.lean` is committed for reading.

    A hand-written example of generated output drifts, and then the
    documentation quietly describes something the code does not do. This
    compares the two with comments stripped, so the prose in the fixture is
    free to explain while the Lean has to match.
    """
    import pathlib

    from ftp_eval.backends.comments import strip_comments

    directory = pathlib.Path(__file__).resolve().parents[2] / "examples" / "frozen-target"
    read = read_interface_problem(
        (directory / "Problem.lean").read_text(encoding="utf-8"),
        problem_id="demo",
        module="Problem",
    )
    demo = InterfaceProblem("demo", "Problem", read.target_parameters, ("1",))
    generated = build_check_source(demo, "Answer", ("1",))
    committed = (directory / "Check.lean").read_text(encoding="utf-8")

    def code(text: str) -> list[str]:
        stripped = strip_comments(text, "lean4")
        return [line.strip() for line in stripped.splitlines() if line.strip()]

    assert code(generated) == code(committed)


def test_the_example_answer_clears_the_interface():
    import pathlib

    directory = pathlib.Path(__file__).resolve().parents[2] / "examples" / "frozen-target"
    read = read_interface_problem(
        (directory / "Problem.lean").read_text(encoding="utf-8"),
        problem_id="demo",
        module="Problem",
    )
    demo = InterfaceProblem("demo", "Problem", read.target_parameters, ("1",))
    report = check_interface(
        demo,
        (directory / "Answer.lean").read_text(encoding="utf-8"),
        allowed_imports=["Mathlib"],
    )
    assert report.ok, report.format_text()
    # A def, a lemma and an instance, none of them penalised.
    assert report.helper_count == 3
