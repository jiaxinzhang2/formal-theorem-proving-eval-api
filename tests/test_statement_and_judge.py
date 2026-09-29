"""Statement checking, judges, and the combined end-to-end verdict."""

from __future__ import annotations

import json

import pytest

from ftp_eval import (
    CheckKind,
    ConsensusJudge,
    JudgeError,
    JudgeLabel,
    MockJudge,
    ProbeKind,
    ProofAttempt,
    StatementChecker,
    StatementStatus,
    StatementTask,
    create,
    create_judge,
    load_triplets,
)
from ftp_eval.proving.backends.lean4 import Lean4Verifier, parse_lean_theorem, parse_printed_axioms
from ftp_eval.pipeline import EndToEndRunner, EndToEndStatus, combine
from ftp_eval.formalizing.checker import format_statement_summary


def task(formal="theorem t (n : Nat) (h : 0 < n) : n ^ 2 >= n := by", **kw) -> StatementTask:
    kw.setdefault("informal_statement", "Let n be a positive integer. Show n^2 >= n.")
    kw.setdefault("header", "import Mathlib")
    return StatementTask(task_id="t", formal_statement=formal, **kw)


# -- Lean theorem parsing (what probes are built from) ----------------


def test_parses_binders_and_conclusion():
    parsed = parse_lean_theorem("theorem foo (n : Nat) (h : 0 < n) : n ^ 2 >= n := by")
    assert parsed is not None
    assert parsed.name == "foo"
    assert parsed.binders == "(n : Nat) (h : 0 < n)"
    assert parsed.conclusion == "n ^ 2 >= n"


def test_colons_inside_binders_do_not_split_the_statement():
    parsed = parse_lean_theorem("theorem foo {α : Type} [Ring α] (x : α) : x + 0 = x")
    assert parsed is not None
    assert parsed.conclusion == "x + 0 = x"


def test_handles_a_statement_with_no_binders():
    parsed = parse_lean_theorem("theorem foo : 2 + 2 = 4 := by norm_num")
    assert parsed is not None
    assert parsed.binders == ""
    assert parsed.conclusion == "2 + 2 = 4"


def test_refuses_to_parse_what_it_cannot_parse():
    # Better to report "not checked" than to build a probe for the wrong
    # proposition and report a confident wrong answer.
    assert parse_lean_theorem("def f (n : Nat) : Nat := n") is None
    assert parse_lean_theorem("") is None
    assert parse_lean_theorem("theorem foo") is None


def test_rebuild_substitutes_the_goal_keeping_binders():
    parsed = parse_lean_theorem("theorem foo (n : Nat) (h : 0 < n) : n ^ 2 >= n")
    assert parsed is not None
    rebuilt = parsed.rebuild(name="probe", conclusion="False")
    assert rebuilt == "theorem probe (n : Nat) (h : 0 < n) : False"


# -- probe construction -----------------------------------------------


def test_builds_an_elaboration_probe_with_a_placeholder_body():
    probe = Lean4Verifier().build_probe(task(), ProbeKind.ELABORATES)
    assert probe is not None
    assert "import Mathlib" in probe
    assert probe.rstrip().endswith("sorry")


def test_builds_a_triviality_probe_from_the_real_binders():
    probe = Lean4Verifier().build_probe(task(), ProbeKind.TRIVIAL)
    assert probe is not None
    assert "(n : Nat) (h : 0 < n)" in probe
    assert "first |" in probe


def test_builds_a_vacuity_probe_targeting_False():
    probe = Lean4Verifier().build_probe(task(), ProbeKind.VACUOUS)
    assert probe is not None
    assert ": False" in probe


def test_no_vacuity_probe_when_there_are_no_hypotheses():
    probe = Lean4Verifier().build_probe(
        task(formal="theorem t : 2 + 2 = 4 := by"), ProbeKind.VACUOUS
    )
    assert probe is None


def test_gold_probe_needs_matching_binders():
    same = task(gold_formal_statement="theorem g (n : Nat) (h : 0 < n) : n * n >= n")
    assert Lean4Verifier().build_probe(same, ProbeKind.GOLD_EQUIVALENT) is not None
    different = task(gold_formal_statement="theorem g (m : Int) : m = m")
    assert Lean4Verifier().build_probe(different, ProbeKind.GOLD_EQUIVALENT) is None


