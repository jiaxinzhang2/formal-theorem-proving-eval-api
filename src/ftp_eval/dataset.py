"""Reading tasks and attempts, writing results.

JSONL is the only format the core depends on: one object per line, so a
long run can be streamed and a crashed run can be resumed from whatever
made it to disk. A plain JSON array is also accepted on input, because
half the benchmarks in the wild ship that way.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence, TypeVar

from .types import ProofAttempt, ProofTask, StatementTask, VerificationResult

__all__ = [
    "read_jsonl",
    "write_jsonl",
    "load_tasks",
    "load_attempts",
    "load_results",
    "load_triplets",
    "ResultWriter",
    "already_done",
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


def load_triplets(
    informal_path: str | os.PathLike[str] | None,
    formal_path: str | os.PathLike[str],
    proof_path: str | os.PathLike[str] | None = None,
    *,
    language: str = "lean4",
    id_field: str = "task_id",
) -> tuple[list[StatementTask], list[ProofAttempt]]:
    """Load the three-file layout: problem text, formalization, proof.

    Each file is JSONL keyed by ``task_id``; they are joined on that id,
    not on line order, because three files that drift out of alignment
    would silently pair every problem with the wrong formalization.

    Returns statement tasks (problem + formalization, for
    :class:`~ftp_eval.statement.StatementChecker`) and proof attempts (for
    the verifier).

    Both other files are optional, for the two common partial setups:

    * ``proof_path=None`` -- audit formalizations without any proofs.
    * ``informal_path=None`` -- the statements are **given** (the harness
      supplies them, the model only writes proofs), so there is no prose
      to check faithfulness against. The structural checks still apply,
      and those are the ones that matter for a fixed task set: a vacuous
      or trivially-true statement is now your dataset's bug, not the
      model's, and worth finding once rather than per attempt.

    Recognized field names, in order of preference:

    * informal: ``informal_statement``, ``problem``, ``statement``, ``nl``, ``text``
    * formal:   ``formal_statement``, ``formal``, ``statement``, ``theorem``
    * proof:    ``proof``, ``formal_proof``, ``completion``, ``output``
    """
    informal = _index_by_id(informal_path, id_field, "informal") if informal_path else {}
    formal = _index_by_id(formal_path, id_field, "formal")
    proofs = _index_by_id(proof_path, id_field, "proof") if proof_path else {}

    missing_formal = sorted(set(informal) - set(formal))
    if missing_formal:
        raise ValueError(
            "%s: %d problem(s) have no formalization (e.g. %s)"
            % (formal_path, len(missing_formal), ", ".join(missing_formal[:5]))
        )
    # Only worth warning about when prose was supplied at all: with
    # `informal_path=None` the caller has said the statements are given and
    # there is no prose by design, so the warning would be pure noise.
    orphan_formal = sorted(set(formal) - set(informal)) if informal_path else []
    if orphan_formal:
        # Not fatal: a formalization without its prose can still be proof
        # checked, it just cannot be judged for faithfulness.
        print(
            "warning: %d formalization(s) have no natural-language problem (e.g. %s); "
            "their faithfulness cannot be assessed"
            % (len(orphan_formal), ", ".join(orphan_formal[:5])),
            file=sys.stderr,
        )

    statement_tasks: list[StatementTask] = []
    attempts: list[ProofAttempt] = []

    for task_id in sorted(set(formal)):
        formal_row = formal[task_id]
        informal_row = informal.get(task_id, {})
        statement_tasks.append(
            StatementTask(
                task_id=task_id,
                informal_statement=_first_field(
                    informal_row,
                    ("informal_statement", "problem", "statement", "nl", "text"),
                    default="",
                ),
                formal_statement=_first_field(
                    formal_row,
                    ("formal_statement", "formal", "statement", "theorem"),
                    required=True,
                    what="formal statement",
                    task_id=task_id,
                ),
                header=str(formal_row.get("header") or ""),
                language=str(formal_row.get("language") or language),
                gold_formal_statement=formal_row.get("gold_formal_statement"),
                split=formal_row.get("split") or informal_row.get("split"),
                metadata={
                    **{k: v for k, v in informal_row.items() if k != id_field},
                    **{k: v for k, v in formal_row.items() if k != id_field},
                },
            )
        )
        proof_rows = proofs.get(task_id)
        if proof_rows is None:
            continue
        for index, row in enumerate(proof_rows if isinstance(proof_rows, list) else [proof_rows]):
            attempts.append(
                ProofAttempt(
                    task_id=task_id,
                    proof=_first_field(
                        row,
                        ("proof", "formal_proof", "completion", "output"),
                        required=True,
                        what="proof",
                        task_id=task_id,
                    ),
                    model=row.get("model"),
                    sample_index=int(row.get("sample_index", index) or index),
                    metadata={k: v for k, v in row.items() if k != id_field},
                )
            )
    return statement_tasks, attempts


def _index_by_id(
    path: str | os.PathLike[str], id_field: str, what: str
) -> dict[str, Any]:
    """Index a JSONL file by id, collecting repeats into lists."""
    out: dict[str, Any] = {}
    for lineno, obj in enumerate(read_jsonl(path), start=1):
        task_id = obj.get(id_field) or obj.get("id") or obj.get("name")
        if not task_id:
            raise ValueError(
                "%s:%d: %s record has no %r field to join on"
                % (path, lineno, what, id_field)
            )
        task_id = str(task_id)
        if task_id in out:
            existing = out[task_id]
            out[task_id] = (existing if isinstance(existing, list) else [existing]) + [obj]
        else:
            out[task_id] = [obj] if what == "proof" else obj
    return out


def _first_field(
    row: Mapping[str, Any],
    names: Sequence[str],
    *,
    default: str | None = None,
    required: bool = False,
    what: str = "field",
    task_id: str = "?",
) -> str:
    for name in names:
        value = row.get(name)
        if isinstance(value, str) and value.strip():
            return value
    if required:
        raise ValueError(
            "task %r: no %s found; looked for %s, got keys %s"
            % (task_id, what, "/".join(names), ", ".join(sorted(row)[:10]))
        )
    return default or ""


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
