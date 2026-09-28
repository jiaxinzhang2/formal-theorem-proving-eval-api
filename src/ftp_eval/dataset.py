"""Reading tasks and attempts, writing results.

JSONL is the only format the core depends on: one object per line, so a
long run can be streamed and a crashed run can be resumed from whatever
made it to disk. A plain JSON array is also accepted on input, because
half the benchmarks in the wild ship that way.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence, TypeVar

from .types import ProofAttempt, ProofTask, VerificationResult

__all__ = [
    "read_jsonl",
    "write_jsonl",
    "load_tasks",
    "load_attempts",
    "load_results",
    "ResultWriter",
    "attempts_from_samples",
]

T = TypeVar("T")


def read_jsonl(path: str | os.PathLike[str]) -> Iterator[dict[str, Any]]:
    """Yield objects from a .jsonl file, or from a .json array."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(stripped)
        if not isinstance(data, list):
            raise ValueError("%s: top-level JSON must be an array of objects" % p)
        for i, obj in enumerate(data):
            if not isinstance(obj, dict):
                raise ValueError("%s: item %d is not an object" % (p, i))
            yield obj
        return
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("%s:%d: invalid JSON (%s)" % (p, lineno, exc.msg)) from exc
        if not isinstance(obj, dict):
            raise ValueError("%s:%d: expected an object" % (p, lineno))
        yield obj


def write_jsonl(path: str | os.PathLike[str], rows: Iterable[Any]) -> int:
    """Write ``rows`` (dicts or objects with ``to_dict``) as JSONL."""
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            payload = row.to_dict() if hasattr(row, "to_dict") else row
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            n += 1
    return n


def _load(
    path: str | os.PathLike[str],
    build: Callable[[dict[str, Any]], T],
    what: str,
) -> list[T]:
    out: list[T] = []
    for i, obj in enumerate(read_jsonl(path), start=1):
        try:
            out.append(build(obj))
        except Exception as exc:
            raise ValueError("%s: %s #%d is invalid: %s" % (path, what, i, exc)) from exc
    return out


def load_tasks(path: str | os.PathLike[str], *, language: str | None = None) -> list[ProofTask]:
    """Load tasks, optionally forcing a language onto records that omit it."""

    def build(obj: dict[str, Any]) -> ProofTask:
        if language and not obj.get("language"):
            obj = {**obj, "language": language}
        return ProofTask.from_dict(obj)

    return _load(path, build, "task")


def load_attempts(path: str | os.PathLike[str]) -> list[ProofAttempt]:
    return _load(path, ProofAttempt.from_dict, "attempt")


def load_results(path: str | os.PathLike[str]) -> list[VerificationResult]:
    return _load(path, VerificationResult.from_dict, "result")


class ResultWriter:
    """Append results to JSONL as they arrive, flushing each line.

    Verification is slow and often paid for by the minute, so results are
    durable the moment they exist rather than at the end of the run: kill
    the process at task 300 of 500 and you keep 300 results.
    """

    def __init__(self, path: str | os.PathLike[str] | None, *, append: bool = False) -> None:
        self.path = Path(path) if path is not None else None
        self._fh = None
        if self.path is not None:
            if self.path.parent and not self.path.parent.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a" if append else "w", encoding="utf-8", newline="\n")
        self.count = 0

    def write(self, result: VerificationResult) -> None:
        self.count += 1
        if self._fh is None:
            return
        self._fh.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "ResultWriter":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def attempts_from_samples(
    task_id: str,
    samples: Sequence[str],
    *,
    model: str | None = None,
) -> list[ProofAttempt]:
    """Turn k raw completions for one task into numbered attempts."""
    return [
        ProofAttempt(task_id=task_id, proof=text, model=model, sample_index=i)
        for i, text in enumerate(samples)
    ]


def already_done(path: str | os.PathLike[str]) -> set[str]:
    """Attempt ids present in an existing results file, for ``--resume``.

    A partially written final line (the process died mid-flush) is
    ignored rather than crashing the resume.
    """
    p = Path(path)
    if not p.exists():
        return set()
    done: set[str] = set()
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            attempt_id = obj.get("attempt_id")
            if isinstance(attempt_id, str):
                done.add(attempt_id)
    return done