def test_mock_backend_builds_no_probes():
    # An honest None, so checks report "did not run" rather than passing.
    assert create("mock").build_probe(task(), ProbeKind.TRIVIAL) is None


def test_probe_skips_soundness_screening():
    # The elaboration probe submits `sorry` on purpose; screening it would
    # reject every probe and tell us nothing.
    backend = create("mock")
    result = backend.probe(task(), "theorem t : True := by sorry MOCK_PASS")
    assert result.status.value == "verified"


# -- axiom listing parsing --------------------------------------------


def test_parses_an_axiom_listing():
    log = "'target' depends on axioms: [propext, Classical.choice, sorryAx]"
    assert parse_printed_axioms(log, "target") == ["propext", "Classical.choice", "sorryAx"]


def test_parses_the_no_axioms_case():
    assert parse_printed_axioms("'target' does not depend on any axioms", "target") == []


def test_missing_listing_returns_none_not_empty():
    # None means "the audit did not run", which must not read as clean.
    assert parse_printed_axioms("Building Mathlib...", "target") is None


# -- statement checks --------------------------------------------------


def test_statement_with_sorry_is_malformed():
    verdict = StatementChecker().check(task(formal="theorem t : True := by sorry"))
    assert verdict.status is StatementStatus.MALFORMED
    assert any(c.kind is CheckKind.NO_PLACEHOLDER and c.passed is False for c in verdict.checks)


def test_statement_declaring_an_axiom_is_malformed():
    verdict = StatementChecker().check(task(formal="axiom t : True"))
    assert verdict.status is StatementStatus.MALFORMED


def test_no_verifier_means_prover_checks_report_not_run():
    verdict = StatementChecker().check(task())
    by_kind = {c.kind: c for c in verdict.checks}
    assert by_kind[CheckKind.ELABORATES].passed is None
    assert "no verifier" in by_kind[CheckKind.ELABORATES].detail


def test_no_judge_means_faithfulness_is_unassessed():
    verdict = StatementChecker().check(task())
    assert verdict.status is StatementStatus.INCONCLUSIVE
    assert not verdict.checked_faithfulness


def test_structural_checks_alone_never_reach_ok():
    # Passing the prover checks is not evidence the statement means the
    # right thing, so the verdict stays INCONCLUSIVE without a judge.
    checker = StatementChecker(create("mock"), None)
    verdict = checker.check(task(formal="theorem t (h : 0 < 1) : True := by MOCK_FAIL"))
    assert verdict.status is not StatementStatus.OK


def test_judge_verdict_reaches_ok():
    checker = StatementChecker(None, MockJudge())
    verdict = checker.check(task(formal="theorem t (n : Nat) : n = n JUDGE_FAITHFUL := by"))
    assert verdict.status is StatementStatus.OK
    assert verdict.checked_faithfulness


def test_judge_rejection_is_suspicious():
    checker = StatementChecker(None, MockJudge())
    verdict = checker.check(task(formal="theorem t : True JUDGE_UNFAITHFUL := by"))
    assert verdict.status is StatementStatus.SUSPICIOUS


def test_judge_abstention_is_not_counted_as_a_failure():
    checker = StatementChecker(None, MockJudge())
    verdict = checker.check(task(formal="theorem t : True JUDGE_UNSURE := by"))
    assert verdict.status is StatementStatus.INCONCLUSIVE
    assert not verdict.failures


def test_judge_failure_does_not_become_a_rejection():
    # An outage must not turn into a wave of unfaithful verdicts.
    checker = StatementChecker(None, MockJudge())
    verdict = checker.check(task(formal="theorem t : True JUDGE_ERROR := by"))
    assert verdict.status is StatementStatus.INCONCLUSIVE
    assert not verdict.checked_faithfulness


def test_statement_verdict_round_trips():
    from ftp_eval.types import StatementVerdict

    verdict = StatementChecker(None, MockJudge()).check(
        task(formal="theorem t : True JUDGE_FAITHFUL := by")
    )
    restored = StatementVerdict.from_dict(json.loads(json.dumps(verdict.to_dict())))
    assert restored.status is verdict.status
    assert restored.judge is not None
    assert restored.judge.label is JudgeLabel.FAITHFUL


