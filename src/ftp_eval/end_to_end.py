"""End-to-end evaluation: check the formalization *and* the proof.

The combined verdict exists because neither half is sufficient on its own,
and because the obvious way to combine them is wrong.

A model evaluated end-to-end -- given prose, asked for a formal statement
and a proof -- has a trivial winning strategy if only the proof is
checked: formalize the problem as ``True`` and prove it with ``trivial``.
Every proof checks out, the score is 100%, and nothing was proved. So
:class:`EndToEndResult` requires *both* links: a faithful formalization
and a valid proof of it. A valid proof of an unfaithful statement scores
zero, and is reported under its own status so it can be told apart from
an honest failure to prove.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterator, Sequence

from .autoformalization.checker import StatementChecker
from .types import (
    ProofAttempt,
    StatementStatus,
    StatementTask,
    StatementVerdict,
    Status,
    VerificationResult,
)
from .proving.verifier import Verifier

__all__ = ["EndToEndStatus", "EndToEndResult", "EndToEndRunner"]


class EndToEndStatus(str, Enum):
    """Combined outcome over both links."""

    #: Faithful formalization and a valid proof. The only success.
    SOLVED = "solved"
    #: The proof is valid, but the statement it proves is not the problem.
    #: Scores zero, and is the failure worth looking at: it is what a
    #: proof-only harness would have counted as a win.
    PROVED_WRONG_STATEMENT = "proved_wrong_statement"
    #: Faithful formalization, no valid proof. An honest miss.
    UNPROVED = "unproved"
    #: Formalization is broken or degenerate, so the proof is moot.
    BAD_STATEMENT = "bad_statement"
    #: Proof was accepted but was reward hacking.
    HACKED_PROOF = "hacked_proof"
    #: Faithfulness was never assessed, so success cannot be claimed.
    UNVERIFIABLE = "unverifiable"
    #: The harness broke.
    ERROR = "error"

    @property
    def is_success(self) -> bool:
        return self is EndToEndStatus.SOLVED


@dataclass(frozen=True)
class EndToEndResult:
    """One task's combined statement and proof outcome."""

    task_id: str
    status: EndToEndStatus
    statement: StatementVerdict
    proof: VerificationResult | None
    wall_time_s: float = 0.0
    split: str | None = None

    @property
    def solved(self) -> bool:
        return self.status.is_success

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "solved": self.solved,
            "split": self.split,
            "wall_time_s": round(self.wall_time_s, 4),
            "statement": self.statement.to_dict(),
            "proof": self.proof.to_dict() if self.proof else None,
        }


def combine(
    statement: StatementVerdict, proof: VerificationResult | None
) -> EndToEndStatus:
    """Decide the combined status. The ordering encodes the priorities.

    A broken statement is reported before anything about the proof,
    because a proof of a malformed statement is not interesting. Reward
    hacking in the proof is reported before "unproved", because the two
    call for different responses. And a valid proof of an unfaithful
    statement gets its own status rather than being folded into
    "unproved", because that case is the one a proof-only harness scores
    as a win and is therefore the one worth counting.
    """
    if statement.status is StatementStatus.ERROR:
        return EndToEndStatus.ERROR
    if statement.status is StatementStatus.MALFORMED:
        return EndToEndStatus.BAD_STATEMENT

    proof_verified = proof is not None and proof.status is Status.VERIFIED
    if proof is not None and proof.status is Status.REJECTED:
        return EndToEndStatus.HACKED_PROOF
    if proof is not None and proof.status is Status.ERROR:
        return EndToEndStatus.ERROR

    if statement.status is StatementStatus.SUSPICIOUS:
        # A valid proof of a statement that is trivial, vacuous, or that
        # the judge read as not matching the problem.
        return (
            EndToEndStatus.PROVED_WRONG_STATEMENT
            if proof_verified
            else EndToEndStatus.BAD_STATEMENT
        )

    if not statement.checked_faithfulness:
        # Structural checks passed but nothing assessed meaning. Claiming
        # a solve here would be claiming something never checked.
        return EndToEndStatus.UNVERIFIABLE if proof_verified else EndToEndStatus.UNPROVED

    return EndToEndStatus.SOLVED if proof_verified else EndToEndStatus.UNPROVED


