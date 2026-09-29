"""The results folder: every stage's conclusion, on disk.

A grading run produces one directory, and the layout is the point -- a
disputed result has to be answerable from it months later without re-running
anything::

    results/2026-09-29T0312Z/
      run.json                     what was graded, with what, when
      problems.tsv                 problem ids + content hashes
      leaderboard.tsv              the ranking, scannable
      summary.txt                  stage 3 in prose
      summary.json                 stage 3 as data

      1-match/
        all.jsonl                  every stage-1 verdict
        refused.jsonl              only the refusals, for review
      2-compile/
        all.jsonl                  every stage-2 verdict
        probes/<participant>/<problem>.lean    the exact source compiled
        logs/<participant>/<problem>.log       what the prover said
      3-report/
        by-problem.tsv             solve counts, difficulty signal
        by-participant.tsv         per-participant totals
        reasons.tsv                why answers were refused
      answers/
        <participant>/<problem>.json    all three stages for one answer

Naming rules, so the folder stays predictable:

* **Stage directories are numbered.** ``1-match``, ``2-compile``,
  ``3-report`` -- reading order is the pipeline order.
* **``.jsonl`` streams, ``.json`` single objects, ``.tsv`` for anything a
  human or a spreadsheet will open.** No format is used for two purposes.
* **Participant and problem ids are path components, never concatenated
  into filenames.** ``alice/P001.json`` sorts and globs; ``alice_P001``
  breaks the moment an id contains an underscore.
* **The probe sources are kept.** A verdict nobody can reproduce is a
  verdict nobody can appeal, so the exact text handed to the prover is on
  disk next to what it said.
* **Problem hashes are recorded.** Which version of the problem set was
  graded is then a fact, not a memory.
* **The run id is a UTC timestamp** (``2026-09-29T0312Z``), so runs sort
  chronologically and never collide. ``--run-id`` overrides it.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .stages import GradedAnswer
from .statistics import ContestStatistics

__all__ = ["RunDirectory", "default_run_id"]


def default_run_id() -> str:
    """A UTC timestamp that sorts chronologically and is filename-safe."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")


def _safe(component: str) -> str:
    """Make an id safe as a single path component, without collapsing ids.

    Only characters that cannot appear in a path are replaced, so two
    distinct ids cannot become the same directory.
    """
    out = "".join("-" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in component)
    return out.strip() or "unnamed"


def _tsv(rows: Iterable[Sequence[Any]]) -> str:
    return "\n".join("\t".join("" if v is None else str(v) for v in row) for row in rows) + "\n"


@dataclass
class RunDirectory:
    """Writes one grading run's results."""

    root: Path

    @classmethod
    def create(
        cls, base: str | os.PathLike[str], run_id: str | None = None
    ) -> "RunDirectory":
        root = Path(base) / (run_id or default_run_id())
        for sub in ("1-match", "2-compile/probes", "2-compile/logs", "3-report", "answers"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        return cls(root=root)

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

    def write_answers(self, graded: Sequence[GradedAnswer]) -> None:
        """Every stage's conclusion, per answer and in per-stage streams."""
        match_rows: list[dict[str, Any]] = []
        refused_rows: list[dict[str, Any]] = []
        compile_rows: list[dict[str, Any]] = []

        for answer in graded:
            record = answer.to_dict()
            participant = _safe(answer.participant or "anonymous")
            problem = _safe(answer.problem_id)
            self._json("answers/%s/%s.json" % (participant, problem), record)

            if answer.match is not None:
                row = {
                    "participant": answer.participant,
                    "problem_id": answer.problem_id,
                    **answer.match.to_dict(),
                }
                match_rows.append(row)
                if not answer.match.status.answers_the_problem:
                    refused_rows.append(row)

            compile_rows.append(
                {
                    "participant": answer.participant,
                    "problem_id": answer.problem_id,
                    "status": answer.compile_status.value,
                    "detail": answer.compile_detail,
                    "compile_time_s": answer.compile_time_s,
                }
            )

        self._jsonl("1-match/all.jsonl", match_rows)
        self._jsonl("1-match/refused.jsonl", refused_rows)
        self._jsonl("2-compile/all.jsonl", compile_rows)

    def write_probe(self, answer: GradedAnswer, source: str, log: str = "") -> None:
        """Keep the exact text compiled, and what the prover said about it.

        A verdict nobody can reproduce is a verdict nobody can appeal.
        """
        participant = _safe(answer.participant or "anonymous")
        problem = _safe(answer.problem_id)
        if source:
            self._text("2-compile/probes/%s/%s.lean" % (participant, problem), source)
        if log:
            self._text("2-compile/logs/%s/%s.log" % (participant, problem), log)

    def write_report(
        self,
        statistics: ContestStatistics,
        *,
        leaderboard: Sequence[Mapping[str, Any]],
        graded: Sequence[GradedAnswer],
    ) -> None:
        """Stage 3, in both machine and human form."""
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
            "3-report/by-problem.tsv",
            _tsv(
                [("problem_id", "attempted", "solved", "solve_rate", "refused_match", "refused_compile")]
                + [
                    (
                        p.problem_id,
                        p.attempted,
                        p.solved,
                        "%.3f" % p.solve_rate,
                        p.refused_at_match,
                        p.refused_at_compile,
                    )
                    for p in statistics.by_problem
                ]
            ),
        )
        self._text(
            "3-report/by-participant.tsv",
            _tsv(
                [("participant", "solved", "attempted", "rejected")]
                + [
                    (r.get("participant"), r.get("solved"), r.get("attempted"), r.get("rejected"))
                    for r in leaderboard
                ]
            ),
        )
        self._text(
            "3-report/reasons.tsv",
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
        self._path(relative).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def _jsonl(self, relative: str, rows: Iterable[Mapping[str, Any]]) -> None:
        with self._path(relative).open("w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _text(self, relative: str, content: str) -> None:
        self._path(relative).write_text(content, encoding="utf-8", newline="\n")