# -- judges ------------------------------------------------------------


def test_mock_judge_abstains_without_a_marker():
    assert MockJudge().judge(task()).label is JudgeLabel.UNSURE


def test_mock_judge_is_deterministic():
    a = MockJudge(faithful_rate=0.5).judge(task())
    b = MockJudge(faithful_rate=0.5).judge(task())
    assert a.label is b.label


def test_mock_judge_reports_zero_cost():
    usage = MockJudge().judge(task()).usage
    assert usage.calls == 1
    assert usage.estimated_cost_usd == 0.0


def test_consensus_takes_a_majority():
    judge = ConsensusJudge(MockJudge(), samples=3, recheck_rejections=0)
    verdict = judge.judge(task(formal="theorem t : True JUDGE_FAITHFUL := by"))
    assert verdict.label is JudgeLabel.FAITHFUL
    assert len(verdict.samples) == 3
    assert "3/3" in verdict.aggregation


def test_consensus_rechecks_a_rejection_before_it_stands():
    # The specific failure this guards against: a single low-effort pass
    # rejects a correct formalization and is never revisited.
    judge = ConsensusJudge(MockJudge(), samples=1, recheck_rejections=2)
    verdict = judge.judge(task(formal="theorem t : True JUDGE_UNFAITHFUL := by"))
    assert len(verdict.samples) == 3
    assert "rechecked" in verdict.aggregation


def test_consensus_sums_usage_across_samples():
    judge = ConsensusJudge(MockJudge(), samples=3, recheck_rejections=0)
    verdict = judge.judge(task(formal="theorem t : True JUDGE_FAITHFUL := by"))
    assert verdict.usage.calls == 3


def test_consensus_propagates_a_judge_error():
    judge = ConsensusJudge(MockJudge(), samples=2)
    with pytest.raises(JudgeError):
        judge.judge(task(formal="theorem t : True JUDGE_ERROR := by"))


def test_judge_registry_and_consensus_wrapping():
    plain = create_judge("mock")
    assert isinstance(plain, MockJudge)
    wrapped = create_judge("mock", consensus=3)
    assert isinstance(wrapped, ConsensusJudge)


def test_unknown_judge_names_alternatives():
    with pytest.raises(KeyError, match="known judges"):
        create_judge("nonexistent")


def test_claude_judge_reports_unavailable_without_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    info = create_judge("claude").info()
    assert info.costs_money
    assert info.model == "claude-opus-5"
    # Either the SDK is missing or the credential is; both must say so.
    assert info.detail


def test_claude_judge_cost_estimate_uses_the_price_table():
    from ftp_eval.formalizing.judges.claude import estimate_cost_usd

    cost = estimate_cost_usd("claude-opus-5", {"input_tokens": 1_000_000, "output_tokens": 0})
    assert cost == pytest.approx(5.00)
    # An unknown model reads as unknown, never as free.
    assert estimate_cost_usd("some-future-model", {"input_tokens": 1000}) is None


def test_claude_judge_never_turns_an_api_failure_into_a_rejection():
    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("503")

    from ftp_eval.formalizing.judges.claude import ClaudeJudge

    with pytest.raises(JudgeError):
        ClaudeJudge(client=Boom(), api_key="x").judge(task())


def test_claude_judge_parses_a_structured_verdict():
    class Block:
        type = "text"
        text = json.dumps(
            {"label": "unfaithful", "reasoning": "the hypothesis n > 0 is missing", "confidence": 0.9}
        )

    class Response:
        content = [Block()]
        stop_reason = "end_turn"
        model = "claude-opus-5"

        class usage:
            input_tokens = 500
            output_tokens = 40
            cache_read_input_tokens = 1200
            cache_creation_input_tokens = 0

    class Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                return Response()

    from ftp_eval.formalizing.judges.claude import ClaudeJudge

    verdict = ClaudeJudge(client=Client(), api_key="x").judge(task())
    assert verdict.label is JudgeLabel.UNFAITHFUL
    assert verdict.confidence == pytest.approx(0.9)
    assert verdict.usage.input_tokens == 500
    assert verdict.usage.cache_read_input_tokens == 1200
    assert verdict.usage.estimated_cost_usd is not None


