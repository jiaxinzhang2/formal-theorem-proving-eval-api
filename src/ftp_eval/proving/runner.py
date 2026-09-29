"""Batch evaluation: pair tasks with attempts, verify, stream results out.

Verification runs are long and often metered, so the runner is built
around three properties:

* **Incremental.** Every result is handed to the callback and flushed to
  disk the moment it exists. You watch a run progress instead of waiting
  for a final dump, and a crash costs you one attempt, not the run.
* **Resumable.** Results already in the output file are skipped.
* **Isolated.** One exploding task cannot take down the run; it becomes
  a ``Status.ERROR`` row and the run continues.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

from ..dataset import ResultWriter, already_done
from ..types import (
    Diagnostic,
    ErrorKind,
    ProofAttempt,
    ProofTask,
    Severity,
    Status,
    VerificationResult,
    index_attempts,
)
from .verifier import Verifier, assemble_source

__all__ = ["EvalRunner", "RunConfig", "ProgressEvent", "ResultCache"]


@dataclass
class RunConfig:
    """Knobs for one evaluation run."""

    #: Wall-clock budget per attempt, unless the task overrides it.
    timeout_s: float = 300.0
    #: Parallel verifications. Forced to 1 for backends that say they are
    #: not thread-safe, because silently corrupting a run is worse than
    #: being slow.
    concurrency: int = 1
    #: Directory for the verdict cache; None disables caching.
    cache_dir: str | os.PathLike[str] | None = None
    #: Skip attempts whose ids are already in the output file.
    resume: bool = False
    #: Stop the run after this many ``Status.ERROR`` results. Guards
    #: against burning an hour on a misconfigured toolchain.
    max_consecutive_errors: int = 20
    #: Verify at most this many samples per task (None = all).
    limit_samples: int | None = None


@dataclass
class ProgressEvent:
    """Emitted after each attempt, for progress bars and logging."""

    index: int
    total: int
    result: VerificationResult

    @property
    def elapsed_fraction(self) -> float:
        return self.index / self.total if self.total else 1.0

    def format_line(self) -> str:
        r = self.result
        mark = {
            Status.VERIFIED: "OK  ",
            Status.REJECTED: "CHEAT",
            Status.FAILED: "fail",
            Status.TIMEOUT: "time",
            Status.ERROR: "ERR ",
            Status.SKIPPED: "skip",
        }[r.status]
        detail = ""
        if r.status is Status.REJECTED and r.soundness.violations:
            detail = "  <- %s" % r.soundness.violations[0]
        elif r.error_kind and r.status is not Status.VERIFIED:
            detail = "  [%s]" % r.error_kind.value
        return "[%d/%d] %-5s %-28s %6.1fs%s%s" % (
            self.index,
            self.total,
            mark,
            r.task_id[:28],
            r.wall_time_s,
            " (cached)" if r.cached else "",
            detail,
        )


class ResultCache:
    """Content-addressed verdict cache.

    Keyed on (backend, task fingerprint, proof text), so re-running an
    evaluation after adding one model costs only the new work, while any
    edit to a statement or a proof invalidates its entry automatically.
    """

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _key(self, backend: str, task: ProofTask, attempt: ProofAttempt) -> str:
        payload = "\x00".join([backend, task.fingerprint, attempt.proof])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, backend: str, task: ProofTask, attempt: ProofAttempt) -> VerificationResult | None:
        path = self.dir / (self._key(backend, task, attempt) + ".json")
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cached"] = True
            return VerificationResult.from_dict(data)
        except Exception:
            # A corrupt cache entry should cost one recomputation, nothing more.
            return None

    def put(self, backend: str, task: ProofTask, attempt: ProofAttempt, result: VerificationResult) -> None:
        if result.status in (Status.ERROR, Status.SKIPPED):
            # Never cache a non-verdict: the next run may have a working
            # toolchain and deserves a real answer.
            return
        path = self.dir / (self._key(backend, task, attempt) + ".json")
        tmp = path.with_suffix(".json.tmp")
        with self._lock:
            tmp.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)


class EvalRunner:
    """Drives a :class:`~ftp_eval.verifier.Verifier` over a task set."""

    def __init__(
        self,
        verifier: Verifier,
        config: RunConfig | None = None,
        *,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> None:
        self.verifier = verifier
        self.config = config or RunConfig()
        self.on_progress = on_progress
        self.cache = ResultCache(self.config.cache_dir) if self.config.cache_dir else None

    # -- planning -----------------------------------------------------

    def plan(
        self,
        tasks: Sequence[ProofTask],
        attempts: Iterable[ProofAttempt],
        *,
        skip_attempt_ids: set[str] | None = None,
    ) -> tuple[list[tuple[ProofTask, ProofAttempt]], list[VerificationResult]]:
        """Pair tasks with attempts.

        Returns the work to do plus ``SKIPPED`` results for tasks that got
        no attempt at all -- a task nobody tried is reported, not dropped,
        because a silently shrinking denominator inflates every score.
        """
        by_task = index_attempts(attempts)
        skip = skip_attempt_ids or set()
        work: list[tuple[ProofTask, ProofAttempt]] = []
        prefilled: list[VerificationResult] = []

        for task in tasks:
            task_attempts = by_task.get(task.task_id, [])
            if not task_attempts:
                prefilled.append(
                    VerificationResult(
                        task_id=task.task_id,
                        attempt_id="%s#none" % task.task_id,
                        backend=self.verifier.name,
                        status=Status.SKIPPED,
                        error_kind=ErrorKind.HARNESS,
                        split=task.split,
                        diagnostics=(
                            Diagnostic(
                                Severity.WARNING,
                                "no attempt was supplied for this task",
                                kind=ErrorKind.HARNESS,
                            ),
                        ),
                    )
                )
                continue
            if self.config.limit_samples is not None:
                task_attempts = task_attempts[: self.config.limit_samples]
            for attempt in task_attempts:
                if attempt.attempt_id in skip:
                    continue
                work.append((task, attempt))

        unknown = set(by_task) - {t.task_id for t in tasks}
        if unknown:
            # Attempts for tasks we do not have cannot be scored; say so
            # loudly rather than dropping them on the floor.
            sample = ", ".join(sorted(unknown)[:5])
            print(
                "warning: %d attempt group(s) reference unknown task ids (e.g. %s)"
                % (len(unknown), sample),
                file=sys.stderr,
            )
        return work, prefilled

    # -- execution ----------------------------------------------------

    def run(
        self,
        tasks: Sequence[ProofTask],
        attempts: Iterable[ProofAttempt],
        *,
        out_path: str | os.PathLike[str] | None = None,
    ) -> list[VerificationResult]:
        """Verify everything and return all results (also streamed to disk)."""
        return list(self.iter_run(tasks, attempts, out_path=out_path))

    def iter_run(
        self,
        tasks: Sequence[ProofTask],
        attempts: Iterable[ProofAttempt],
        *,
        out_path: str | os.PathLike[str] | None = None,
    ) -> Iterator[VerificationResult]:
        """Same as :meth:`run`, but yields each result as it lands."""
        skip: set[str] = set()
        if self.config.resume and out_path:
            skip = already_done(out_path)
            if skip:
                print("resuming: %d attempt(s) already verified" % len(skip), file=sys.stderr)

        work, prefilled = self.plan(tasks, attempts, skip_attempt_ids=skip)
        total = len(work) + len(prefilled)
        index = 0
        consecutive_errors = 0
        started = time.monotonic()

        concurrency = max(1, int(self.config.concurrency))
        if concurrency > 1 and not self.verifier.thread_safe:
            print(
                "note: backend %r is not thread-safe; running with concurrency=1"
                % self.verifier.name,
                file=sys.stderr,
            )
            concurrency = 1

        writer = ResultWriter(out_path, append=bool(skip))
        try:
            for result in prefilled:
                index += 1
                writer.write(result)
                self._emit(ProgressEvent(index, total, result))
                yield result

            if concurrency == 1:
                for task, attempt in work:
                    result = self._verify_one(task, attempt)
                    index += 1
                    writer.write(result)
                    self._emit(ProgressEvent(index, total, result))
                    yield result
                    consecutive_errors = (
                        consecutive_errors + 1 if result.status is Status.ERROR else 0
                    )
                    if self._should_abort(consecutive_errors):
                        break
            else:
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures: dict[Future[VerificationResult], tuple[ProofTask, ProofAttempt]] = {}
                    pending = iter(work)
                    # Keep the pool topped up rather than submitting all of
                    # it at once, so an abort actually stops early.
                    for _ in range(concurrency):
                        item = next(pending, None)
                        if item is None:
                            break
                        futures[pool.submit(self._verify_one, *item)] = item
                    aborted = False
                    while futures and not aborted:
                        done, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                        for done_future in done:
                            futures.pop(done_future, None)
                            result = done_future.result()
                            index += 1
                            writer.write(result)
                            self._emit(ProgressEvent(index, total, result))
                            yield result
                            consecutive_errors = (
                                consecutive_errors + 1 if result.status is Status.ERROR else 0
                            )
                            if self._should_abort(consecutive_errors):
                                for f in futures:
                                    f.cancel()
                                aborted = True
                                break
                            item = next(pending, None)
                            if item is not None:
                                futures[pool.submit(self._verify_one, *item)] = item
        finally:
            writer.close()
            if out_path:
                print(
                    "wrote %d result(s) to %s in %.1fs"
                    % (writer.count, out_path, time.monotonic() - started),
                    file=sys.stderr,
                )

    def _should_abort(self, consecutive_errors: int) -> bool:
        limit = self.config.max_consecutive_errors
        if limit and consecutive_errors >= limit:
            print(
                "aborting: %d consecutive harness/toolchain errors -- fix the backend "
                "before continuing (partial results are on disk)" % consecutive_errors,
                file=sys.stderr,
            )
            return True
        return False

    def _verify_one(self, task: ProofTask, attempt: ProofAttempt) -> VerificationResult:
        if self.cache is not None:
            hit = self.cache.get(self.verifier.name, task, attempt)
            if hit is not None:
                return hit
        result = self.verifier.verify(task, attempt, timeout_s=self.config.timeout_s)
        if self.cache is not None:
            self.cache.put(self.verifier.name, task, attempt, result)
        return result

    def _emit(self, event: ProgressEvent) -> None:
        if self.on_progress is not None:
            self.on_progress(event)

    # -- debugging helper ---------------------------------------------

    def preview_source(self, task: ProofTask, attempt: ProofAttempt) -> str:
        """The exact text the backend would see. Worth checking once per
        new dataset: a mis-set ``assembly`` turns every result into a
        syntax error and looks like a terrible model."""
        return assemble_source(task, attempt)
