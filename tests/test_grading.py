"""Frozen benchmark grading and durable stage checkpoints."""
import json
from ftp_eval.spec.stage import StageId

import pytest

from ftp_eval import ContestPolicy, ModuleBuild, Status, create
from ftp_eval.backends.types import Diagnostic, Severity
from ftp_eval.proving.grading import ProblemSet, Submission, grade_contest, load_problem_set, load_submissions
from ftp_eval.proving.grading.metadata import BenchmarkManifest, parse_problem_metadata, reconcile_toolchain
from ftp_eval.proving.grading.results import Stage, StageStatus
from ftp_eval.proving.interface import InterfaceFault
from ftp_eval.spec import StageStatus as ContractStatus

PROBLEM = """/-!
- mathdb_id: 392323
- source: demo
- prose: Prove True.
-/
namespace Problem
def Target : Prop := True
end Problem
"""
GOOD = """import FtpEvalBench.P001
namespace Submission
def helper : Nat := 1
lemma supporting : True := by trivial
theorem solution : Problem.Target := supporting
end Submission
"""
HACKED = GOOD.replace("lemma supporting : True := by trivial", "axiom supporting : True")

class RecordingBackend:
    name = "recording"
    language = "lean4"

    def __init__(self, *, axioms=(), status=Status.VERIFIED, hook=None):
        self.axioms = axioms
        self.status = status
        self.calls = []
        self.hook = hook

    def build_modules(self, modules, **options):
        self.calls.append((modules, options))
        if self.hook:
            self.hook()
        return ModuleBuild(self.status, axioms=self.axioms, compile_time_s=0.1,
                           diagnostics=(Diagnostic(Severity.INFO, "checked"),), raw={"log": "checked"})

@pytest.fixture
def benchmark(tmp_path):
    problems = tmp_path / "problems"
    problems.mkdir()
    for pid in ("P001", "P002"):
        (problems / (pid + ".lean")).write_text(PROBLEM, encoding="utf-8")
    (tmp_path / "benchmark.json").write_text(json.dumps({"name": "test", "toolchain": {"lean": "v4.15.0", "mathlib_rev": "abc1234"}}), encoding="utf-8")
    for name, answer in (("alice", GOOD), ("bob", HACKED)):
        directory = tmp_path / "submissions" / name
        directory.mkdir(parents=True)
        (directory / "P001.lean").write_text(answer, encoding="utf-8")
    (tmp_path / "submissions/bob/P999.lean").write_text("-- misfiled", encoding="utf-8")
    return tmp_path

def load(root):
    problems = load_problem_set(root / "problems")
    return problems, load_submissions(root / "submissions", problems)

def one(backend=None, **options):
    return grade_contest(ProblemSet({"P001": PROBLEM}), [Submission("alice", {"P001": GOOD})], verifier=backend, **options)

def test_stage_status_has_one_definition():
    assert StageStatus is ContractStatus

def test_kernel_and_audit_are_both_required():
    assert one(RecordingBackend()).grades()[0].solved
    assert not one().grades()[0].solved
    assert not one(create("mock")).grades()[0].solved
    assert not one(RecordingBackend(axioms=None)).grades()[0].solved
    assert not one(RecordingBackend(axioms=("sorryAx",))).grades()[0].solved

def test_helpers_need_no_matching_declaration():
    backend = RecordingBackend()
    answer = one(backend).grades()[0]
    assert answer.solved and answer.report.helper_count == 2
    modules, options = backend.calls[0]
    assert modules[0].cacheable and not modules[1].cacheable
    assert "Submission.solution" in modules[2].source
    assert "supporting" not in modules[2].source
    assert options["audit_declaration"] == "ftp_eval_target"

def test_import_and_axiom_policies_are_enforced():
    manifest = BenchmarkManifest(raw={"policy": {"allowed_imports": [], "allowed_axioms": []}})
    answer = GOOD.replace("import FtpEvalBench.P001", "import FtpEvalBench.P001\nimport Mathlib")
    backend = RecordingBackend()
    result = grade_contest(ProblemSet({"P001": PROBLEM}, manifest=manifest), [Submission("a", {"P001": answer})], verifier=backend)
    assert result.grades()[0].failed_at is Stage.INTERFACE
    assert not backend.calls
    assert one(RecordingBackend(axioms=("Classical.choice",)), policy=ContestPolicy(allowed_axioms=frozenset())).grades()[0].failed_at is Stage.AXIOMS

