"""Load benchmark folders and participant answers; snapshot the Benchmark contract."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from ...spec.benchmark import Benchmark, BenchmarkProblem
from .manifest import BenchmarkManifest, load_benchmark_manifest
from .provenance import ProblemMetadata, parse_problem_metadata


#: Files in a submission directory that are not answers.
_IGNORED = {".gitkeep", ".ds_store", "readme.md", "readme.txt", "notes.md"}


@dataclass(frozen=True)
class ProblemSet(Benchmark):
    """The setter's benchmark: a folder of Lean files, one frozen Problem.Target each."""

    problems: Mapping[str, str]
    root: Path | None = None
    #: Per-problem provenance, read from each file's metadata block.
    metadata: Mapping[str, ProblemMetadata] = field(default_factory=dict)
    #: Benchmark-wide declaration from ``benchmark.json``, when present.
    manifest: BenchmarkManifest = field(default_factory=BenchmarkManifest)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.problems))

    # -- the Benchmark contract ----------------------------------------

    @property
    def name(self) -> str:
        return self.manifest.name or (self.root.name if self.root else "")

    def problem_ids(self) -> tuple[str, ...]:
        return self.ids()

    def problem(self, problem_id: str) -> BenchmarkProblem:
        """One problem, with everything needed to cite it later.

        The content hash comes from the source, so a published result ties
        to the exact text that was graded rather than to a filename that
        may have been edited since.
        """
        entry = self.metadata.get(problem_id)
        return BenchmarkProblem(
            problem_id=problem_id,
            source=self.problems[problem_id],
            target="Problem.Target",
            metadata=entry,
            origin=str(self.root / ("%s.lean" % problem_id)) if self.root else "",
        )

    def manifest_fields(self) -> Mapping[str, Any]:
        return {**self.manifest.raw, "name": self.manifest.name,
                "version": self.manifest.version, "description": self.manifest.description,
                "language": self.manifest.language,
                "toolchain": {**self.manifest.raw.get("toolchain", {}),
                              "lean": self.manifest.lean, "mathlib_rev": self.manifest.mathlib_rev}}

    def source_for(self, problem_id: str) -> str:
        """The problem file's Lean source."""
        return self.problems[problem_id]

    def prose_for(self, problem_id: str) -> str:
        """The problem's natural-language statement, or "" if it records none.

        Empty is a normal answer, not an error: grading never needs prose.
        Only ``ftp-eval audit`` does, and it reports which problems it could
        not judge rather than failing on them.
        """
        entry = self.metadata.get(problem_id)
        return entry.prose if entry else ""

    def without_provenance(self) -> tuple[str, ...]:
        """Problems recording neither a MathDB id nor a source.

        Reported rather than rejected: an unsourced problem still grades.
        But for a published contest it is worth knowing which ones cannot
        be traced back.
        """
        return tuple(
            pid
            for pid in self.ids()
            if not (pid in self.metadata and self.metadata[pid].has_provenance)
        )

    def __len__(self) -> int:
        return len(self.problems)


@dataclass(frozen=True)
class Submission:
    """One participant's answers, keyed by problem id."""

    participant: str
    answers: Mapping[str, str]
    #: Files matching no problem id. Reported, never dropped.
    unrecognized: tuple[str, ...] = ()
    root: Path | None = None


def load_problem_set(root: str | os.PathLike[str], *, suffix: str = ".lean") -> ProblemSet:
    """Load the benchmark. The file stem is the problem id."""
    directory = Path(root)
    if not directory.is_dir():
        raise NotADirectoryError("problem directory not found: %s" % directory)
    problems = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(directory.glob("*%s" % suffix))
    }
    if not problems:
        raise ValueError("no %s files in %s" % (suffix, directory))
    metadata = {
        pid: parse_problem_metadata(source, problem_id=pid)
        for pid, source in problems.items()
    }
    # `benchmark.json` sits beside the problems directory or inside it.
    manifest = load_benchmark_manifest(directory)
    if not manifest.name:
        manifest = load_benchmark_manifest(directory.parent)
    return ProblemSet(
        problems=problems, root=directory, metadata=metadata, manifest=manifest
    )


def load_submissions(
    root: str | os.PathLike[str], problem_set: ProblemSet, *, suffix: str = ".lean"
) -> list[Submission]:
    """Load one directory per participant.

    A file whose stem is not a problem id is recorded as unrecognized
    rather than dropped: a participant who misnames a file should be told,
    not silently scored zero on work they did.
    """
    directory = Path(root)
    if not directory.is_dir():
        raise NotADirectoryError("submission directory not found: %s" % directory)

    out: list[Submission] = []
    for participant_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        answers: dict[str, str] = {}
        unrecognized: list[str] = []
        for path in sorted(participant_dir.iterdir()):
            if path.is_dir() or path.name.lower() in _IGNORED:
                continue
            if path.suffix == suffix and path.stem in problem_set.problems:
                answers[path.stem] = path.read_text(encoding="utf-8")
            else:
                unrecognized.append(path.name)
        out.append(
            Submission(
                participant=participant_dir.name,
                answers=answers,
                unrecognized=tuple(unrecognized),
                root=participant_dir,
            )
        )
    return out


def snapshot_benchmark(benchmark: Benchmark) -> ProblemSet:
    """Read the public Benchmark contract once; freeze a run's inputs in memory."""
    fields = dict(benchmark.manifest_fields())
    toolchain = fields.get("toolchain", {})
    if not isinstance(toolchain, dict):
        raise ValueError("benchmark toolchain must be an object")
    manifest = BenchmarkManifest(name=str(fields.get("name") or benchmark.name),
                                 version=str(fields.get("version") or ""),
                                 description=str(fields.get("description") or ""),
                                 language=str(fields.get("language") or "lean4"),
                                 lean=str(toolchain.get("lean") or ""),
                                 mathlib_rev=str(toolchain.get("mathlib_rev") or ""), raw=fields)
    problems, metadata = {}, {}
    for item in benchmark.iter_problems():
        if item.problem_id in problems:
            raise ValueError("duplicate problem id: %s" % item.problem_id)
        if item.target != "Problem.Target":
            raise ValueError("benchmark must export the frozen Problem.Target interface")
        problems[item.problem_id] = item.source
        metadata[item.problem_id] = item.metadata if isinstance(item.metadata, ProblemMetadata) else parse_problem_metadata(item.source, problem_id=item.problem_id)
    return ProblemSet(problems, metadata=metadata, manifest=manifest)
