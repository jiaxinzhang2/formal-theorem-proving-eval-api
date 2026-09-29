"""Durable run inputs, per-stage checkpoints and final reports.

See docs/runs.md for the schema-v3 directory layout. Sources are saved before
backend calls, events are flushed, and JSON snapshots are replaced atomically."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ...spec.artifacts import ArtifactWriter
from ...spec.stage import StageId
from .results import GradedAnswer
from .summary import ContestStatistics

__all__ = ["RunDirectory", "default_run_id"]


def default_run_id() -> str:
    """A UTC timestamp that sorts chronologically and is filename-safe."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]


def _safe(component: str) -> str:
    """Reject path components that could escape or collide after normalization."""
    if (not component or component in (".", "..") or component != component.strip()
            or component.endswith(".")
            or any(ch in '<>:"/\\|?*' or ord(ch) < 32 for ch in component)):
        raise ValueError("invalid run, participant or problem id: %r" % component)
    return component


def _tsv(rows: Iterable[Sequence[Any]]) -> str:
    return "\n".join("\t".join("" if v is None else str(v) for v in row) for row in rows) + "\n"


@dataclass
class RunDirectory(ArtifactWriter):
    """Writes one grading run's results."""

    root: Path

    @property
    def directory(self) -> Path:
        return self.root

    @classmethod
    def create(
        cls, base: str | os.PathLike[str], run_id: str | None = None
    ) -> "RunDirectory":
        root = Path(base) / _safe(run_id or default_run_id())
        root.mkdir(parents=True, exist_ok=False)
        for sub in (*[s.directory for s in StageId], StageId.KERNEL.directory + "/logs", "answers"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        writer = cls(root=root)
        for relative in ("events.jsonl", "1-interface/refused.jsonl",
                         *[s.directory + "/all.jsonl" for s in StageId if s not in (StageId.ENVIRONMENT, StageId.REPORT)]):
            writer._text(relative, "")
        return writer

    # -- manifest ------------------------------------------------------

    def write_manifest(
        self,
        *,
        problems: Mapping[str, str],
        participants: Sequence[str],
        backend: str | None,
        judge: str | None = None,
        problem_metadata: Mapping[str, Any] | None = None,
        benchmark: Any = None,
        observed_toolchain: Mapping[str, Any] | None = None,
        toolchain_warnings: Sequence[str] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """What was graded, with what, when.

        Three parts carry the reproducibility weight:

        * **Problem hashes**, which turn "which version of the problem set
          was this?" from a memory into a fact.
        * **Declared and observed toolchains, side by side.** The benchmark
          says which Lean and Mathlib it was built against; the grader
          reports what it actually ran. A result produced against a
          different Mathlib is not reproducible, and recording only the
          claim would hide that.
        * **Per-problem provenance**, including the MathDB number, so a
          disputed problem can be traced to its source.
        """
        from ... import __version__

        manifest: dict[str, Any] = {
            "run_id": self.root.name,
            "graded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ftp_eval_version": __version__,
            "backend": backend,
            "judge": judge,
            "kernel_checked": backend is not None,
            "problems": len(problems),
            "participants": list(participants),
            "benchmark": benchmark.to_dict() if benchmark is not None else None,
            "toolchain": {
                "declared": (
                    benchmark.to_dict().get("declared_toolchain")
                    if benchmark is not None
                    else None
                ),
                "observed": dict(observed_toolchain or {}),
                "warnings": list(toolchain_warnings),
            },
            **dict(extra or {}),
        }
        self._json("run.json", manifest)

        metadata = problem_metadata or {}
        self._json(
            "problems.json",
            {
                pid: {
                    "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                    "bytes": len(source.encode("utf-8")),
                    **(
                        metadata[pid].to_dict()
                        if pid in metadata and hasattr(metadata[pid], "to_dict")
                        else {}
                    ),
                }
                for pid, source in sorted(problems.items())
            },
        )
        self._text(
            "problems.tsv",
            _tsv(
                [
                    (
                        "problem_id",
                        "sha256",
                        "bytes",
                        "mathdb_id",
                        "theorem",
                        "answer_holes",
                        "difficulty",
                        "source",
                    )
                ]
                + [
                    (
                        pid,
                        hashlib.sha256(source.encode("utf-8")).hexdigest()[:16],
                        len(source.encode("utf-8")),
                        getattr(metadata.get(pid), "mathdb_id", ""),
                        getattr(metadata.get(pid), "theorem_name", ""),
                        getattr(metadata.get(pid), "answer_holes", ""),
                        getattr(metadata.get(pid), "difficulty", ""),
                        getattr(metadata.get(pid), "source", ""),
                    )
                    for pid, source in sorted(problems.items())
                ]
            ),
        )

    # -- per-stage -----------------------------------------------------

    def write_inputs(self, problems: Mapping[str, str], submissions: Sequence[Any]) -> None:
        for pid, source in problems.items():
            self._text("inputs/problems/%s.lean" % _safe(pid), source)
        for submission in submissions:
            for pid, source in submission.answers.items():
                self._text("inputs/submissions/%s/%s.lean" % (_safe(submission.participant), _safe(pid)), source)
        self.write_run_stage("benchmark", "passed", {"problems": len(problems), "participants": len(submissions)})

    def write_run_stage(self, stage: str, status: str, payload: Any = None) -> None:
        record = {"stage": stage, "status": status, "payload": payload,
                  "recorded_at": datetime.now(timezone.utc).isoformat()}
        directory = StageId(stage).directory if stage != "benchmark" else "inputs"
        self._json(directory + "/stage.json", record)
        self._append("events.jsonl", record)

    def write_answers(self, graded: Sequence[GradedAnswer]) -> None:
        """Per-answer records and separate interface, kernel and audit streams."""
        for answer in graded:
            self._json("answers/%s/%s.json" % (_safe(answer.participant or "anonymous"), _safe(answer.problem_id)), answer.to_dict())

    def write_stage(self, problem_id: str, participant: str, stage: str, event: Mapping[str, Any]) -> None:
        """Checkpoint before advancing; append history and replace the latest snapshot."""
        directory = StageId(stage).directory
        record = {"problem_id": problem_id, "participant": participant,
                  "recorded_at": datetime.now(timezone.utc).isoformat(), **event}
        self._json("%s/by-answer/%s/%s.json" % (directory, _safe(participant or "anonymous"), _safe(problem_id)), record)
        self._append("events.jsonl", record)
        if event["status"] != "running":
            self._append(directory + "/all.jsonl", record)
            if stage == "interface" and event["status"] == "failed":
                self._append(directory + "/refused.jsonl", record)

    def _append(self, relative: str, record: Mapping[str, Any]) -> None:
        with self._path(relative).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def set_state(self, status: str, **fields: Any) -> None:
        manifest = json.loads((self.root / "run.json").read_text(encoding="utf-8"))
        manifest.update(status=status, updated_at=datetime.now(timezone.utc).isoformat(), **fields)
        self._json("run.json", manifest)

    def write_modules(self, answer: GradedAnswer) -> None:
        """Preserve all exact module sources, even for refused answers."""
        stem = "%s/%s" % (_safe(answer.participant or "anonymous"), _safe(answer.problem_id))
        for module, source in answer.modules.items():
            self._text(StageId.KERNEL.directory + "/modules/%s/%s.lean" % (stem, module.replace(".", "/")), source)
        if answer.build:
            log = str(answer.build.raw.get("log", ""))
            if log:
                self._text(StageId.KERNEL.directory + "/logs/%s.log" % stem, log)

    def write_report(
        self,
        statistics: ContestStatistics,
        *,
        leaderboard: Sequence[Mapping[str, Any]],
        graded: Sequence[GradedAnswer],
    ) -> None:
        """Aggregate reporting, in both machine and human form."""
        self._json("summary.json", statistics.to_dict())
        self._text("summary.txt", statistics.format_text() + "\n")

        self._text(
            "leaderboard.tsv",
            _tsv(
                [("rank", "participant", "solved", "attempted", "rejected", "not_attempted")]
                + [
                    (
                        index,
                        row.get("participant"),
                        row.get("solved"),
                        row.get("attempted"),
                        row.get("rejected"),
                        row.get("not_attempted"),
                    )
                    for index, row in enumerate(leaderboard, start=1)
                ]
            ),
        )
        self._text(
            StageId.REPORT.directory + "/by-problem.tsv",
            _tsv(
                [("problem_id", "attempted", "solved", "solve_rate", "refused_interface", "refused_kernel", "refused_replay", "refused_dependencies", "refused_axioms")]
                + [
                    (
                        p.problem_id,
                        p.attempted,
                        p.solved,
                        "%.3f" % p.solve_rate,
                        p.refused_at_interface,
                        p.refused_at_kernel,
                        p.refused_at_replay,
                        p.refused_at_dependencies,
                        p.refused_at_axioms,
                    )
                    for p in statistics.by_problem
                ]
            ),
        )
        self._text(
            StageId.REPORT.directory + "/by-participant.tsv",
            _tsv(
                [("participant", "solved", "attempted", "rejected")]
                + [
                    (r.get("participant"), r.get("solved"), r.get("attempted"), r.get("rejected"))
                    for r in leaderboard
                ]
            ),
        )
        self._text(
            StageId.REPORT.directory + "/reasons.tsv",
            _tsv(
                [("count", "stage", "reason")]
                + [
                    (count, "", reason)
                    for reason, count in sorted(
                        statistics.reasons.items(), key=lambda kv: -kv[1]
                    )
                ]
            ),
        )

    # -- plumbing ------------------------------------------------------

    def _path(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _json(self, relative: str, payload: Any) -> None:
        path = self._path(relative)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def _jsonl(self, relative: str, rows: Iterable[Mapping[str, Any]]) -> None:
        with self._path(relative).open("w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _text(self, relative: str, content: str) -> None:
        self._path(relative).write_text(content, encoding="utf-8", newline="\n")