def test_instance_policy_is_enforced():
    answer = GOOD.replace("def helper", "instance : Inhabited Nat := ⟨0⟩\ndef helper")
    result = grade_contest(ProblemSet({"P001": PROBLEM}), [Submission("a", {"P001": answer})], verifier=RecordingBackend(), policy=ContestPolicy(allow_global_instances=False))
    assert any(f is InterfaceFault.INSTANCE_NOT_ALLOWED for f, _ in result.grades()[0].report.faults)

def test_gold_arguments_are_checked_by_kernel_not_strings():
    problem = PROBLEM.replace("def Target : Prop := True", "def Target (n : Nat) : Prop := n = 1")
    answer = GOOD.replace("Problem.Target", "Problem.Target (0 + 1)")
    manifest = BenchmarkManifest(raw={"problems": {"P001": {"gold_arguments": ["1"]}}})
    backend = RecordingBackend()
    result = grade_contest(ProblemSet({"P001": problem}, manifest=manifest), [Submission("a", {"P001": answer})], verifier=backend)
    assert result.grades()[0].against_gold
    modules = backend.calls[0][0]
    assert "Problem.Target (1)" in modules[1].source and modules[1].cacheable
    assert "Submission.solution" in modules[-1].source
    assert "Problem.Target (1)" not in modules[-1].source

def test_old_problem_format_is_not_silently_accepted():
    with pytest.raises(ValueError, match="Problem.Target"):
        grade_contest(ProblemSet({"P001": "theorem t : True := by sorry"}), [Submission("a", {"P001": GOOD})])

def test_loading_preserves_metadata_and_stray_files(benchmark):
    problems, submissions = load(benchmark)
    assert problems.ids() == ("P001", "P002")
    assert problems.metadata["P001"].mathdb_id == "392323"
    assert problems.metadata["P001"].theorem_name == "Problem.Target"
    assert problems.manifest.lean == "v4.15.0"
    assert submissions[1].unrecognized == ("P999.lean",)

def test_statistics_include_refused_and_missing_answers(benchmark):
    result = grade_contest(*load(benchmark), verifier=RecordingBackend())
    assert result.participants[0].solved == 1
    assert result.participants[0].not_attempted == 1
    assert result.statistics.answers == 2
    assert result.statistics.refused_at == {"interface": 1}
    assert result.statistics.unattempted_problems == ("P002",)
    assert result.grades()[1].failure_mode == "reward_hacking"
    assert all(g.structure for g in result.grades())

def test_backend_error_does_not_abort_other_answers():
    class Broken(RecordingBackend):
        def build_modules(self, *args, **kwargs):
            raise RuntimeError("backend disconnected")
    result = grade_contest(ProblemSet({"P001": PROBLEM}), [Submission("a", {"P001": GOOD}), Submission("b", {"P001": GOOD})], verifier=Broken())
    assert len(result.grades()) == 2
    assert all(g.compile_status is StageStatus.NOT_RUN and not g.solved for g in result.grades())

@pytest.mark.parametrize("relative", [
    "run.json", "events.jsonl", "problems.json", "problems.tsv", "summary.json",
    "leaderboard.tsv", StageId.INTERFACE.directory + "/all.jsonl", StageId.INTERFACE.directory + "/refused.jsonl",
    StageId.KERNEL.directory + "/all.jsonl", StageId.AXIOMS.directory + "/all.jsonl", StageId.REPORT.directory + "/by-problem.tsv",
    "answers/alice/P001.json", StageId.INTERFACE.directory + "/by-answer/alice/P001.json",
])
def test_run_records_every_stage(benchmark, tmp_path, relative):
    result = grade_contest(*load(benchmark), verifier=RecordingBackend(), output_dir=tmp_path / "runs", run_id="r")
    assert (result.run_directory / relative).is_file()

