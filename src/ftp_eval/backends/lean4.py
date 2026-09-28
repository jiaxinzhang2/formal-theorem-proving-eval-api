"""Lean 4 backend: compile a file with ``lake env lean`` and read the log.

This is the batch-compilation strategy -- one process per attempt, full
elaboration, no persistent session. It is slower per attempt than driving
a REPL, but it is the strategy whose verdict is easiest to trust: the
file either compiles against the real Mathlib you pinned or it does not,
with no shared state carried over from a previous attempt.

Requirements: a Lean 4 project directory (one containing
``lakefile.lean``/``lakefile.toml`` and a built ``.lake``), so that
``import Mathlib`` resolves. Point ``project_dir`` at it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from ..types import (
    BackendInfo,
    Diagnostic,
    ErrorKind,
    ProofAttempt,
    ProofTask,
    Severity,
    SoundnessReport,
    Status,
)
from ..verifier import BackendUnavailable, RawVerdict, Verifier, VerifierError

__all__ = ["Lean4Verifier", "parse_lean_log", "classify_lean_message"]

#: ``file.lean:12:4: error: message`` -- Lean's standard message prefix.
_MESSAGE_RE = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+):(?P<col>\d+):\s*(?P<severity>error|warning|info):\s*(?P<msg>.*)$"
)

#: Ordered, first match wins. Ordering matters: "unknown identifier" is
#: also a type error, and the more specific label is the useful one.
_CLASSIFIERS: tuple[tuple[re.Pattern[str], ErrorKind], ...] = (
    (re.compile(r"unknown (identifier|constant|namespace|tactic)", re.I), ErrorKind.UNKNOWN_IDENTIFIER),
    (re.compile(r"declaration uses 'sorry'", re.I), ErrorKind.INCOMPLETE),
    (re.compile(r"unsolved goals", re.I), ErrorKind.UNSOLVED_GOALS),
    # Lean writes this as "(deterministic) timeout at whnf", so the
    # parenthesis has to be optional or the match silently misses.
    (re.compile(r"(maximum recursion depth|\(?deterministic\)?\s+timeout|maxHeartbeats|out of memory)", re.I), ErrorKind.RESOURCE_LIMIT),
    (re.compile(r"(unexpected token|expected .*(token|term|command)|unexpected end of input)", re.I), ErrorKind.SYNTAX),
    (re.compile(r"(linarith failed|nlinarith failed|simp made no progress|ring_nf failed|tactic .* failed|omega could not)", re.I), ErrorKind.TACTIC_FAILED),
    (re.compile(r"(type mismatch|application type mismatch|failed to synthesize|function expected)", re.I), ErrorKind.TYPE),
)


def classify_lean_message(message: str) -> ErrorKind:
    """Map one Lean error message onto the shared taxonomy."""
    for pattern, kind in _CLASSIFIERS:
        if pattern.search(message):
            return kind
    return ErrorKind.UNKNOWN


def parse_lean_log(text: str) -> list[Diagnostic]:
    """Parse ``lean``'s stdout/stderr into diagnostics.

    Continuation lines (the goal state under an "unsolved goals" error)
    are attached to the diagnostic they belong to, because the first line
    alone rarely says what actually went wrong.
    """
    diagnostics: list[Diagnostic] = []
    pending: list[str] = []

    def flush() -> None:
        if not pending or not diagnostics:
            pending.clear()
            return
        last = diagnostics[-1]
        extra = "\n".join(pending).rstrip()
        pending.clear()
        if extra:
            diagnostics[-1] = Diagnostic(
                severity=last.severity,
                message=(last.message + "\n" + extra).strip(),
                line=last.line,
                column=last.column,
                kind=last.kind,
            )

    for raw_line in text.splitlines():
        match = _MESSAGE_RE.match(raw_line.strip())
        if match:
            flush()
            message = match.group("msg").strip()
            severity = Severity(match.group("severity"))
            diagnostics.append(
                Diagnostic(
                    severity=severity,
                    message=message,
                    line=int(match.group("line")),
                    column=int(match.group("col")),
                    kind=classify_lean_message(message) if severity is Severity.ERROR else None,
                )
            )
        elif diagnostics:
            pending.append(raw_line)
    flush()

    # Re-classify now that continuation lines are attached: "unsolved
    # goals" often only appears in the body.
    out: list[Diagnostic] = []
    for d in diagnostics:
        if d.severity is Severity.ERROR and d.kind is ErrorKind.UNKNOWN:
            d = Diagnostic(d.severity, d.message, d.line, d.column, classify_lean_message(d.message))
        out.append(d)
    return out


class Lean4Verifier(Verifier):
    """Verify Lean 4 proofs by compiling them in a Lean project.

    Parameters
    ----------
    project_dir:
        A Lean 4 project whose dependencies are already built. Defaults
        to ``$FTP_EVAL_LEAN_PROJECT``.
    lake:
        Path to the ``lake`` executable (default: found on ``PATH``).
    memory_mb, max_heartbeats:
        Per-attempt resource caps. A runaway ``simp`` should cost one
        attempt, not the machine.
    """

    name = "lean4"
    language = "lean4"
    #: Separate processes and separate temp files, so parallel is safe;
    #: Lake's own build lock is the reason the project must be pre-built.
    thread_safe = True

    def __init__(
        self,
        *,
        project_dir: str | os.PathLike[str] | None = None,
        lake: str | None = None,
        memory_mb: int | None = 8192,
        max_heartbeats: int | None = 400_000,
        extra_args: Iterable[str] = (),
        keep_sources: str | os.PathLike[str] | None = None,
        **config: Any,
    ) -> None:
        super().__init__(**config)
        raw_dir = project_dir or os.environ.get("FTP_EVAL_LEAN_PROJECT")
        self.project_dir = Path(raw_dir).expanduser().resolve() if raw_dir else None
        self.lake = lake or os.environ.get("FTP_EVAL_LAKE") or "lake"
        self.memory_mb = memory_mb
        self.max_heartbeats = max_heartbeats
        self.extra_args = tuple(extra_args)
        #: Where to keep generated .lean files for debugging; None = temp.
        self.keep_sources = Path(keep_sources) if keep_sources else None
        if self.keep_sources:
            self.keep_sources.mkdir(parents=True, exist_ok=True)

    # -- availability -------------------------------------------------

    def _lake_path(self) -> str:
        found = shutil.which(self.lake)
        if not found:
            raise BackendUnavailable(
                "could not find %r on PATH; install Lean 4 via elan or set FTP_EVAL_LAKE"
                % self.lake
            )
        return found

    def info(self) -> BackendInfo:
        try:
            lake = self._lake_path()
        except BackendUnavailable as exc:
            return BackendInfo(self.name, self.language, False, detail=str(exc))
        if self.project_dir is None:
            return BackendInfo(
                self.name,
                self.language,
                False,
                detail="no Lean project configured; pass project_dir= or set "
                "FTP_EVAL_LEAN_PROJECT to a built Lean 4 project",
            )
        if not self.project_dir.is_dir():
            return BackendInfo(
                self.name, self.language, False, detail="project_dir does not exist: %s" % self.project_dir
            )
        has_lakefile = any(
            (self.project_dir / f).exists() for f in ("lakefile.lean", "lakefile.toml")
        )
        if not has_lakefile:
            return BackendInfo(
                self.name,
                self.language,
                False,
                detail="%s has no lakefile.lean/lakefile.toml" % self.project_dir,
            )
        version = None
        try:
            proc = subprocess.run(
                [lake, "env", "lean", "--version"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            version = (proc.stdout or proc.stderr).strip().splitlines()[0] if proc.stdout or proc.stderr else None
        except Exception as exc:
            return BackendInfo(
                self.name, self.language, False, detail="lake env lean failed: %s" % exc
            )
        if not (self.project_dir / ".lake").exists():
            return BackendInfo(
                self.name,
                self.language,
                False,
                version=version,
                detail="%s has no .lake build directory; run `lake exe cache get && lake build` "
                "first, otherwise every attempt pays for a full Mathlib build" % self.project_dir,
            )
        return BackendInfo(
            self.name,
            self.language,
            True,
            version=version,
            supports=("batch_compile", "axiom_audit", "heartbeat_limit", "memory_limit"),
        )

    # -- verification -------------------------------------------------

    def _verify(
        self, task: ProofTask, attempt: ProofAttempt, source: str, timeout_s: float
    ) -> RawVerdict:
        info = self.info()
        if not info.available:
            raise BackendUnavailable(info.detail or "lean4 backend unavailable")
        assert self.project_dir is not None  # guaranteed by info().available

        source = self._with_options(source)
        path, cleanup = self._write_source(task, attempt, source)
        cmd = [self._lake_path(), "env", "lean"]
        if self.memory_mb:
            cmd.append("--memory=%d" % self.memory_mb)
        cmd.extend(self.extra_args)
        cmd.append(str(path))

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return RawVerdict.timeout({"command": " ".join(cmd), "timeout_s": timeout_s})
        except OSError as exc:
            raise VerifierError("failed to run lean: %s" % exc) from exc
        finally:
            cleanup()

        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        diagnostics = parse_lean_log(log)
        raw = {
            "exit_code": proc.returncode,
            "command": " ".join(cmd),
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-4000:],
        }

        errors = [d for d in diagnostics if d.severity is Severity.ERROR]
        if proc.returncode == 0 and not errors:
            return RawVerdict.verified(raw)

        if errors:
            # The first error is the one the model has to fix; later ones
            # are usually cascade damage.
            kind = errors[0].kind or ErrorKind.UNKNOWN
        elif proc.returncode != 0:
            # Non-zero exit with no parseable error: that is the toolchain
            # misbehaving, not the proof being wrong. Do not score it.
            raise VerifierError(
                "lean exited %d with no parseable diagnostics; log tail: %s"
                % (proc.returncode, log[-500:].strip())
            )
        else:
            kind = ErrorKind.UNKNOWN
        return RawVerdict.failed(kind, diagnostics, raw)

    def _with_options(self, source: str) -> str:
        """Insert resource caps so one attempt cannot hang the run.

        The option has to go *after* the imports: Lean 4 requires every
        ``import`` at the top of the file, so prepending a ``set_option``
        would turn every attempt into a syntax error.
        """
        if not self.max_heartbeats:
            return source
        option = "set_option maxHeartbeats %d" % self.max_heartbeats
        lines = source.splitlines()
        insert_at = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import ") or not stripped or stripped.startswith("--"):
                if stripped.startswith("import "):
                    insert_at = i + 1
                continue
            break
        lines.insert(insert_at, option)
        return "\n".join(lines) + "\n"

    def _write_source(self, task: ProofTask, attempt: ProofAttempt, source: str):
        """Write the source where ``lake env lean`` can see it.

        The file lives inside the project directory: Lean resolves
        imports relative to the project root, and a file in the system
        temp dir would not find Mathlib.
        """
        stem = re.sub(r"[^A-Za-z0-9_]", "_", "%s_%s" % (task.task_id, attempt.attempt_id))[:80]
        if self.keep_sources:
            path = self.keep_sources / ("%s.lean" % stem)
            path.write_text(source, encoding="utf-8")
            return path, lambda: None

        assert self.project_dir is not None
        scratch = self.project_dir / ".ftp_eval_tmp"
        scratch.mkdir(exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="%s_" % stem, suffix=".lean", dir=scratch)
        path = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(source)

        def cleanup() -> None:
            try:
                path.unlink()
                olean = path.with_suffix(".olean")
                if olean.exists():
                    olean.unlink()
            except OSError:
                pass

        return path, cleanup

    # -- soundness ----------------------------------------------------

    def extra_soundness_checks(
        self, task: ProofTask, attempt: ProofAttempt, source: str, verdict: RawVerdict
    ) -> SoundnessReport:
        """Upgrade the regex screen with what Lean itself reports.

        ``declaration uses 'sorry'`` is emitted as a *warning*, so a file
        full of ``sorry`` compiles with exit code 0. Without this check a
        model that answers every problem with ``sorry`` scores 100%.
        """
        report = SoundnessReport()
        if verdict.status is not Status.VERIFIED:
            return report
        for d in verdict.diagnostics:
            lowered = d.message.lower()
            if "uses 'sorry'" in lowered or "uses sorry" in lowered:
                report = report.with_violation("Lean reports the declaration uses 'sorry'")
            elif "uses 'native_decide'" in lowered or "native_decide" in lowered:
                report = report.with_violation(
                    "proof relies on native_decide, which trusts the compiler rather than the kernel"
                )
        return report
