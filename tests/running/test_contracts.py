"""The Benchmark and ArtifactWriter contracts drive the real evaluation pipeline."""
from __future__ import annotations

from ftp_eval.backends.types import BackendInfo, ModuleBuild, Status
from ftp_eval.proving.running import Submission, grade_contest
from ftp_eval.spec.artifacts import ArtifactWriter
from ftp_eval.spec.benchmark import Benchmark, BenchmarkProblem
from ftp_eval.proving.checking.interface import InterfaceVerdict
from ftp_eval.proving.running.results import GradedAnswer


PROBLEM = "namespace Problem\ndef Target (n : Nat) : Prop := n = 1\nend Problem\n"


def test_run_records_use_the_checkers_acceptance_rule():
    assert issubclass(GradedAnswer, InterfaceVerdict)
    assert GradedAnswer.solved is InterfaceVerdict.solved


ANSWER = "import FtpEvalBench.P001\nnamespace Submission\nvariable (k : Nat)\ntheorem solution : Problem.Target 1 := rfl\nend Submission\n"


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
