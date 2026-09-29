"""Frozen interfaces, health probes and execution boundaries stay aligned."""
import io
import tarfile

import pytest

from ftp_eval.backends.container import ContainerJob, DockerExecutor
from ftp_eval.backends.container import ContainerExecutor, ContainerResult
from ftp_eval.backends.lean4_docker import Lean4DockerVerifier
from ftp_eval.backends.types import ModuleBuild, StatementTask, Status
from ftp_eval.backends.lean4 import Lean4Verifier, parse_printed_axioms
from ftp_eval.backends.soundness import DEFAULT_ALLOWED_IMPORTS
from ftp_eval.backends.verifier import VerifierError
from ftp_eval.proving.grading.contest import ProblemSet, Submission, grade_answer, grade_contest
from ftp_eval.proving.grading.problem_health import HealthKind, check_problem_health, format_health_summary
from ftp_eval.proving.interface import InterfaceFault, check_interface, read_interface_problem, build_submission_modules
from ftp_eval.proving.interface import grade_interface
from dataclasses import replace
from ftp_eval.spec.stage import StageId
from ftp_eval.proving.grading.metadata import BenchmarkManifest, validate_environment
from ftp_eval.spec.artifacts import ArtifactWriter
from ftp_eval.spec.benchmark import Benchmark, BenchmarkProblem
from ftp_eval.backends.types import BackendInfo

PROBLEM = "namespace Problem\ndef Target (n : Nat) : Prop := n = 1\nend Problem\n"
ANSWER = "import FtpEvalBench.P001\nnamespace Submission\nvariable (k : Nat)\ntheorem solution : Problem.Target 1 := rfl\nend Submission\n"