def test_claude_judge_rejects_an_unparseable_response():
    class Block:
        type = "text"
        text = "I think it looks fine to me"

    class Response:
        content = [Block()]
        stop_reason = "end_turn"
        model = "claude-opus-5"
        usage = None

    class Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                return Response()

    from ftp_eval.formalizing.judges.claude import ClaudeJudge

    with pytest.raises(JudgeError, match="non-JSON"):
        ClaudeJudge(client=Client(), api_key="x").judge(task())


# -- end-to-end combination -------------------------------------------


def _verdict(status: StatementStatus, *, checked: bool = True):
    from ftp_eval.types import Check, StatementVerdict

    checks = (
        (Check(CheckKind.JUDGE_FAITHFUL, True, "ok"),)
        if checked
        else (Check(CheckKind.JUDGE_FAITHFUL, None, "not run"),)
    )
    return StatementVerdict(task_id="t", status=status, checks=checks)


def _proof(status):
    from ftp_eval.types import VerificationResult

    return VerificationResult(task_id="t", attempt_id="t#0", backend="mock", status=status)


def test_valid_proof_of_a_faithful_statement_is_solved():
    from ftp_eval.types import Status

    assert combine(_verdict(StatementStatus.OK), _proof(Status.VERIFIED)) is EndToEndStatus.SOLVED


def test_valid_proof_of_an_unfaithful_statement_gets_its_own_status():
    # The failure a proof-only harness would have scored as a win.
    from ftp_eval.types import Status

    assert (
        combine(_verdict(StatementStatus.SUSPICIOUS), _proof(Status.VERIFIED))
        is EndToEndStatus.PROVED_WRONG_STATEMENT
    )


def test_faithful_statement_without_a_proof_is_an_honest_miss():
    from ftp_eval.types import Status

    assert combine(_verdict(StatementStatus.OK), _proof(Status.FAILED)) is EndToEndStatus.UNPROVED


def test_malformed_statement_short_circuits():
    from ftp_eval.types import Status

    assert (
        combine(_verdict(StatementStatus.MALFORMED), _proof(Status.VERIFIED))
        is EndToEndStatus.BAD_STATEMENT
    )


def test_hacked_proof_is_reported_as_such():
    from ftp_eval.types import Status

    assert (
        combine(_verdict(StatementStatus.OK), _proof(Status.REJECTED))
        is EndToEndStatus.HACKED_PROOF
    )


def test_unassessed_faithfulness_cannot_be_called_solved():
    from ftp_eval.types import Status

    assert (
        combine(_verdict(StatementStatus.OK, checked=False), _proof(Status.VERIFIED))
        is EndToEndStatus.UNVERIFIABLE
    )


def test_trivial_formalization_with_a_valid_proof_does_not_score():
    """The degenerate end-to-end strategy must not win.

    Formalize the problem as `True`, prove it with `trivial`. Proof-only
    checking scores this 100%.
    """
    backend = create("mock")
    checker = StatementChecker(None, MockJudge())
    runner = EndToEndRunner(backend, checker)
    results = runner.run(
        [task(formal="theorem t : True JUDGE_UNFAITHFUL := by")],
        [ProofAttempt(task_id="t", proof=" trivial MOCK_PASS")],
    )
    assert results[0].status is EndToEndStatus.PROVED_WRONG_STATEMENT
    assert not results[0].solved
    assert "WRONG statement" in __import__(
        "ftp_eval.pipeline", fromlist=["format_end_to_end"]
    ).format_end_to_end(results)


# -- three-file loading ------------------------------------------------


