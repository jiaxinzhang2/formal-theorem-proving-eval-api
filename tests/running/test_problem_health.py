"""Is a problem set fit to grade?

These checks moved out of the faithfulness API because none of them looks
at the prose. Four need a prover; ``assumes_nothing`` is text only and runs
without one, which is why a setter with no toolchain still gets something.

The one that matters most is ``non_vacuous``. A vacuously true problem is
the worst thing to publish: every answer to it is genuinely valid, the
kernel is entirely satisfied, and none of them proves anything. No amount
of checking the answers finds it.
"""

from __future__ import annotations

from ftp_eval import StatementTask, create
from ftp_eval.proving.running.problem_health import (
    HealthKind,
    check_problem_health,
    format_health_summary,
)


def task(formal: str, **kw) -> StatementTask:
    return StatementTask(
        task_id=kw.pop("task_id", "t"),
        informal_statement=kw.pop("informal", ""),
        formal_statement=formal,
        language="lean4",
        **kw,
    )


def check(kind: HealthKind, report) -> object:
    for c in report.checks:
        if c.kind is kind:
            return c
    raise AssertionError("no %s check in %s" % (kind, [c.kind for c in report.checks]))


# -- assumes_nothing: text only, so it always runs --------------------


def test_a_problems_own_hole_is_not_a_defect():
    # A problem file's `sorry` is what a participant fills in. Flagging it
    # would mark every problem in a benchmark ungradeable.
    report = check_problem_health(None, task("theorem t : True := by sorry"))
    assert check(HealthKind.ASSUMES_NOTHING, report).passed is True
    assert not report.ungradeable


def test_variable_bindings_are_not_a_defect():
    report = check_problem_health(
        None, task("variable (n : Nat)" + chr(92) + "ntheorem t : n + 0 = n := by sorry")
    )
    assert check(HealthKind.ASSUMES_NOTHING, report).passed is True


def test_a_problem_that_assumes_its_conclusion_is_ungradeable():
    report = check_problem_health(None, task("axiom cheat : True"))
    assumes = check(HealthKind.ASSUMES_NOTHING, report)
    assert assumes.passed is False and assumes.fatal
    assert report.ungradeable


def test_an_elaborator_setting_that_weakens_the_goal_is_caught():
    report = check_problem_health(
        None, task("set_option autoImplicit true" + chr(92) + "ntheorem t : P := by sorry")
    )
    assert check(HealthKind.ASSUMES_NOTHING, report).passed is False


def test_the_text_check_runs_with_no_prover_at_all():
    # The point of keeping it separate: a setter with no Lean toolchain
    # still gets this one.
    report = check_problem_health(None, task("axiom cheat : True"))
    assert report.checked
    assert report.ungradeable


# -- the prover questions ---------------------------------------------


def test_without_a_prover_the_probes_report_not_checked():
    report = check_problem_health(None, task("theorem t : True := by"))
    for kind in (HealthKind.ELABORATES, HealthKind.NON_TRIVIAL, HealthKind.NON_VACUOUS):
        c = check(kind, report)
        assert c.passed is None, "%s claimed an answer with no prover" % kind
        assert "no prover" in c.detail


def test_a_well_formed_problem_with_no_prover_is_not_called_healthy():
    # `checked` is True because the text check ran, but no probe did, and
    # the summary has to say so rather than implying a clean bill.
    report = check_problem_health(None, task("theorem t : True := by"))
    assert not report.ungradeable and not report.suspect
    probed = [c for c in report.checks if c.kind is not HealthKind.ASSUMES_NOTHING]
    assert all(c.passed is None for c in probed)


def test_the_mock_backend_builds_no_probes_and_says_so():
    # The mock cannot build statement probes; that must read as "not run",
    # never as a pass.
    report = check_problem_health(create("mock"), task("theorem t : True := by"))
    elaborates = check(HealthKind.ELABORATES, report)
    assert elaborates.passed is None
    assert "cannot build" in elaborates.detail


# -- the summary ------------------------------------------------------


def test_the_summary_names_what_was_never_checked():
    reports = [check_problem_health(None, task("theorem t%d : True := by" % i)) for i in range(3)]
    text = format_health_summary(reports)
    assert "NOT CHECKED (3)" in text
    assert "nothing here is evidence" in text


def test_the_summary_leads_with_ungradeable_problems():
    reports = [
        check_problem_health(None, task("axiom cheat : True", task_id="P001")),
        check_problem_health(None, task("theorem P002 : True := by", task_id="P002")),
    ]
    text = format_health_summary(reports)
    assert "UNGRADEABLE (1)" in text
    assert "P001" in text
    assert "every answer to it is valid and none proves anything" in text


def test_a_health_report_round_trips():
    import json

    report = check_problem_health(None, task("axiom cheat : True", task_id="P001"))
    restored = json.loads(json.dumps(report.to_dict()))
    assert restored["problem_id"] == "P001"
    assert restored["ungradeable"] is True
    assert restored["checks"][0]["kind"] == "assumes_nothing"
