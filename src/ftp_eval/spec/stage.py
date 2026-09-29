"""Names and statuses shared by grading results."""
from enum import Enum

__all__ = ["StageId", "StageStatus"]

class StageId(str, Enum):
    ENVIRONMENT = "environment"
    INTERFACE = "interface"
    KERNEL = "kernel"
    REPLAY = "replay"
    DEPENDENCIES = "dependencies"
    AXIOMS = "axioms"
    REPORT = "report"

    @property
    def number(self) -> int:
        return {"environment": 0, "interface": 1, "kernel": 2, "replay": 3,
                "dependencies": 4, "axioms": 5, "report": 6}[self.value]

    @property
    def directory(self) -> str:
        return "%d-%s" % (self.number, self.value)

class StageStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"

    @property
    def conclusive(self) -> bool:
        return self is not StageStatus.NOT_RUN