def test_load_triplets_joins_on_task_id(tmp_path):
    (tmp_path / "nl.jsonl").write_text(
        '{"task_id": "b", "informal_statement": "second"}\n'
        '{"task_id": "a", "informal_statement": "first"}\n',
        encoding="utf-8",
    )
    # Deliberately a different order: the join must be by id, not by line.
    (tmp_path / "f.jsonl").write_text(
        '{"task_id": "a", "formal_statement": "theorem a : True := by"}\n'
        '{"task_id": "b", "formal_statement": "theorem b : True := by"}\n',
        encoding="utf-8",
    )
    (tmp_path / "p.jsonl").write_text(
        '{"task_id": "a", "proof": " trivial"}\n{"task_id": "b", "proof": " trivial"}\n',
        encoding="utf-8",
    )
    tasks, attempts = load_triplets(
        tmp_path / "nl.jsonl", tmp_path / "f.jsonl", tmp_path / "p.jsonl"
    )
    by_id = {t.task_id: t for t in tasks}
    assert by_id["a"].informal_statement == "first"
    assert by_id["b"].informal_statement == "second"
    assert len(attempts) == 2


def test_load_triplets_accepts_alternative_field_names(tmp_path):
    (tmp_path / "nl.jsonl").write_text('{"id": "a", "problem": "prose"}\n', encoding="utf-8")
    (tmp_path / "f.jsonl").write_text(
        '{"id": "a", "formal": "theorem a : True := by"}\n', encoding="utf-8"
    )
    (tmp_path / "p.jsonl").write_text('{"id": "a", "completion": " trivial"}\n', encoding="utf-8")
    tasks, attempts = load_triplets(
        tmp_path / "nl.jsonl", tmp_path / "f.jsonl", tmp_path / "p.jsonl"
    )
    assert tasks[0].informal_statement == "prose"
    assert attempts[0].proof == " trivial"


def test_load_triplets_collects_multiple_samples(tmp_path):
    (tmp_path / "nl.jsonl").write_text('{"task_id": "a", "problem": "x"}\n', encoding="utf-8")
    (tmp_path / "f.jsonl").write_text(
        '{"task_id": "a", "formal_statement": "theorem a : True := by"}\n', encoding="utf-8"
    )
    (tmp_path / "p.jsonl").write_text(
        '{"task_id": "a", "proof": " one"}\n{"task_id": "a", "proof": " two"}\n', encoding="utf-8"
    )
    _, attempts = load_triplets(tmp_path / "nl.jsonl", tmp_path / "f.jsonl", tmp_path / "p.jsonl")
    assert [a.sample_index for a in attempts] == [0, 1]


def test_load_triplets_rejects_a_missing_formalization(tmp_path):
    (tmp_path / "nl.jsonl").write_text(
        '{"task_id": "a", "problem": "x"}\n{"task_id": "b", "problem": "y"}\n', encoding="utf-8"
    )
    (tmp_path / "f.jsonl").write_text(
        '{"task_id": "a", "formal_statement": "theorem a : True := by"}\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="no formalization"):
        load_triplets(tmp_path / "nl.jsonl", tmp_path / "f.jsonl")


def test_load_triplets_works_without_prose_for_given_statements(tmp_path, capsys):
    """The statements-are-given case: audit them with no prose at all.

    On a fixed task set, a vacuous or trivially-true statement is the
    dataset's bug rather than the model's, and worth finding once over the
    task set instead of per attempt. That must not require inventing
    natural-language problems to sit next to it.
    """
    (tmp_path / "f.jsonl").write_text(
        '{"task_id": "a", "formal_statement": "theorem a (n : Nat) : n = n := by"}\n'
        '{"task_id": "b", "formal_statement": "theorem b : True := by"}\n',
        encoding="utf-8",
    )
    tasks, attempts = load_triplets(None, tmp_path / "f.jsonl")
    assert [t.task_id for t in tasks] == ["a", "b"]
    assert all(t.informal_statement == "" for t in tasks)
    assert attempts == []
    # No orphan warning: there is no prose by design, not by mistake.
    assert "no natural-language problem" not in capsys.readouterr().err


def test_structural_checks_still_run_without_prose():
    task = StatementTask(
        task_id="t", informal_statement="", formal_statement="theorem t : True := by sorry"
    )
    verdict = StatementChecker().check(task)
    assert verdict.status is StatementStatus.MALFORMED
    assert not verdict.checked_faithfulness


def test_statement_summary_warns_about_unassessed_faithfulness():
    verdicts = [StatementChecker().check(task())]
    assert "no faithfulness assessment" in format_statement_summary(verdicts)
