"""CLI behaviour, dataset I/O, registry, and the HTTP adapter's mapping."""

from __future__ import annotations

import json

import pytest

from ftp_eval import ProofAttempt, ProofTask, Status, available, create, load_tasks, write_jsonl
from ftp_eval.backends.axle import AxleVerifier
from ftp_eval.cli import EXIT_OK, EXIT_UNSOUND, main
from ftp_eval.verifier import VerifierError


@pytest.fixture
def fixture_files(tmp_path):
    tasks = [
        ProofTask(task_id="t1", header="import Mathlib", formal_statement="theorem t1 : True := by"),
        ProofTask(task_id="t2", header="import Mathlib", formal_statement="theorem t2 : True := by"),
    ]
    attempts = [
        ProofAttempt(task_id="t1", proof=" MOCK_PASS", model="demo"),
        ProofAttempt(task_id="t2", proof=" MOCK_FAIL", model="demo"),
    ]
    tp, ap = tmp_path / "tasks.jsonl", tmp_path / "attempts.jsonl"
    write_jsonl(tp, tasks)
    write_jsonl(ap, attempts)
    return tp, ap, tmp_path


# -- I/O --------------------------------------------------------------


def test_round_trip_through_jsonl(tmp_path):
    task = ProofTask(
        task_id="t1",
        formal_statement="theorem t1 : True := by",
        split="valid",
        metadata={"source": "minif2f"},
    )
    path = tmp_path / "tasks.jsonl"
    write_jsonl(path, [task])
    loaded = load_tasks(path)[0]
    assert loaded.task_id == task.task_id
    assert loaded.split == "valid"
    assert loaded.metadata["source"] == "minif2f"


def test_a_plain_json_array_is_accepted(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps([{"task_id": "t1", "formal_statement": "theorem t1 : True := by"}]),
        encoding="utf-8",
    )
    assert len(load_tasks(path)) == 1


def test_unknown_columns_are_preserved_in_metadata(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        json.dumps(
            {"task_id": "t1", "formal_statement": "theorem t1 : True := by", "difficulty": "hard"}
        )
        + "\n",
        encoding="utf-8",
    )
    assert load_tasks(path)[0].metadata["difficulty"] == "hard"


