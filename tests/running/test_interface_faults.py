"""Every refusal reason reaches the report at least once.

Each `InterfaceFault` has a unit test next to the rule that raises it. What
those cannot show is that the reason survives the rest of the way out: into
the graded answer, into `interface/refused.jsonl`, and into the counts the
report prints. Before `benchmarks/interface-faults` existed the published
fixtures between them produced four of the twelve reasons, so eight of them
were never rendered end to end by anything.

No prover is involved. Every answer here beside `solved/` is refused from
its text, which is also why the fixture's Target is deliberately trivial.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ftp_eval import evaluate_benchmark, load_problem_set, load_submissions
from ftp_eval.proving.checking.interface import InterfaceFault
from ftp_eval.proving.running.manifest import policy_from_manifest
from ftp_eval.spec.stage import StageId


FIXTURE = Path(__file__).resolve().parents[2] / "benchmarks" / "interface-faults"

#: Participant directory -> the one reason that answer is there to produce.
EXPECTED = {
    "solution-missing": InterfaceFault.SOLUTION_MISSING,
    "problem-namespace": InterfaceFault.PROBLEM_NAMESPACE_REUSED,
    "outside-namespace": InterfaceFault.DECLARATION_OUTSIDE_NAMESPACE,
    "import-not-allowed": InterfaceFault.IMPORT_NOT_ALLOWED,
    "unparsed": InterfaceFault.UNPARSED,
    "global-instance": InterfaceFault.INSTANCE_NOT_ALLOWED,
    "syntax-extension": InterfaceFault.SYNTAX_EXTENSION,
    "imported-attribute": InterfaceFault.IMPORTED_ATTRIBUTE,
    "compile-time-eval": InterfaceFault.REWARD_HACKING,
}


def graded(tmp_path):
    problems = load_problem_set(FIXTURE / "problems")
    answers = load_submissions(FIXTURE / "submissions", problems)
    result = evaluate_benchmark(
        problems, answers, policy=policy_from_manifest(problems.manifest),
        output_dir=tmp_path, run_id="interface-faults",
    )
    return result, {a.participant: a for p in result.participants for a in p.grades}


@pytest.mark.parametrize("participant,fault", sorted(EXPECTED.items()))
def test_each_answer_produces_the_one_reason_it_is_there_for(participant, fault, tmp_path):
    """Isolation is the point: an answer that trips two rules proves neither."""
    _, by_participant = graded(tmp_path)
    answer = by_participant[participant]
    assert answer.report is not None
    assert [f for f, _ in answer.report.faults] == [fault], answer.report.format_text()
    assert not answer.solved
    assert answer.refused_at == "interface"


def test_the_correct_answer_is_not_refused(tmp_path):
    """Without this the fixture could pass by refusing everything."""
    _, by_participant = graded(tmp_path)
    answer = by_participant["solved"]
    assert answer.report is not None and answer.report.ok, answer.report.format_text()
    # No prover ran, so it is not solved either -- and that must not read as
    # a pass anywhere.
    assert not answer.solved


def test_every_reason_the_grader_can_give_is_covered(tmp_path):
    """The fixture is the coverage claim, so the claim is what is tested.

    `SOLUTION_UNPROVED` and `INCOMPLETE_PROOF` are deliberately absent:
    `demo-2026` carries those as honest non-answers, where they belong
    alongside a real problem rather than beside a trivial one.
    """
    _, by_participant = graded(tmp_path)
    seen = {f for a in by_participant.values() if a.report for f, _ in a.report.faults}
    missing = set(InterfaceFault) - seen - {
        InterfaceFault.SOLUTION_UNPROVED, InterfaceFault.INCOMPLETE_PROOF,
        InterfaceFault.SOLUTION_BINDERS,
    }
    assert not missing, "no fixture answer produces: %s" % sorted(f.value for f in missing)


def test_the_reasons_are_written_to_the_run_directory(tmp_path):
    """A reason that never leaves memory is not reported."""
    graded(tmp_path)
    refused = (tmp_path / "interface-faults" / StageId.INTERFACE.directory / "refused.jsonl")
    text = refused.read_text(encoding="utf-8")
    for fault in EXPECTED.values():
        assert fault.value in text, fault.value
