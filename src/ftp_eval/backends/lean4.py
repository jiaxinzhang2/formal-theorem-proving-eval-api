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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .types import (
    BackendInfo,
    Diagnostic,
    ErrorKind,
    ProbeKind,
    ProofAttempt,
    ProofTask,
    Severity,
    SoundnessReport,
    StatementTask,
    Status,
)

from ..source.soundness import audit_axioms
from .verifier import BackendUnavailable, RawVerdict, Verifier, VerifierError

__all__ = [
    "Lean4Verifier",
    "parse_lean_log",
    "classify_lean_message",
    "LeanTheorem",
    "parse_lean_theorem",
    "parse_printed_axioms",
]

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


@dataclass(frozen=True)
class LeanTheorem:
    """A Lean 4 theorem taken apart into the pieces a probe needs.

    ``theorem foo (n : Nat) (h : 0 < n) : n ^ 2 >= n := by`` becomes
    keyword ``theorem``, name ``foo``, binders ``(n : Nat) (h : 0 < n)``,
    conclusion ``n ^ 2 >= n``.
    """

    keyword: str
    name: str
    binders: str
    conclusion: str

    def rebuild(self, *, name: str | None = None, conclusion: str | None = None) -> str:
        """Reassemble, optionally substituting the name or the goal."""
        parts = [self.keyword, name or self.name]
        if self.binders.strip():
            parts.append(self.binders.strip())
        return "%s : %s" % (" ".join(parts), conclusion if conclusion is not None else self.conclusion)


#: Declaration keywords whose shape is ``kw name binders : goal``.
_THEOREM_KEYWORDS = ("theorem", "lemma", "example", "problem")

_OPENERS = {"(": ")", "[": "]", "{": "}", "⟨": "⟩", "⦃": "⦄"}
_CLOSERS = {v: k for k, v in _OPENERS.items()}


def _split_top_level_colon(text: str) -> int:
    """Index of the ``:`` that separates binders from the goal, or -1.

    Walks the string tracking bracket depth so that the colons inside
    ``(n : Nat)`` are skipped, and skips ``:=`` so that a statement with
    no binders is not split at its assignment.
    """
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
            if depth < 0:
                return -1
        elif ch == ":" and depth == 0:
            if text[i : i + 2] == ":=":
                return -1
            return i
        i += 1
    return -1


def parse_lean_theorem(statement: str) -> LeanTheorem | None:
    """Split a Lean 4 theorem statement into its parts.

    Returns ``None`` for anything it cannot take apart confidently --
    mutual blocks, ``def``s, statements whose binders do not balance.
    Refusing beats guessing here: a mis-parsed statement produces a probe
    that tests the wrong proposition and reports a confident wrong answer.
    """
    text = strip_lean_comments(statement).strip()
    if not text:
        return None

    # Drop anything from the proof seam onward; the binders and goal are
    # all that a probe needs.
    seam = _find_seam(text)
    if seam != -1:
        text = text[:seam].rstrip()

    match = re.match(
        r"^(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|nonrec\s+)?(%s)\s+"
        % "|".join(_THEOREM_KEYWORDS),
        text,
    )
    if not match:
        return None
    keyword = match.group(1)
    rest = text[match.end() :].lstrip()

    # `example` has no name; everything else takes an identifier.
    if keyword == "example":
        name, after_name = "", rest
    else:
        name_match = re.match(r"^([^\s({\[:]+)", rest)
        if not name_match:
            return None
        name = name_match.group(1)
        after_name = rest[name_match.end() :]

    colon = _split_top_level_colon(after_name)
    if colon == -1:
        return None
    binders = after_name[:colon].strip()
    conclusion = after_name[colon + 1 :].strip()
    if not conclusion:
        return None
    return LeanTheorem(keyword=keyword, name=name or "anonymous", binders=binders, conclusion=conclusion)


def _find_seam(text: str) -> int:
    """Index of the top-level ``:=`` that starts the proof, or -1."""
    depth = 0
    for i, ch in enumerate(text):
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
        elif ch == ":" and depth == 0 and text[i : i + 2] == ":=":
            return i
    return -1


def strip_lean_comments(text: str) -> str:
    """Remove Lean block and line comments."""
    out = re.sub(r"/-(?:.|\n)*?-/", " ", text)
    return re.sub(r"--[^\n]*", " ", out)


def _normalize_binders(binders: str) -> str:
    """Whitespace-insensitive form, for comparing two binder lists."""
    return re.sub(r"\s+", " ", binders.strip())