def test_a_bad_line_names_the_file_and_line_number(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text('{"task_id": "ok", "formal_statement": "x := by"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r":2: invalid JSON"):
        load_tasks(path)


def test_missing_task_id_is_rejected_loudly():
    with pytest.raises(ValueError, match="task_id"):
        ProofTask(task_id="", formal_statement="theorem t : True := by")


def test_continue_statement_needs_a_statement():
    with pytest.raises(ValueError, match="formal_statement"):
        ProofTask(task_id="t1", formal_statement="   ")


def test_attempt_id_defaults_to_task_and_sample():
    assert ProofAttempt(task_id="t1", proof="x", sample_index=3).attempt_id == "t1#3"


# -- registry ---------------------------------------------------------


def test_builtin_backends_are_discoverable():
    names = list(available())
    assert {"mock", "lean4", "axle"} <= set(names)


def test_unknown_backend_names_the_alternatives():
    with pytest.raises(KeyError, match="known backends"):
        create("nonexistent-prover")


def test_custom_backend_can_be_registered():
    from ftp_eval import RawVerdict, Verifier, register

    class AlwaysPasses(Verifier):
        name = "always"
        language = "lean4"

        def _verify(self, task, attempt, source, timeout_s):
            return RawVerdict.verified()

    register("always", AlwaysPasses, overwrite=True)
    assert create("always").verify(
        ProofTask(task_id="t", formal_statement="theorem t : True := by"),
        ProofAttempt(task_id="t", proof=" trivial"),
    ).status is Status.VERIFIED


# -- CLI --------------------------------------------------------------


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


def test_cli_verify_end_to_end(fixture_files, capsys):
    tasks, attempts, tmp = fixture_files
    out = tmp / "results.jsonl"
    code = main(
        [
            "verify", "-b", "mock",
            "--tasks", str(tasks), "--attempts", str(attempts),
            "--out", str(out), "--summary-out", str(tmp / "summary.json"),
            "--k", "1", "--quiet",
        ]
    )
    assert code == EXIT_OK
    assert "solve rate" in capsys.readouterr().out
    summary = json.loads((tmp / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["verified"] == 1
    assert summary["counts"]["failed"] == 1
    assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_cli_verify_strict_fails_on_an_unsound_pass(fixture_files, tmp_path):
    tasks, _, tmp = fixture_files
    cheating = tmp / "cheat.jsonl"
    write_jsonl(cheating, [ProofAttempt(task_id="t1", proof=" MOCK_PASS -- and\n sorry")])
    code = main(
        ["verify", "-b", "mock", "--tasks", str(tasks), "--attempts", str(cheating),
         "--quiet", "--strict"]
    )
    assert code == EXIT_UNSOUND


def test_cli_score_reads_a_results_file(fixture_files, capsys):
    tasks, attempts, tmp = fixture_files
    out = tmp / "results.jsonl"
    main(["verify", "-b", "mock", "--tasks", str(tasks), "--attempts", str(attempts),
          "--out", str(out), "--quiet"])
    capsys.readouterr()
    assert main(["score", str(out), "--k", "1", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["tasks"] == 2


def test_cli_preview_shows_the_assembled_source(fixture_files, capsys):
    tasks, attempts, _ = fixture_files
    assert main(["preview", "--tasks", str(tasks), "--attempts", str(attempts)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "import Mathlib" in out
    assert "theorem t1 : True := by MOCK_PASS" in out


def test_cli_backend_options_are_json_decoded(capsys):
    # -o pass_rate=1.0 must arrive as a float, not the string "1.0".
    assert main(["doctor", "-b", "mock", "-o", "pass_rate=1.0"]) == EXIT_OK


def test_cli_rejects_a_malformed_option():
    with pytest.raises(SystemExit):
        main(["doctor", "-b", "mock", "-o", "nonsense"])


# -- HTTP adapter -----------------------------------------------------


def test_http_backend_refuses_to_guess_an_unrecognized_response():
    # Mis-mapping a field would silently report failures for accepted
    # proofs, so an unknown shape must raise rather than default.
    backend = AxleVerifier(url="https://example.invalid/verify", api_key="k")
    with pytest.raises(VerifierError, match="response_map"):
        backend._interpret({"unexpected": True}, 200)


def test_http_backend_maps_a_boolean_ok_field():
    backend = AxleVerifier(url="https://example.invalid/verify", api_key="k")
    assert backend._interpret({"verified": True}, 200).status is Status.VERIFIED
    assert backend._interpret({"verified": False}, 200).status is Status.FAILED


def test_http_backend_maps_a_custom_field_name():
    backend = AxleVerifier(
        url="https://example.invalid/verify", api_key="k", response_map={"ok": "proved"}
    )
    assert backend._interpret({"proved": True}, 200).status is Status.VERIFIED


def test_http_backend_recognizes_a_remote_timeout():
    backend = AxleVerifier(url="https://example.invalid/verify", api_key="k")
    verdict = backend._interpret({"verified": False, "error": "verification timed out"}, 200)
    assert verdict.status is Status.TIMEOUT


def test_http_backend_parses_diagnostic_objects():
    backend = AxleVerifier(url="https://example.invalid/verify", api_key="k")
    verdict = backend._interpret(
        {"verified": False, "messages": [{"message": "unsolved goals", "line": 4}]}, 200
    )
    assert verdict.diagnostics[0].line == 4
    assert verdict.error_kind.value == "unsolved_goals"


def test_http_backend_is_unavailable_without_configuration():
    assert not AxleVerifier(url="", api_key="").info().available
    assert not AxleVerifier(url="https://example.invalid", api_key="").info().available