class EndToEndRunner:
    """Runs statement checking and proof verification over a task set."""

    def __init__(
        self,
        verifier: Verifier,
        checker: StatementChecker,
        *,
        timeout_s: float = 300.0,
    ) -> None:
        self.verifier = verifier
        self.checker = checker
        self.timeout_s = timeout_s

    def run(
        self,
        tasks: Sequence[StatementTask],
        attempts: Sequence[ProofAttempt],
        *,
        on_result: Any = None,
    ) -> list[EndToEndResult]:
        return list(self.iter_run(tasks, attempts, on_result=on_result))

    def iter_run(
        self,
        tasks: Sequence[StatementTask],
        attempts: Sequence[ProofAttempt],
        *,
        on_result: Any = None,
    ) -> Iterator[EndToEndResult]:
        """Yield one combined result per task, as each completes."""
        by_task: dict[str, list[ProofAttempt]] = {}
        for attempt in attempts:
            by_task.setdefault(attempt.task_id, []).append(attempt)

        for task in tasks:
            started = time.monotonic()
            statement = self.checker.check(task)

            proof: VerificationResult | None = None
            task_attempts = by_task.get(task.task_id, [])
            if task_attempts and statement.status is not StatementStatus.MALFORMED:
                # Verify samples until one is accepted; a single valid
                # proof is what the task asks for.
                proof_task = task.to_proof_task()
                for attempt in task_attempts:
                    proof = self.verifier.verify(
                        proof_task, attempt, timeout_s=self.timeout_s
                    )
                    if proof.status is Status.VERIFIED:
                        break

            result = EndToEndResult(
                task_id=task.task_id,
                status=combine(statement, proof),
                statement=statement,
                proof=proof,
                wall_time_s=time.monotonic() - started,
                split=task.split,
            )
            if on_result is not None:
                on_result(result)
            yield result


def summarize_end_to_end(results: Sequence[EndToEndResult]) -> dict[str, Any]:
    """Roll combined results up, keeping the two failure modes separate."""
    from collections import Counter

    statuses: Counter[str] = Counter(r.status.value for r in results)
    total = len(results)
    solved = statuses.get(EndToEndStatus.SOLVED.value, 0)
    return {
        "tasks": total,
        "solved": solved,
        "solve_rate": round(solved / total, 6) if total else 0.0,
        "statuses": dict(statuses),
        "proved_wrong_statement": statuses.get(
            EndToEndStatus.PROVED_WRONG_STATEMENT.value, 0
        ),
        "unverifiable": statuses.get(EndToEndStatus.UNVERIFIABLE.value, 0),
    }


def format_end_to_end(results: Sequence[EndToEndResult]) -> str:
    data = summarize_end_to_end(results)
    lines = [
        "end-to-end: %d task(s), %d solved (%.1f%%)"
        % (data["tasks"], data["solved"], 100 * data["solve_rate"]),
    ]
    labels = {
        EndToEndStatus.SOLVED.value: "faithful statement + valid proof",
        EndToEndStatus.PROVED_WRONG_STATEMENT.value: "valid proof of the WRONG statement",
        EndToEndStatus.UNPROVED.value: "faithful statement, not proved",
        EndToEndStatus.BAD_STATEMENT.value: "statement malformed or degenerate",
        EndToEndStatus.HACKED_PROOF.value: "proof was reward hacking",
        EndToEndStatus.UNVERIFIABLE.value: "proof valid, faithfulness never assessed",
        EndToEndStatus.ERROR.value: "harness error",
    }
    for status, count in sorted(data["statuses"].items(), key=lambda kv: -kv[1]):
        lines.append("  %-28s %4d   %s" % (status, count, labels.get(status, "")))
    if data["proved_wrong_statement"]:
        lines.append(
            "IMPORTANT: %d task(s) had a valid proof of an unfaithful statement. A "
            "proof-only harness would have scored every one of those as a success."
            % data["proved_wrong_statement"]
        )
    if data["unverifiable"]:
        lines.append(
            "NOTE: %d task(s) had a valid proof but no faithfulness assessment, so they "
            "are not counted as solved. Configure a judge to resolve them."
            % data["unverifiable"]
        )
    return "\n".join(lines)