#: ``'foo' depends on axioms: [propext, Classical.choice]`` -- or
#: ``'foo' does not depend on any axioms``.
_AXIOMS_LINE_RE = re.compile(
    r"'(?P<name>[^']+)'\s+(?:depends on axioms:\s*\[(?P<axioms>[^\]]*)\]"
    r"|does not depend on any axioms)",
)


def parse_printed_axioms(log: str, name: str) -> list[str] | None:
    """Read a ``#print axioms`` listing out of Lean's output.

    Returns the axiom names, ``[]`` when Lean said the declaration depends
    on none, or ``None`` when no listing for ``name`` was found -- which
    means the audit did not run and must not be read as a clean result.
    """
    for match in _AXIOMS_LINE_RE.finditer(log):
        printed = match.group("name")
        if printed != name and not printed.endswith("." + name):
            continue
        raw = match.group("axioms")
        if raw is None:
            return []
        return [a.strip() for a in raw.split(",") if a.strip()]
    return None


#: Tactics tried when asking "is this goal trivially closable?". Cheap and
#: total, so the probe is fast; ``decide``/``norm_num`` are included
#: because a formalization reduced to a closed numeric claim is exactly
#: the degenerate case worth catching.
_TRIVIAL_TACTICS = ("trivial", "rfl", "simp", "decide", "norm_num", "omega", "tauto")

