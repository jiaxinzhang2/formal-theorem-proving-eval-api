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
import json
import uuid
import re
import hashlib
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .types import (
    ModuleBuild,
    ModuleSource,
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

from .lean_diagnostics import classify_lean_message, parse_lean_log, parse_printed_axioms
from .comments import strip_comments
from .soundness import audit_axioms
from .verifier import BackendUnavailable, RawVerdict, Verifier, VerifierError

__all__ = [
    "Lean4Verifier",
    "parse_lean_log",
    "classify_lean_message",
    "LeanTheorem",
    "parse_lean_theorem",
    "parse_printed_axioms",
]


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
    text = strip_comments(statement, "lean4").strip()
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


def _normalize_binders(binders: str) -> str:
    """Whitespace-insensitive form, for comparing two binder lists."""
    return re.sub(r"\s+", " ", binders.strip())


#: Tactics tried when asking "is this goal trivially closable?". Cheap and
#: total, so the probe is fast; ``decide``/``norm_num`` are included
#: because a formalization reduced to a closed numeric claim is exactly
#: the degenerate case worth catching.
_TRIVIAL_TACTICS = ("trivial", "rfl", "simp", "decide", "norm_num", "omega", "tauto")

#: Tactics tried when asking "are these hypotheses contradictory?".
_VACUITY_TACTICS = ("omega", "simp_all", "exact absurd rfl (by decide)", "linarith", "tauto")


def _artifact_digest(path: Path) -> str:
    """SHA-256 of one compiled artifact, read in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class _StagedArtifacts:
    """What the grader put on an answer's search path, and whether it still is.

    Lean imports an ``.olean`` without rechecking it against the source it
    was built from, and kernel replay does not help: a forged module is
    internally consistent, it is simply a different problem. An answer that
    can run code while it elaborates -- `#eval` and `initialize` are
    screened, but the screen is syntactic and conservative -- can rewrite
    these files between its own compile and the generated check, and a
    swapped ``Problem.olean`` makes the frozen target say whatever the
    answer wants.

    So the expected digests live here, in the grader's own memory, and the
    files are re-read before anything else compiles against them.
    Detection, not prevention: on a native backend the answer's Lean
    process runs as the grading user, so no file permission is a boundary.
    See :attr:`Lean4Verifier.sandbox`.
    """

    directory: Path
    digests: dict[Path, str] = field(default_factory=dict)

    def record(self, path: Path) -> None:
        self.digests[path] = _artifact_digest(path)

    def copy_in(self, olean: Path, module: ModuleSource) -> None:
        """Place a trusted module's artifact on the search path."""
        destination = self.directory / Path(*module.path_parts).with_suffix(".olean")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(olean, destination)
        self.record(destination)

    def breach(self) -> str:
        """What changed since it was written, or ``""`` when nothing did."""
        for path, digest in self.digests.items():
            if not path.is_file():
                return "%s disappeared" % path.name
            if _artifact_digest(path) != digest:
                return "%s was replaced" % path.name
        # An answer can also *add* a module, shadowing a library one: the
        # staged directory comes first on LEAN_PATH.
        extra = next((p for p in self.directory.rglob("*.olean") if p not in self.digests), None)
        return "%s was added" % extra.name if extra is not None else ""


def _tampering(detail: str, when: str) -> ModuleBuild:
    return ModuleBuild(
        Status.FAILED,
        error_kind=ErrorKind.SOUNDNESS,
        diagnostics=(Diagnostic(
            Severity.ERROR,
            "compiled module artifact tampering: %s %s, so nothing checked "
            "against it is evidence of anything" % (detail, when),
            kind=ErrorKind.SOUNDNESS,
        ),),
    )


def lean_library_prefixes(configuration: str) -> set[str]:
    """Library names a Lake configuration declares, from either syntax.

    Both forms are read from whatever text is handed over, because the
    two backends get it from different places -- the native one reads the
    project's files, the container one `cat`s whichever file the image
    has -- and the shapes are disjoint enough that trying both costs
    nothing. A generated module may not reuse one of these names: it
    would be resolved to the project's own library rather than to the
    staged copy.
    """
    prefixes = set(re.findall(r"\blean_lib\s+([A-Za-z_][A-Za-z0-9_]*)", configuration))
    for block in re.split(r"(?m)^\s*\[\[lean_lib\]\]\s*", configuration)[1:]:
        match = re.search(r'(?m)^\s*name\s*=\s*"([^".]+)', re.split(r"(?m)^\s*\[", block)[0])
        if match:
            prefixes.add(match.group(1))
    return prefixes


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
    #: `lake env lean` as the grading user, with that user's filesystem
    #: and network. Build directories are per-answer, which keeps two
    #: answers apart; it does not keep an answer away from the grader.
    sandbox = "none"
    #: Tampering with the staged modules is detected by digest, and the
    #: source screen refuses the commands that run code at elaboration
    #: time. Neither is a boundary: the answer's Lean process could edit
    #: this package, the Lean toolchain or the run records instead. Use
    #: `lean4-docker` to grade answers you did not write.
    safe = "none"

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
        recheck: bool = False,
        **config: Any,
    ) -> None:
        super().__init__(**config)
        #: Run the kernel axiom audit on accepted proofs. Costs a second
        #: compile per verified attempt and is the strongest soundness
        #: evidence available, so it defaults on.
        self.audit_axioms = audit_axioms
        self.recheck = recheck
        self.axiom_audit_timeout_s = axiom_audit_timeout_s
        raw_dir = project_dir or os.environ.get("FTP_EVAL_LEAN_PROJECT")
        self.project_dir = Path(raw_dir).expanduser().resolve() if raw_dir else None
        self.lake = lake or os.environ.get("FTP_EVAL_LAKE") or "lake"
        self.memory_mb = memory_mb
        self.max_heartbeats = max_heartbeats
        self.extra_args = tuple(extra_args)
        self._module_capabilities: dict[str, bool] = {}
        self.module_support_detail = ""
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
            if proc.returncode:
                return BackendInfo(self.name, self.language, False,
                                   detail="lake env lean failed: %s" % (version or proc.returncode))
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
        for i, line in enumerate(strip_comments(source, "lean4").splitlines()):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped == "prelude" or not stripped:
                if stripped:
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


    # -- ordered multi-module builds ------------------------------------

    #: Probed once, then remembered: None = not asked yet.

    def build_modules(
        self,
        modules: Sequence[ModuleSource],
        *,
        audit_declaration: str = "",
        timeout_s: float = 300.0,
        on_stage: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> ModuleBuild | None:
        """Compile modules in order, each able to import the ones before it.

        Returns ``None`` when this toolchain cannot do it -- see
        :meth:`supports_module_builds`. ``None`` is "did not run": the caller
        must not read it as either verdict.
        """
        info = self.info()
        if not info.available:
            raise BackendUnavailable(info.detail or "lean4 backend unavailable")
        if not modules:
            return None
        self.preflight_modules(modules)
        assert self.project_dir is not None

        root = self.project_dir / ".ftp_eval_build"
        cache_root = root / "cache"
        runs = root / "run"
        runs.mkdir(parents=True, exist_ok=True)
        run_root = runs / ("answer-" + uuid.uuid4().hex)
        run_root.mkdir()
        started = time.monotonic()
        staged = run_root / "modules"
        staged.mkdir()
        search: list[Path] = [staged]
        reused = False

        artifacts = _StagedArtifacts(staged)

        #: What each untrusted module actually imports, read by Lean's own
        #: parser rather than by a regex over the text. The screen reads
        #: `import` lines, and the text a regex reads and the text Lean
        #: compiles can be made to differ; this is the observation a policy
        #: can be enforced against. `None` means it could not be answered.
        imports: dict[str, list[str] | None] = {}
        roots: tuple[str, ...] | None = None
        prelude: tuple[str, ...] = ()

        def breached(when: str) -> ModuleBuild | None:
            detail = artifacts.breach()
            return _tampering(detail, when) if detail else None

        def emit(stage: str, status: str, result: ModuleBuild | None = None) -> None:
            if on_stage is not None:
                on_stage(stage, {"status": status, "payload": result.to_dict() if result else None})

        def finish(outcome: ModuleBuild, stage: str = "") -> ModuleBuild:
            outcome.compile_time_s = time.monotonic() - started
            outcome.reused_cache = reused
            outcome.raw = {**outcome.raw, "failed_stage": stage, "module_imports": dict(imports),
                           "module_sources": {
                m.module: self._with_options(m.source) for m in modules
            }}
            return outcome

        try:
            home = run_root
            for index, module in enumerate(modules):
                if module.cacheable:
                    cache_key = {
                        "module": module.module, "source": self._with_options(module.source),
                        "toolchain": (self.project_dir / "lean-toolchain").read_text(encoding="utf-8") if (self.project_dir / "lean-toolchain").exists() else "",
                        "lake_manifest": (self.project_dir / "lake-manifest.json").read_text(encoding="utf-8") if (self.project_dir / "lake-manifest.json").exists() else "",
                        "extra_args": self.extra_args,
                        "backend_environment": self.cache_environment(),
                        "trusted_dependencies": [(m.module, hashlib.sha256(self._with_options(m.source).encode("utf-8")).hexdigest())
                                                 for m in modules[:index] if m.cacheable],
                    }
                    digest = hashlib.sha256(json.dumps(cache_key, sort_keys=True).encode("utf-8")).hexdigest()[:24]
                    home = cache_root / digest
                    olean = home / Path(*module.path_parts).with_suffix(".olean")
                    if olean.exists() and self._cached_artifact_intact(olean):
                        # Keyed by content, so a hit is the same source, not
                        # merely the same name -- and the artifact still
                        # hashes to what we wrote, so the key describes it.
                        reused = True
                        artifacts.copy_in(olean, module)
                        continue
                else:
                    # Later source files are never written into a directory
                    # previously writable by an untrusted compilation.
                    home = staged

                if not module.cacheable:
                    # Staged before the compile, so the import listing is
                    # read off the same bytes the compile will see.
                    source_path = home / Path(*module.path_parts).with_suffix(".lean")
                    source_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path.write_text(self._with_options(module.source),
                                           encoding="utf-8", newline="\n")
                    if roots is None:
                        roots = self._search_roots(staged, search, timeout_s)
                        prelude = self._prelude_dependencies(staged, search, timeout_s)
                    seen = self.observed_imports(source_path, search, roots, prelude, timeout_s)
                    imports[module.module] = None if seen is None else list(seen)

                breach = breached("before building %s" % module.module)
                if breach is not None:
                    emit("kernel", "failed", breach)
                    return finish(breach, "artifacts")

                outcome = self._build_one(module, home, search if home == staged else [*search, home], timeout_s)
                if outcome is not None:
                    emit("kernel", "not_run" if outcome.status is Status.ERROR else "failed", outcome)
                    return finish(outcome, "kernel")
                olean = home / Path(*module.path_parts).with_suffix(".olean")
                if home != staged:
                    if olean.exists():
                        self._sidecar(olean).write_text(_artifact_digest(olean), encoding="utf-8")
                        artifacts.copy_in(olean, module)
                elif olean.exists():
                    artifacts.record(olean)

            breach = breached("after the last module compiled")
            if breach is not None:
                emit("kernel", "failed", breach)
                return finish(breach, "artifacts")
            emit("kernel", "passed", ModuleBuild(Status.VERIFIED))
            replay = None
            if self.recheck and audit_declaration:
                emit("replay", "running")
                replay = self._replay_last(modules[-1].module, search, timeout_s)
                emit("replay", "passed" if replay.verified else "failed", replay)
                if not replay.verified:
                    return finish(replay, "replay")
            else:
                emit("replay", "not_run")

            if not audit_declaration:
                emit("dependencies", "not_run")
                return finish(ModuleBuild(Status.VERIFIED))

            emit("dependencies", "running")
            audited = self._audit_last(
                modules[-1], home,
                search, audit_declaration, timeout_s,
            )
            # The audit is a fresh `lean` process over the generated check
            # module, which imports the answer's `.olean`. Lean runs an
            # imported module's `initialize` blocks, so that import is the
            # last moment answer-supplied code can execute -- check again
            # before reading the listing it printed.
            breach = breached("while the dependency listing was produced")
            if breach is not None:
                emit("dependencies", "failed", breach)
                return finish(breach, "artifacts")
            if audited.verified and audited.axioms is None and audit_declaration:
                audited.status = Status.ERROR
                audited.diagnostics += (Diagnostic(Severity.ERROR, "axiom dependency listing missing"),)
            emit("dependencies", "passed" if audited.verified else "failed", audited)
            if replay is not None:
                audited.raw = {**audited.raw, "replay": replay.to_dict()}
            return finish(audited, "" if audited.verified else "dependencies")
        finally:
            if not self.keep_sources:
                shutil.rmtree(run_root, ignore_errors=True)

    def cache_environment(self) -> Mapping[str, Any]:
        """Runtime identity beyond the project's pinned manifest."""
        return {"lean_version": self.info().version}

    def _replay_last(self, module: str, search: Sequence[Path], timeout_s: float) -> ModuleBuild:
        """Replay compiled declarations using the toolchain's kernel checker.

        This is a separate process, not a second execution of answer tactics.
        It uses the official kernel; it is not an independent kernel implementation.
        """
        proc = self._run_tool("leanchecker", ["--fresh", module], search, timeout_s)
        if proc is None:
            return ModuleBuild(Status.TIMEOUT, failed_module=module, error_kind=ErrorKind.TIMEOUT)
        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return ModuleBuild(Status.VERIFIED if proc.returncode == 0 else Status.FAILED,
                           failed_module=module if proc.returncode else "",
                           diagnostics=tuple(parse_lean_log(log)),
                           raw={"checker": "leanchecker", "fresh": True,
                                "exit_code": proc.returncode, "log": log})

    def library_prefixes(self) -> set[str]:
        if self.project_dir is None:
            return set()
        prefixes: set[str] = set()
        for name in ("lakefile.toml", "lakefile.lean"):
            path = self.project_dir / name
            if path.exists():
                prefixes |= lean_library_prefixes(path.read_text(encoding="utf-8"))
        return prefixes

    def preflight_modules(self, modules: Sequence[ModuleSource]) -> None:
        prefixes = {module.path_parts[0] for module in modules}
        conflicts = prefixes & (self.library_prefixes() | {"Lean", "Init", "Std", "Mathlib", "Aesop", "Batteries"})
        if conflicts:
            raise BackendUnavailable("generated module prefix conflicts with a project lean_lib or trusted library: %s; "
                                     "choose FtpEvalBench and update the answer import" % ", ".join(sorted(conflicts)))
        for prefix in sorted(prefixes):
            if not self.supports_module_builds(module_prefix=prefix):
                raise BackendUnavailable(self.module_support_detail or "module imports unavailable for prefix " + prefix)

    def supports_module_builds(self, *, module_prefix: str = "FtpEvalBench") -> bool:
        """Whether an inherited LEAN_PATH actually reaches ``lake env lean``.

        Probed rather than assumed, with a throwaway pair: a module declaring
        one constant, and a second importing it. If the import does not
        resolve, multi-module grading is unavailable on this toolchain and
        says so, instead of reporting every answer as failing to compile.
        """
        if not self.info().available or self.project_dir is None:
            self.module_support_detail = "backend unavailable; module capability was not probed"
            return False
        if module_prefix in self.library_prefixes():
            self.module_support_detail = "module prefix %s conflicts with project lean_lib" % module_prefix
            return False
        if module_prefix in self._module_capabilities:
            return self._module_capabilities[module_prefix]
        probe_root = self.project_dir / ".ftp_eval_build" / ("probe-" + uuid.uuid4().hex)
        try:
            name = module_prefix + ".Probe" + uuid.uuid4().hex
            base = ModuleSource(name, "def ftpEvalProbe : Nat := 1\n")
            top = ModuleSource(
                name + ".Check",
                "import %s\n\nexample : ftpEvalProbe = 1 := rfl\n" % name,
            )
            failed = self._build_one(base, probe_root, [probe_root], 120.0)
            if failed is None:
                failed = self._build_one(top, probe_root, [probe_root], 120.0)
            self._module_capabilities[module_prefix] = failed is None
            self.module_support_detail = "" if failed is None else (
                "module import probe failed for prefix %s: %s" % (module_prefix, failed.raw.get("log", failed.status.value)))
        except Exception as exc:  # pragma: no cover - a broken toolchain is "no"
            self._module_capabilities[module_prefix] = False
            self.module_support_detail = "module probe failed for %s: %s" % (module_prefix, exc)
        finally:
            shutil.rmtree(probe_root, ignore_errors=True)
        return self._module_capabilities[module_prefix]

    #: Module prefix for the probe that makes Lean print its search path.
    _PROBE = "FtpEvalSearchPathProbe"

    @contextmanager
    def _probe(self, home: Path, suffix: str, source: str) -> Iterator[Path]:
        """A throwaway module beside the staged ones, removed afterwards.

        It has to live on the search path rather than in a temp directory:
        `lake env lean` refuses an input outside the project root, and the
        answers for both probes depend on the search path being the one
        the real compile will use.
        """
        path = home / (self._PROBE + suffix + ".lean")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="\n")
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    def _search_roots(self, home: Path, search: Sequence[Path], timeout_s: float) -> tuple[str, ...]:
        """Every directory Lean resolves imports against, asked of Lean.

        An import that cannot resolve makes Lean print its own search path,
        and that is the only list guaranteed to be the one the real imports
        used. Lake's build layout has moved between releases -- on 4.29 it
        is `.lake/build/lib/lean`, with a segment that was not there before
        -- so deriving it from the project directory would be a guess that
        fails quietly, in the direction of calling a module unnameable.
        """
        with self._probe(home, "", "import %s.Missing\n" % self._PROBE) as probe:
            proc = self._run_lean(["--deps", str(probe)], search, timeout_s)
        if proc is None:
            return ()
        _, found, tail = ("%s\n%s" % (proc.stdout or "", proc.stderr or "")).partition(
            "search path entries:")
        if not found:
            return ()
        roots: list[str] = []
        for line in tail.splitlines():
            entry = line.strip()
            if not entry:
                if roots:
                    break
                continue
            roots.append(entry)
        return tuple(roots)

    def _dependencies(self, path: Path, search: Sequence[Path], timeout_s: float) -> tuple[str, ...] | None:
        """Olean paths this source file imports, read by Lean's own parser.

        ``None`` when the question could not be answered, which upstream
        must not read as "it imports nothing". Direct imports only, which
        is what an allowlist is about.
        """
        proc = self._run_lean(["--deps", str(path)], search, timeout_s)
        if proc is None or proc.returncode != 0:
            return None
        return tuple(line.strip() for line in (proc.stdout or "").splitlines() if line.strip())

    @staticmethod
    def _module_name(dependency: str, roots: Sequence[str]) -> str:
        """The module name an olean path stands for, given the search path.

        A path under no known root comes back marked rather than guessed
        at: an unnameable import is one an allowlist cannot clear, and
        that is the direction a missing root should fail in.
        """
        target = os.path.normpath(dependency)
        # Case folding is for *comparing* paths, never for the name: a
        # Windows root arrives as `e:\...` where the dependency says
        # `E:\...`, and a lowercased `Lean` would quietly stop matching
        # the allowlist it is supposed to be checked against.
        folded = os.path.normcase(target)
        if len(folded) != len(target):
            return "?" + dependency
        best = ""
        for root in roots:
            prefix = os.path.normcase(os.path.normpath(root))
            if folded.startswith(prefix + os.sep) and len(prefix) > len(best):
                best = prefix
        if not best:
            return "?" + dependency
        relative = target[len(best) + 1:]
        stem = relative[: -len(".olean")] if relative.endswith(".olean") else relative
        return stem.replace(os.sep, ".").replace("/", ".")

    def _prelude_dependencies(self, home: Path, search: Sequence[Path], timeout_s: float) -> tuple[str, ...]:
        """What Lean imports on its own, with nothing written in the file.

        The prelude is auto-imported, so it appears in every listing.
        Subtracting the empty file's listing leaves exactly what the
        answer asked for, which is what the policy is written about --
        and it does so without this code having to know the prelude's
        name, which is the kind of thing that changes.
        """
        with self._probe(home, "Prelude", "\n") as probe:
            return self._dependencies(probe, search, timeout_s) or ()

    def observed_imports(self, path: Path, search: Sequence[Path], roots: Sequence[str],
                         prelude: Sequence[str], timeout_s: float) -> tuple[str, ...] | None:
        """Module names one staged source imports, minus the implicit prelude."""
        dependencies = self._dependencies(path, search, timeout_s)
        if dependencies is None:
            return None
        implicit = set(prelude)
        names = [self._module_name(d, roots) for d in dependencies if d not in implicit]
        return tuple(dict.fromkeys(names))

    @staticmethod
    def _sidecar(olean: Path) -> Path:
        return olean.with_name(olean.name + ".sha256")

    def _cached_artifact_intact(self, olean: Path) -> bool:
        """Whether a cached artifact still hashes to what was written beside it.

        The cache key decides the *path*; until this existed nothing re-read
        the file, so a hit was "a file of that name is present". One answer
        able to write to disk could leave a forged `.olean` in the cache and
        every later answer -- in this process or a later run -- would import
        it as the frozen problem, with the poisoner's own record showing an
        ordinary failure and the beneficiary's showing a clean proof.

        A mismatch is treated as a miss rather than an error: the module is
        recompiled from its trusted source, which repairs the entry. An entry
        written before sidecars existed has none and is recompiled once.
        """
        try:
            expected = self._sidecar(olean).read_text(encoding="utf-8").strip()
        except OSError:
            return False
        return bool(expected) and expected == _artifact_digest(olean)

    def _build_one(
        self,
        module: ModuleSource,
        home: Path,
        search: Sequence[Path],
        timeout_s: float,
    ) -> ModuleBuild | None:
        """Compile one module into ``home``. None means it compiled cleanly."""
        path = home / Path(*module.path_parts).with_suffix(".lean")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._with_options(module.source), encoding="utf-8", newline="\n")
        olean = path.with_suffix(".olean")

        proc = self._run_lean([str(path), "-o", str(olean)], search, timeout_s)
        if proc is None:
            return ModuleBuild(
                Status.TIMEOUT,
                failed_module=module.module,
                error_kind=ErrorKind.TIMEOUT,
                diagnostics=(
                    Diagnostic(
                        Severity.ERROR,
                        "building %s exceeded %.0fs" % (module.module, timeout_s),
                        kind=ErrorKind.TIMEOUT,
                    ),
                ),
            )
        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        diagnostics = tuple(parse_lean_log(log))
        errors = [d for d in diagnostics if d.severity is Severity.ERROR]
        if proc.returncode != 0 or errors:
            return ModuleBuild(
                Status.FAILED,
                failed_module=module.module,
                diagnostics=diagnostics,
                error_kind=errors[0].kind if errors else ErrorKind.UNKNOWN,
                raw={"exit_code": proc.returncode, "log": log},
            )
        return None

    def _audit_last(
        self,
        module: ModuleSource,
        home: Path,
        search: Sequence[Path],
        declaration: str,
        timeout_s: float,
    ) -> ModuleBuild:
        """Re-run the final module to read its ``#print axioms`` listing.

        A separate run because the listing goes to stdout, and the build that
        produced the ``.olean`` may have been served from cache. Without a
        listing ``axioms`` stays ``None``, which upstream reads as "the audit
        did not run" -- never as "no axioms".
        """
        if not declaration:
            return ModuleBuild(Status.VERIFIED, axioms=None)
        path = home / Path(*module.path_parts).with_suffix(".lean")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._with_options(module.source), encoding="utf-8", newline="\n")
        proc = self._run_lean([str(path)], search, timeout_s)
        if proc is None:
            return ModuleBuild(
                Status.TIMEOUT, failed_module=module.module, error_kind=ErrorKind.TIMEOUT
            )
        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        diagnostics = tuple(parse_lean_log(log))
        if proc.returncode != 0 or any(d.severity is Severity.ERROR for d in diagnostics):
            return ModuleBuild(Status.FAILED, failed_module=module.module,
                               diagnostics=diagnostics, raw={"exit_code": proc.returncode, "log": log})
        listing = parse_printed_axioms(log, declaration)
        return ModuleBuild(
            Status.VERIFIED,
            axioms=None if listing is None else tuple(listing),
            diagnostics=tuple(parse_lean_log(log)),
            raw={"log": log},
        )

    def _run_lean(
        self, arguments: Sequence[str], search: Sequence[Path], timeout_s: float
    ) -> Any:
        """``lake env lean`` with the build directories on LEAN_PATH.

        ``lake env`` adds the project's own paths; the inherited LEAN_PATH is
        how the staged modules get found. Whether that inheritance works is
        exactly what :meth:`supports_module_builds` probes, so this method
        never has to assume it.
        """
        return self._run_tool("lean", arguments, search, timeout_s)

    def _run_tool(
        self, tool: str, arguments: Sequence[str], search: Sequence[Path], timeout_s: float
    ) -> Any:
        assert self.project_dir is not None
        cmd = [self._lake_path(), "env", tool]
        if tool == "lean":
            if self.memory_mb:
                cmd.append("--memory=%d" % self.memory_mb)
            cmd.extend(self.extra_args)
        cmd.extend(arguments)

        env = dict(os.environ)
        existing = env.get("LEAN_PATH", "")
        staged = os.pathsep.join(str(d) for d in search)
        env["LEAN_PATH"] = (staged + os.pathsep + existing) if existing else staged
        try:
            return subprocess.run(
                cmd,
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return None

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
            seam = _find_seam(strip_comments(statement, "lean4"))
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
