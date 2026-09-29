"""CLI discovery, diagnostics, auditing and benchmark runs."""
from __future__ import annotations

import json
import pytest
from ftp_eval.spec.stage import StageId
from ftp_eval.cli import EXIT_OK, EXIT_UNSOUND, EXIT_USAGE, main


@pytest.fixture
def benchmark(tmp_path, monkeypatch):
    """A two-problem benchmark with one honest participant and one cheat.

    The shape a real contest has: a folder of problems, a folder per
    participant. MOCK_PASS / MOCK_FAIL steer the mock prover.
    """
    # Keep the CLI's default run records inside the test's temporary workspace.
    monkeypatch.chdir(tmp_path)
    problem = """namespace Problem
-- %s
def Target : Prop := True
end Problem
"""
    answer = """import FtpEvalBench.%s
namespace Submission
theorem solution : Problem.Target := by
  %s
end Submission
"""

    problems = tmp_path / "problems"
    problems.mkdir()
    for pid in ("P001", "P002"):
        (problems / (pid + ".lean")).write_text(problem % pid, encoding="utf-8")

    honest = tmp_path / "submissions" / "honest"
    honest.mkdir(parents=True)
    (honest / "P001.lean").write_text(answer % ("P001", "MOCK_PASS"), encoding="utf-8")
    (honest / "P002.lean").write_text(answer % ("P002", "MOCK_FAIL"), encoding="utf-8")

    # Resubmitting the problem module does not export the required solution.
    cheat = tmp_path / "submissions" / "cheat"
    cheat.mkdir()
    (cheat / "P001.lean").write_text(problem % "P001", encoding="utf-8")

    return problems, tmp_path / "submissions", tmp_path


def test_cli_backends_lists_availability(capsys):
    assert main(["backends"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "mock" in out and "lean4" in out


def test_cli_backends_json_is_valid(capsys):
    assert main(["backends", "--json"]) == EXIT_OK
    assert isinstance(json.loads(capsys.readouterr().out), list)


def test_cli_doctor_smoke_passes_for_mock(capsys):
    assert main(["doctor", "-b", "mock", "--smoke"]) == EXIT_OK
    assert "available: yes" in capsys.readouterr().out


def test_cli_doctor_reports_an_unavailable_backend():
    # No URL and no credential configured.
    assert main(["doctor", "-b", "axle"]) != EXIT_OK


def test_cli_grade_end_to_end(benchmark, capsys):
    problems, submissions, tmp = benchmark
    results = tmp / "results"
    code = main(
        ["grade", "-b", "mock", "--problems", str(problems),
         "--submissions", str(submissions), "--out", str(results), "--quiet"]
    )
    assert code == EXIT_OK
    assert "participant" in capsys.readouterr().out

    # Every stage left its record behind, under the names the artifact
    # layout promises.
    run = next(results.iterdir())
    assert (run / "run.json").exists()
    assert (run / StageId.INTERFACE.directory / "all.jsonl").exists()
    assert (run / StageId.REPORT.directory / "by-problem.tsv").exists()
    assert (run / "leaderboard.tsv").exists()


def test_cli_grade_strict_fails_on_a_refused_answer(benchmark):
    problems, submissions, _ = benchmark
    code = main(
        ["grade", "-b", "mock", "--problems", str(problems),
         "--submissions", str(submissions), "--quiet", "--strict"]
    )
    assert code == EXIT_UNSOUND


def test_cli_grade_rejects_an_empty_problem_folder(tmp_path):
    (tmp_path / "problems").mkdir()
    (tmp_path / "subs").mkdir()
    code = main(
        ["grade", "--problems", str(tmp_path / "problems"),
         "--submissions", str(tmp_path / "subs"), "--quiet"]
    )
    assert code == EXIT_USAGE


def test_cli_audit_checks_the_problem_set_itself(benchmark, capsys):
    problems, _, _ = benchmark
    assert main(["audit", "--problems", str(problems), "--quiet"]) == EXIT_OK
    out = capsys.readouterr().out
    # Neither problem records provenance or prose, and audit reports both
    # gaps rather than giving the set a clean bill of health.
    assert "no MathDB id" in out
    assert "prose:" in out


def test_cli_backend_options_are_json_decoded(capsys):
    # -o pass_rate=1.0 must arrive as a float, not the string "1.0".
    assert main(["doctor", "-b", "mock", "-o", "pass_rate=1.0"]) == EXIT_OK


def test_cli_rejects_a_malformed_option(capsys):
    # main() reports through its exit code rather than an exception, so a
    # bad -o is a usage error like any other, not a traceback.
    assert main(["doctor", "-b", "mock", "-o", "nonsense"]) == EXIT_USAGE
    assert "nonsense" in capsys.readouterr().err


def test_cli_grade_strict_rejects_unverified_answers(benchmark):
    problems, submissions, _ = benchmark
    assert main(["grade", "--problems", str(problems), "--submissions", str(submissions), "--quiet", "--strict"]) == EXIT_UNSOUND


def test_removed_match_command_is_not_advertised():
    from ftp_eval.cli import build_parser
    assert "match" not in build_parser().format_help()


def test_cli_saves_runs_by_default():
    from ftp_eval.cli import build_parser
    args = build_parser().parse_args(["grade", "--problems", "P", "--submissions", "S"])
    assert args.out == "runs"