def test_checkpoints_exist_before_backend_call(tmp_path):
    run = tmp_path / "runs/r"
    def inspect():
        assert json.loads((run / "run.json").read_text())["status"] == "running"
        assert json.loads((run / StageId.INTERFACE.directory / "by-answer/alice/P001.json").read_text())["status"] == "passed"
        assert json.loads((run / StageId.KERNEL.directory / "by-answer/alice/P001.json").read_text())["status"] == "running"
        assert len(list((run / StageId.KERNEL.directory / "modules/alice/P001").rglob("*.lean"))) == 3
    result = one(RecordingBackend(hook=inspect), output_dir=tmp_path / "runs", run_id="r")
    assert result.grades()[0].solved
    manifest = json.loads((run / "run.json").read_text())
    assert manifest["status"] == "complete" and manifest["schema_version"] == 3
    stages = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
    assert [(e["stage"], e["status"]) for e in stages if e["stage"] not in ("benchmark", "environment", "report")] == [
        ("interface", "running"), ("interface", "passed"),
        ("kernel", "running"), ("kernel", "passed"), ("replay", "not_run"), ("dependencies", "not_run"), ("axioms", "running"), ("axioms", "passed")]

def test_interruption_keeps_completed_answers_and_current_stage(tmp_path):
    backend = RecordingBackend()
    def stop():
        if len(backend.calls) == 2:
            raise KeyboardInterrupt()
    backend.hook = stop
    with pytest.raises(KeyboardInterrupt):
        grade_contest(ProblemSet({"P001": PROBLEM}), [Submission("a", {"P001": GOOD}), Submission("b", {"P001": GOOD})], verifier=backend, output_dir=tmp_path, run_id="r")
    run = tmp_path / "r"
    assert json.loads((run / "run.json").read_text())["status"] == "interrupted"
    assert json.loads((run / "answers/a/P001.json").read_text())["solved"]
    assert json.loads((run / StageId.KERNEL.directory / "by-answer/b/P001.json").read_text())["status"] == "running"

def test_run_ids_cannot_overwrite_previous_results(tmp_path):
    one(output_dir=tmp_path, run_id="r")
    with pytest.raises(FileExistsError):
        one(output_dir=tmp_path, run_id="r")

def test_manifest_records_provenance_policy_and_observed_toolchain(benchmark, tmp_path):
    result = grade_contest(*load(benchmark), output_dir=tmp_path / "runs", run_id="r")
    manifest = json.loads((result.run_directory / "run.json").read_text())
    assert manifest["toolchain"]["declared"]["lean"] == "v4.15.0"
    assert manifest["toolchain"]["observed"]["available"] is False
    assert not manifest["kernel_checked"]
    assert manifest["policy"]["isolate_builds"]
    problems = json.loads((result.run_directory / "problems.json").read_text())
    assert len(problems["P001"]["sha256"]) == 64

def test_unknown_policy_knobs_are_rejected():
    with pytest.raises(ValueError, match="unknown policy"):
        grade_contest(ProblemSet({}, manifest=BenchmarkManifest(raw={"policy": {"typo": True}})), [])


def test_run_metadata_is_saved(tmp_path):
    result = one(output_dir=tmp_path, run_metadata={"model": "model-v2", "seed": 42})
    manifest = json.loads((result.run_directory / "run.json").read_text())
    assert manifest["run_metadata"] == {"model": "model-v2", "seed": 42}
    assert (result.run_directory / "inputs/problems/P001.lean").read_text() == PROBLEM
    assert (result.run_directory / "inputs/submissions/alice/P001.lean").read_text() == GOOD


@pytest.mark.parametrize("run_id", ["..", "../escape", "a/b", "a\\b", "a."])
def test_run_id_stays_inside_output_root(tmp_path, run_id):
    with pytest.raises(ValueError, match="invalid run"):
        one(output_dir=tmp_path, run_id=run_id)

def test_metadata_and_toolchain_discrepancies_remain_visible():
    metadata = parse_problem_metadata("/-!\n- mathdb_id: 1\n- reviewer_initials: ab\n-/\ntheorem t : True := by sorry")
    assert metadata.extra["reviewer_initials"] == "ab"
    assert len(reconcile_toolchain(BenchmarkManifest(lean="v4.15.0", mathlib_rev="abc12345"), {"available": True, "lean_toolchain": "v4.9.0", "mathlib_rev": "ffffffff"})) == 2