def test_submission_variables_are_helpers_not_statement_tampering():
    assert check_interface(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"), ANSWER).ok


@pytest.mark.parametrize("signature", ["(n : Nat)", "{n : Nat}", "[Decidable True]"])
def test_solution_binders_refused_before_compilation(signature):
    answer = ANSWER.replace("solution :", "solution %s :" % signature)
    report = check_interface(read_interface_problem(PROBLEM), answer)
    assert InterfaceFault.SOLUTION_BINDERS in [fault for fault, _ in report.faults]


@pytest.mark.parametrize("target", ["theorem Target : True := trivial", "def Target : Nat := 1", "def Target := True"])
def test_target_must_define_a_proposition(target):
    with pytest.raises(ValueError, match="Prop"):
        read_interface_problem("namespace Problem\n%s\nend Problem\n" % target)


def test_import_refusal_names_the_correct_module():
    report = check_interface(read_interface_problem(PROBLEM, module="New.P001"), ANSWER, allowed_imports=("Mathlib",))
    assert "Import New.P001" in report.faults[0][1]


def test_gold_elaborates_before_submission_import():
    problem = replace(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"), gold_arguments=("2 + 2",))
    modules = build_submission_modules(problem, PROBLEM, ANSWER, "FtpSubmission.A", ("1",))
    assert [m.cacheable for m in modules] == [True, True, False, False]
    assert "Problem.Target (2 + 2)" in modules[1].source
    assert "FtpSubmission.A" not in modules[1].source
    assert "2 + 2" not in modules[-1].source
    assert "_root_.FtpEvalGoal." in modules[-1].source


def test_open_value_check_does_not_reparse_answer_expressions():
    problem = read_interface_problem(PROBLEM, module="FtpEvalBench.P001")
    modules = build_submission_modules(problem, PROBLEM, ANSWER, "FtpSubmission.A", ("my_helper",))
    assert "∃ ftp_value_0, @_root_.Problem.Target ftp_value_0" in modules[1].source
    assert "my_helper" not in modules[-1].source
    assert "⟨_, Submission.solution⟩" in modules[-1].source


def test_nested_problem_namespace_is_refused():
    answer = ANSWER.replace("variable (k : Nat)", "namespace Problem\ndef fake : Nat := 1\nend Problem")
    report = check_interface(read_interface_problem(PROBLEM), answer)
    assert InterfaceFault.PROBLEM_NAMESPACE_REUSED in [fault for fault, _ in report.faults]


@pytest.mark.parametrize("keyword", ["notation", "infix", "infixl", "infixr", "prefix", "postfix", "macro", "macro_rules", "syntax", "declare_syntax_cat", "elab", "elab_rules", "run_tac"])
def test_answers_cannot_extend_the_checker_language(keyword):
    report = check_interface(read_interface_problem(PROBLEM), ANSWER.replace("variable (k : Nat)", keyword + " fake"))
    assert InterfaceFault.SYNTAX_EXTENSION in [fault for fault, _ in report.faults]


def test_answer_cannot_change_imported_attributes():
    report = check_interface(read_interface_problem(PROBLEM), ANSWER.replace("variable (k : Nat)", "attribute [simp] Problem.Target"))
    assert InterfaceFault.IMPORTED_ATTRIBUTE in [fault for fault, _ in report.faults]


def test_answer_can_mark_its_own_helper_simp():
    answer = ANSWER.replace("variable (k : Nat)", "def helper : Nat := 1\nattribute [simp] helper")
    assert check_interface(read_interface_problem(PROBLEM), answer).ok


def test_axiom_listing_requires_exact_unique_name():
    clean = "'ftp_eval_target' does not depend on any axioms"
    assert parse_printed_axioms(clean, "ftp_eval_target") == []
    assert parse_printed_axioms(clean + "\n" + clean, "ftp_eval_target") is None
    assert parse_printed_axioms("'Submission.ftp_eval_target' depends on axioms: [propext]", "ftp_eval_target") is None


def test_default_submission_imports_exclude_lean_metaprogramming():
    report = check_interface(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"),
                             ANSWER.replace("import FtpEvalBench.P001", "import FtpEvalBench.P001\nimport Lean"),
                             allowed_imports=DEFAULT_ALLOWED_IMPORTS)
    assert InterfaceFault.IMPORT_NOT_ALLOWED in [fault for fault, _ in report.faults]


def test_statement_metrics_measure_the_target_body():
    grade = grade_answer(PROBLEM, ANSWER, problem_id="P001", participant="alice")
    assert grade.structure["statement_conclusion_tokens"] == 3
    assert grade.structure["statement_binders"] == 1


class HealthBackend:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def build_modules(self, modules, **kwargs):
        self.calls.append(modules)
        return ModuleBuild(Status.VERIFIED if len(modules) == 1 else self.outcome, axioms=())


def test_frozen_health_catches_universal_value_acceptance():
    backend = HealthBackend(Status.VERIFIED)
    health = check_problem_health(backend, StatementTask("P001", "", PROBLEM.replace("n = 1", "True")), module="FtpEvalBench.P001")
    assert health.ungradeable
    assert "∀ ftp_value_0, @Problem.Target ftp_value_0" in backend.calls[-1][-1].source
    assert "UNGRADEABLE" in format_health_summary([health])
    assert health.raw[HealthKind.NON_VACUOUS.value]["build"]["status"] == "verified"


def test_failed_health_search_does_not_prove_non_vacuity():
    health = check_problem_health(HealthBackend(Status.FAILED), StatementTask("P001", "", PROBLEM))
    check = next(c for c in health.checks if c.kind is HealthKind.NON_VACUOUS)
    assert check.passed is None and "does not establish" in check.detail


def test_frozen_health_unavailable_reports_capability_reason():
    class Unsupported:
        def build_modules(self, *args, **kwargs):
            return None
    health = check_problem_health(Unsupported(), StatementTask("P001", "", PROBLEM))
    assert not health.probed
    assert "cannot execute frozen-target module probes" in format_health_summary([health])


def test_module_capability_failure_aborts_before_answers(tmp_path):
    class Unsupported:
        def supports_module_builds(self):
            return False
        def build_modules(self, *args, **kwargs):
            raise AssertionError("must not grade an answer")
    with pytest.raises(ValueError, match="module"):
        grade_contest(ProblemSet({"P001": PROBLEM}), [Submission("alice", {"P001": ANSWER})],
                      verifier=Unsupported(), output_dir=tmp_path, run_id="r")
    assert (tmp_path / "r" / StageId.ENVIRONMENT.directory / "stage.json").exists()


def test_strict_environment_checks_all_pins_exactly():
    observed = {"available": True, "version": "Lean (version 4.29.0-rc2)",
                "lean_toolchain": "leanprover/lean4:v4.29.0-rc2", "mathlib_rev": "a" * 40,
                "lake_manifest_sha256": "b" * 64, "image_digest": "sha256:" + "c" * 64}
    manifest = BenchmarkManifest(lean="v4.29.0-rc2", mathlib_rev="a" * 40,
                                 raw={"toolchain": {"lake_manifest_sha256": "b" * 64, "image_digest": observed["image_digest"]}})
    assert validate_environment(manifest, observed) == []
    changed = {**observed, "mathlib_rev": "a" * 7, "lake_manifest_sha256": "d" * 64,
               "image_digest": "sha256:" + "e" * 64}
    errors = validate_environment(manifest, changed)
    assert len(errors) == 3


def test_public_benchmark_and_writer_contracts_drive_the_pipeline():
    class MemoryBenchmark(Benchmark):
        name = "memory"
        def problem_ids(self):
            return ("P001",)
        def problem(self, problem_id):
            return BenchmarkProblem(problem_id, PROBLEM)

    class MemoryWriter(ArtifactWriter):
        def __init__(self):
            self.events, self.answers, self.modules = [], [], []
        def write_manifest(self, **fields):
            self.manifest = fields
        def write_inputs(self, problems, submissions):
            self.inputs = dict(problems)
        def write_modules(self, answer):
            self.modules.append(dict(answer.modules))
        def write_stage(self, problem_id, participant, stage, event):
            self.events.append((stage, event["status"]))
        def write_run_stage(self, stage, status, payload=None):
            self.events.append((stage, status))
        def set_state(self, status, **fields):
            self.state = status
        def write_answers(self, graded):
            self.answers.extend(graded)
        def write_report(self, statistics, **fields):
            self.report = statistics

    writer = MemoryWriter()
    class Backend:
        def info(self):
            assert ("environment", "running") in writer.events
            assert writer.inputs == {"P001": PROBLEM}
            return BackendInfo("test", "lean4", True)
        def build_modules(self, modules, **kwargs):
            assert writer.modules
            assert ("kernel", "running") in writer.events
            return ModuleBuild(Status.VERIFIED, axioms=())
    result = grade_contest(MemoryBenchmark(), [Submission("alice", {"P001": ANSWER})],
                           verifier=Backend(), writer=writer)
    assert result.grades()[0].solved
    assert result.run_directory is None
    assert writer.state == "complete" and len(writer.answers) == 1
    assert writer.manifest["benchmark"].name == "memory"


def test_container_rejects_mutable_image_names():
    with pytest.raises(ValueError, match="pinned"):
        ContainerJob("ubuntu:latest", ("true",))


def test_container_output_transfer_rejects_symlinks():
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        member = tarfile.TarInfo("result.olean")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        tar.addfile(member)
    with pytest.raises(VerifierError, match="invalid"):
        DockerExecutor.read_outputs(archive.getvalue(), 100, {"olean": "/tmp/result.olean"})


def test_docker_module_builds_never_execute_native_lean(tmp_path, monkeypatch):
    class Worker(ContainerExecutor):
        def __init__(self):
            self.jobs = []
        def run(self, job):
            self.jobs.append(job)
            if job.command == ("cat", "/project/lean-toolchain"):
                return ContainerResult(0, stdout="leanprover/lean4:v4.29.0-rc2\n")
            if job.command == ("cat", "/project/lake-manifest.json"):
                return ContainerResult(0, stdout='{"packages": []}\n')
            if job.command[:2] == ("sh", "-c"):
                return ContainerResult(0, stdout='name = "worker"\n')
            assert job.command[:2] == ("lake", "env")
            if job.command[-1] == "--version":
                return ContainerResult(0, stdout="Lean (version 4.29.0-rc2)\n")
            if job.outputs:
                return ContainerResult(0, outputs={"olean": b"synthetic compiled module"})
            if job.command[2] == "leanchecker":
                return ContainerResult(0)
            return ContainerResult(0, stdout="'ftp_eval_target' does not depend on any axioms\n")
    worker = Worker()
    verifier = Lean4DockerVerifier(image="sha256:" + "a" * 64, executor=worker, workspace=str(tmp_path))
    monkeypatch.setattr("ftp_eval.backends.lean4.subprocess.run", lambda *args, **kwargs: pytest.fail("native subprocess must not execute a submission"))
    monkeypatch.setattr(Lean4Verifier, "_run_tool", lambda *args, **kwargs: pytest.fail("native tool runner must not be used"))
    monkeypatch.setattr(Lean4Verifier, "_lake_path", lambda *args, **kwargs: pytest.fail("host Lake must not be resolved"))
    problem = replace(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"), gold_arguments=("1",))
    verdict = grade_interface(problem, PROBLEM, ANSWER, verifier=verifier, answer_module="FtpSubmission.Test")
    assert verdict.solved
    assert any(job.command[:3] == ("lake", "env", "leanchecker") for job in worker.jobs)
    assert len([job for job in worker.jobs if job.outputs]) == 8  # four probes + four grading modules
    tools = [job for job in worker.jobs if job.command[:2] == ("lake", "env") and job.command[-1] != "--version"]
    assert all(job.mounts and all(name.startswith("/modules/") for name in job.mounts) for job in tools)
    assert verifier._module_capabilities == {"FtpEvalBench": True, "FtpSubmission": True}


def test_docker_command_has_only_read_only_host_mounts(tmp_path, monkeypatch):
    executor = DockerExecutor()
    monkeypatch.setattr(executor, "_binary", lambda: "docker")
    job = ContainerJob("sha256:" + "a" * 64, ("lake", "env", "lean"), mounts={"/modules/0": tmp_path})
    command = executor.command(job, "ftp-eval-test")
    assert "--read-only" in command and "--network=none" in command
    assert "--cap-drop=ALL" in command and "--security-opt=no-new-privileges" in command
    assert all(command[index + 1].endswith(",readonly") for index, value in enumerate(command) if value == "--mount")