#: Tactics tried when asking "are these hypotheses contradictory?".
_VACUITY_TACTICS = ("omega", "simp_all", "exact absurd rfl (by decide)", "linarith", "tauto")


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
        audit_axioms: bool = True,
        axiom_audit_timeout_s: float = 180.0,
        **config: Any,
    ) -> None:
        super().__init__(**config)
        #: Run the kernel axiom audit on accepted proofs. Costs a second
        #: compile per verified attempt and is the strongest soundness
        #: evidence available, so it defaults on.
        self.audit_axioms = audit_axioms
        self.axiom_audit_timeout_s = axiom_audit_timeout_s
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

        # Timed around the subprocess alone, so the figure is Lean's own
        # elaboration cost and not our file writing, screening or parsing.
        compile_started = time.monotonic()
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
            return RawVerdict.timeout(
                {"command": " ".join(cmd), "timeout_s": timeout_s},
                compile_time_s=time.monotonic() - compile_started,
            )
        except OSError as exc:
            raise VerifierError("failed to run lean: %s" % exc) from exc
        finally:
            cleanup()
        compile_time_s = time.monotonic() - compile_started

        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        diagnostics = parse_lean_log(log)
        raw = {
            "exit_code": proc.returncode,
            "command": " ".join(cmd),
            "compile_time_s": round(compile_time_s, 4),
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-4000:],
        }

        errors = [d for d in diagnostics if d.severity is Severity.ERROR]
        if proc.returncode == 0 and not errors:
            return RawVerdict.verified(raw, compile_time_s=compile_time_s)

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
        return RawVerdict.failed(kind, diagnostics, raw, compile_time_s=compile_time_s)

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

    # -- statement probes ---------------------------------------------

    def build_probe(self, task: StatementTask, kind: ProbeKind) -> str | None:
        """Build the Lean source that answers one question about a statement.

        Every probe reuses the statement's own binders, so it asks about
        the proposition the dataset actually contains rather than a
        paraphrase of it. Returns ``None`` when the statement does not
        parse, which the caller reports as "check did not run".
        """
        header = task.header.rstrip()
        parsed = parse_lean_theorem(task.formal_statement)

        if kind is ProbeKind.ELABORATES:
            # No parse needed: keep the statement verbatim and give it a
            # placeholder body. If this fails to compile, the statement
            # itself is broken, whatever the proof might have said.
            statement = task.formal_statement.rstrip()
            seam = _find_seam(strip_lean_comments(statement))
            body = statement if seam != -1 else statement + " :="
            return self._probe_file(header, "%s sorry" % body.rstrip())

        if parsed is None:
            return None

        if kind is ProbeKind.TRIVIAL:
            # `first | tac | tac | ...` succeeds if any single cheap tactic
            # closes the goal. One compile instead of seven.
            tactics = " | ".join(_TRIVIAL_TACTICS)
            statement = parsed.rebuild(name="ftp_eval_triviality_probe")
            return self._probe_file(header, "%s := by\n  first | %s" % (statement, tactics))

        if kind is ProbeKind.VACUOUS:
            if not parsed.binders.strip():
                # No hypotheses means nothing to be contradictory.
                return None
            tactics = " | ".join(_VACUITY_TACTICS)
            statement = parsed.rebuild(name="ftp_eval_vacuity_probe", conclusion="False")
            return self._probe_file(header, "%s := by\n  first | %s" % (statement, tactics))

        if kind is ProbeKind.GOLD_EQUIVALENT:
            gold = parse_lean_theorem(task.gold_formal_statement or "")
            if gold is None:
                return None
            if _normalize_binders(gold.binders) != _normalize_binders(parsed.binders):
                # Different binders mean the two goals are stated over
                # different contexts; an `Iff` between them would not
                # typecheck, and pretending otherwise would produce a
                # compile error we would misread as "not equivalent".
                return None
            statement = parsed.rebuild(
                name="ftp_eval_gold_equivalence_probe",
                conclusion="(%s) ↔ (%s)" % (parsed.conclusion, gold.conclusion),
            )
            return self._probe_file(
                header, "%s := by\n  first | rfl | simp | tauto | constructor <;> intro h <;> simpa using h" % statement
            )

        return None

    def _probe_file(self, header: str, body: str) -> str:
        parts = [p for p in (header, body) if p.strip()]
        return "\n\n".join(parts) + "\n"

    # -- soundness ----------------------------------------------------

    def extra_soundness_checks(
        self, task: ProofTask, attempt: ProofAttempt, source: str, verdict: RawVerdict
    ) -> SoundnessReport:
        """Upgrade the regex screen with what Lean itself reports.

        Two layers, in increasing strength:

        1. **Lean's own warnings.** ``declaration uses 'sorry'`` is a
           *warning*, so a file full of ``sorry`` compiles with exit code
           0. Without this a model that answers ``sorry`` scores 100%.
        2. **A kernel axiom audit** (``#print axioms``), when
           ``audit_axioms`` is on. This is the only check that sees
           through indirection: a ``sorry`` inside a helper lemma, or an
           axiom pulled in from another file, still appears in the axiom
           set. It is also the check that catches the case where the
           kernel is genuinely satisfied -- ``axiom cheat : <goal>``
           typechecks perfectly and produces no warning at all, and the
           only trace is ``cheat`` in this listing.
        """
        report = SoundnessReport()
        if verdict.status is not Status.VERIFIED:
            return report

        for d in verdict.diagnostics:
            lowered = d.message.lower()
            if "uses 'sorry'" in lowered or "uses sorry" in lowered:
                report = report.with_violation(
                    "[placeholder] Lean reports the declaration uses 'sorry'"
                )
            elif "native_decide" in lowered:
                report = report.with_violation(
                    "[kernel_bypass] Lean reports the proof relies on native_decide, "
                    "which trusts the compiler rather than the kernel"
                )

        if self.audit_axioms:
            report = report.merge(self._audit_axioms(task, source))
        return report

    def _audit_axioms(self, task: ProofTask, source: str) -> SoundnessReport:
        """Re-elaborate the file with ``#print axioms`` appended.

        Costs a second compile, which is why it is separately switchable.
        It is worth it on a headline run: this is the difference between
        "the compiler was happy" and "the kernel checked it from the
        standard axioms and nothing else".
        """
        parsed = parse_lean_theorem(task.formal_statement)
        if parsed is None or parsed.name in ("", "anonymous"):
            # Nothing to interrogate -- an `example` has no name to print
            # axioms for. Reported as no evidence, not as a pass.
            return SoundnessReport()

        probe = "%s\n\n#print axioms %s\n" % (source.rstrip(), parsed.name)
        statement_task = StatementTask(
            task_id=task.task_id,
            informal_statement=task.informal_statement or "",
            formal_statement=task.formal_statement,
            header=task.header,
            language=task.language,
            timeout_s=task.timeout_s,
        )
        result = self.probe(statement_task, probe, timeout_s=self.axiom_audit_timeout_s)

        if result.status in (Status.ERROR, Status.SKIPPED):
            # The audit could not run. Say nothing rather than invent a
            # verdict; the caller still has the regex screen.
            return SoundnessReport()

        log = "%s\n%s" % (result.raw.get("stdout") or "", result.raw.get("stderr") or "")
        axioms = parse_printed_axioms(log, parsed.name)
        if axioms is None:
            return SoundnessReport()
        return audit_axioms(axioms)
