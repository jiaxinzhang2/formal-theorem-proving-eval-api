"""Isolated module builds, cache identity and audit process failures."""
from types import SimpleNamespace
from pathlib import Path

import pytest

from ftp_eval.backends.lean4 import Lean4Verifier
from ftp_eval.backends.types import BackendInfo, ModuleBuild, ModuleSource, Status


@pytest.mark.parametrize("header", [
    "/- provenance\n /- nested -/\n-/\n",
    "-- provenance\n\n",
    "prelude\n/- metadata -/\n",
])
def test_resource_options_follow_imports_after_metadata(tmp_path, header):
    verifier = Lean4Verifier(project_dir=tmp_path)
    source = header + "import Std\n/- between imports -/\nimport Init\nnamespace Problem\nend Problem\n"
    capped = verifier._with_options(source)
    assert capped.index("set_option maxHeartbeats") > capped.index("import Init")
    assert capped.index("set_option maxHeartbeats") < capped.index("namespace Problem")
    assert capped.startswith(header)

def backend(tmp_path, monkeypatch):
    verifier = Lean4Verifier(project_dir=tmp_path)
    monkeypatch.setattr(verifier, "info", lambda: BackendInfo("lean4", "lean4", True))
    monkeypatch.setattr(verifier, "supports_module_builds", lambda **kwargs: True)
    # These tests simulate compilation and artifacts without a Lean toolchain.
    # Import discovery also invokes Lean, so keep it inside the same mock boundary.
    monkeypatch.setattr(verifier, "_search_roots", lambda *args: ())
    monkeypatch.setattr(verifier, "_prelude_dependencies", lambda *args: ())
    monkeypatch.setattr(verifier, "observed_imports", lambda *args: ())
    return verifier

def test_identical_answers_use_unique_build_directories(tmp_path, monkeypatch):
    verifier = backend(tmp_path, monkeypatch)
    roots = []
    monkeypatch.setattr(verifier, "_build_one", lambda *args: None)
    def audit(module, home, *args):
        roots.append(home)
        return ModuleBuild(Status.VERIFIED, axioms=())
    monkeypatch.setattr(verifier, "_audit_last", audit)
    modules = [ModuleSource("Answer", "def x := 1")]
    verifier.build_modules(modules, audit_declaration="x")
    verifier.build_modules(modules, audit_declaration="x")
    assert roots[0] != roots[1]
    assert all(not path.exists() for path in roots)

def test_cache_accounts_for_name_toolchain_and_options(tmp_path, monkeypatch):
    verifier = backend(tmp_path, monkeypatch)
    homes = []
    def build(module, home, *args):
        if module.cacheable:
            homes.append(home)
            path = home / Path(*module.path_parts).with_suffix(".olean")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"compiled")
        return None
    monkeypatch.setattr(verifier, "_build_one", build)
    monkeypatch.setattr(verifier, "_audit_last", lambda *args: ModuleBuild(Status.VERIFIED, axioms=()))
    def run(name="Problem"):
        return verifier.build_modules([ModuleSource(name, "def x := 1", cacheable=True), ModuleSource("Answer", "def y := 1")])
    run()
    assert run().reused_cache
    run("Other")
    (tmp_path / "lean-toolchain").write_text("vNext")
    run()
    verifier.max_heartbeats = 1
    run()
    assert len(set(homes)) == 4

def test_audit_subprocess_failure_is_not_verified(tmp_path, monkeypatch):
    verifier = backend(tmp_path, monkeypatch)
    monkeypatch.setattr(verifier, "_run_lean", lambda *args: SimpleNamespace(returncode=1, stdout="", stderr="failed"))
    result = verifier._audit_last(ModuleSource("Check", "#print axioms missing"), tmp_path, [], "missing", 1.0)
    assert not result.verified
    assert result.status is Status.FAILED


def test_replay_failure_stops_before_dependency_extraction(tmp_path, monkeypatch):
    verifier = backend(tmp_path, monkeypatch)
    verifier.recheck = True
    monkeypatch.setattr(verifier, "_build_one", lambda *args: None)
    monkeypatch.setattr(verifier, "_replay_last", lambda *args: ModuleBuild(Status.FAILED))
    monkeypatch.setattr(verifier, "_audit_last", lambda *args: pytest.fail("must not audit a failed replay"))
    events = []
    result = verifier.build_modules([ModuleSource("Check", "theorem target : True := trivial")],
                                    audit_declaration="target", on_stage=lambda stage, event: events.append((stage, event["status"])))
    assert result.raw["failed_stage"] == "replay"
    assert events == [("kernel", "passed"), ("replay", "running"), ("replay", "failed")]


def test_goal_cache_tracks_trusted_problem_source(tmp_path, monkeypatch):
    verifier = backend(tmp_path, monkeypatch)
    goal_homes = []
    def build(module, home, *args):
        path = home / Path(*module.path_parts).with_suffix(".olean")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"compiled")
        if module.module == "Goal":
            goal_homes.append(home)
        return None
    monkeypatch.setattr(verifier, "_build_one", build)
    for value in ("1", "2"):
        verifier.build_modules([ModuleSource("Problem", "def n := " + value, cacheable=True),
                                ModuleSource("Goal", "import Problem\ndef goal := n", cacheable=True)])
    assert goal_homes[0] != goal_homes[1]


def test_capability_probe_uses_real_prefix_and_nested_module_shape(tmp_path, monkeypatch):
    verifier = Lean4Verifier(project_dir=tmp_path)
    monkeypatch.setattr(verifier, "info", lambda: BackendInfo("lean4", "lean4", True))
    modules = []
    monkeypatch.setattr(verifier, "_build_one", lambda module, *args: modules.append(module) or None)
    assert verifier.supports_module_builds(module_prefix="ActualPrefix")
    assert len(modules[0].path_parts) == 2
    assert len(modules[1].path_parts) == 3
    assert modules[0].path_parts[0] == "ActualPrefix"
    assert modules[1].source.startswith("import " + modules[0].module)


def test_capability_never_probes_an_unavailable_backend(tmp_path, monkeypatch):
    verifier = Lean4Verifier(project_dir=tmp_path)
    monkeypatch.setattr(verifier, "info", lambda: BackendInfo("lean4", "lean4", False))
    monkeypatch.setattr(verifier, "_build_one", lambda *args: pytest.fail("unavailable toolchain"))
    assert not verifier.supports_module_builds()


def test_generated_module_prefix_cannot_collide_with_project_library(tmp_path):
    (tmp_path / "lakefile.toml").write_text('name = "project"\n[[lean_lib]]\nname = "Bench"\n')
    verifier = Lean4Verifier(project_dir=tmp_path)
    with pytest.raises(RuntimeError, match="conflicts.*lean_lib"):
        verifier.preflight_modules([ModuleSource("Bench.P001", "def x := 1")])

@pytest.mark.parametrize("name", ["../outside", ".", "A..B", "A/B", "C:\\unsafe"])
def test_module_names_cannot_escape_build_root(name):
    with pytest.raises(ValueError, match="module name"):
        ModuleSource(name, "def x := 1")


# -- the staged artifacts an answer compiles against -------------------
#
# Lean imports an `.olean` without rechecking it against the source it was
# built from, and kernel replay does not help: a forged module is
# internally consistent, it is simply a different problem. So the grader
# records what it wrote and re-reads it before anything else compiles.


def staged_olean(home, name="Problem"):
    return next(home.rglob(name + ".olean"))


def tampering_run(tmp_path, monkeypatch, sabotage):
    """Build Problem, then let the answer's compile run ``sabotage``."""
    verifier = backend(tmp_path, monkeypatch)
    modules = [ModuleSource("Problem", "def x := 1", cacheable=True),
               ModuleSource("Answer", "def y := 1")]

    def build(module, home, *args):
        path = home / Path(*module.path_parts).with_suffix(".olean")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"compiled " + module.module.encode())
        if not module.cacheable:
            sabotage(home)
        return None

    monkeypatch.setattr(verifier, "_build_one", build)
    monkeypatch.setattr(verifier, "_audit_last", lambda *args: ModuleBuild(Status.VERIFIED, axioms=()))
    return verifier.build_modules(modules, audit_declaration="x")


def test_a_replaced_trusted_artifact_refuses_the_answer(tmp_path, monkeypatch):
    def swap(staged):
        staged_olean(staged).write_bytes(b"forged Problem")
    build = tampering_run(tmp_path, monkeypatch, swap)
    assert not build.verified
    assert "tampering" in build.diagnostics[0].message


def test_a_deleted_trusted_artifact_refuses_the_answer(tmp_path, monkeypatch):
    build = tampering_run(tmp_path, monkeypatch, lambda staged: staged_olean(staged).unlink())
    assert not build.verified
    assert "disappeared" in build.diagnostics[0].message


def test_an_added_module_refuses_the_answer(tmp_path, monkeypatch):
    """The staged directory comes first on LEAN_PATH, so a module an
    answer drops there shadows the library module of the same name."""
    def smuggle(staged):
        (staged / "Mathlib").mkdir(parents=True, exist_ok=True)
        (staged / "Mathlib" / "Tactic.olean").write_bytes(b"shadow")
    build = tampering_run(tmp_path, monkeypatch, smuggle)
    assert not build.verified
    assert "was added" in build.diagnostics[0].message


def test_an_untouched_build_is_not_reported_as_tampering(tmp_path, monkeypatch):
    assert tampering_run(tmp_path, monkeypatch, lambda staged: None).verified


def test_a_rewritten_cache_entry_is_recompiled_rather_than_trusted(tmp_path, monkeypatch):
    """One answer able to write to disk could otherwise leave a forged
    `.olean` in the shared cache, and every later answer -- in this
    process or a later run -- would import it as the frozen problem."""
    verifier = backend(tmp_path, monkeypatch)
    compiled = []

    def build(module, home, *args):
        compiled.append(module.module)
        path = home / Path(*module.path_parts).with_suffix(".olean")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"honest " + module.module.encode())
        return None

    monkeypatch.setattr(verifier, "_build_one", build)
    monkeypatch.setattr(verifier, "_audit_last", lambda *args: ModuleBuild(Status.VERIFIED, axioms=()))
    modules = [ModuleSource("Problem", "def x := 1", cacheable=True),
               ModuleSource("Answer", "def y := 1")]

    verifier.build_modules(modules, audit_declaration="x")
    assert verifier.build_modules(modules, audit_declaration="x").reused_cache

    cached = staged_olean(tmp_path / ".ftp_eval_build" / "cache")
    cached.write_bytes(b"forged Problem")
    compiled.clear()
    build_result = verifier.build_modules(modules, audit_declaration="x")
    assert "Problem" in compiled, "a rewritten cache entry must not be served"
    assert build_result.verified
    assert cached.read_bytes() == b"honest Problem"


def test_a_swapped_frozen_target_is_refused_in_real_lean(tmp_path):
    """The whole chain, against a real toolchain, past the text screen.

    The target is false, so any accepted answer is a defect rather than a
    hard problem. The answer proves `True` and rewrites the staged
    `Problem.olean` while it elaborates; before the digest check the
    generated check module imported the forged target, the kernel agreed
    and the axiom listing came back clean.
    """
    import os
    project = os.environ.get("FTP_EVAL_LEAN_PROJECT")
    if not project:
        pytest.skip("set FTP_EVAL_LEAN_PROJECT to a built Std-capable Lake project")
    verifier = Lean4Verifier(project_dir=project, lake=os.environ.get("FTP_EVAL_LAKE", "lake"))
    if not verifier.info().available:
        pytest.skip("Lean toolchain unavailable")

    swap = (
        '#eval show IO Unit from do\n'
        '  let raw := (<- IO.getEnv "LEAN_PATH").getD ""\n'
        '  let parts := (raw.splitOn ":") ++ (raw.splitOn ";")\n'
        '  let staged := (parts.filter (fun e => (e.splitOn ".ftp_eval_build").length > 1)).head!\n'
        '  let forge := staged ++ "/forge"\n'
        '  IO.FS.createDirAll (forge ++ "/FtpEvalBench")\n'
        '  IO.FS.writeFile (forge ++ "/FtpEvalBench/P001.lean")\n'
        '    "namespace Problem\nabbrev Target : Prop := True\nend Problem\n"\n'
        '  let _ <- IO.Process.output\n'
        '    { cmd := "lean", args := #["FtpEvalBench/P001.lean", "-o", "out.olean"], cwd := forge }\n'
        '  let victim := staged ++ "/FtpEvalBench/P001.olean"\n'
        '  try IO.FS.rename victim (victim ++ ".bak") catch _ => pure ()\n'
        '  try IO.FS.rename (forge ++ "/out.olean") victim catch _ => pure ()\n'
    )
    modules = [
        ModuleSource("FtpEvalBench.P001",
                     "namespace Problem\nabbrev Target : Prop := 2 + 2 = 5\nend Problem\n",
                     cacheable=True),
        ModuleSource("FtpEvalBench.Answer",
                     "import FtpEvalBench.P001\n%snamespace Submission\n"
                     "theorem solution : True := trivial\nend Submission\n" % swap),
        ModuleSource("FtpEvalBench.Answer.Check",
                     "import FtpEvalBench.P001\nimport FtpEvalBench.Answer\n"
                     "theorem ftp_eval_target : _root_.Problem.Target :=\n"
                     "  Submission.solution\n#print axioms ftp_eval_target\n"),
    ]
    build = verifier.build_modules(modules, audit_declaration="ftp_eval_target")
    assert build is not None and not build.verified
    assert any("tampering" in d.message for d in build.diagnostics)


def test_imports_are_read_from_lean_not_from_the_text(tmp_path):
    """What the module imported, named exactly, against a real toolchain.

    The answer's `import Lean` does not begin its line, which is how it
    stayed out of the text reader's view. The listing also has to come
    back with the prelude subtracted and the names cased as written, or
    the allowlist it feeds would not match anything.
    """
    import os
    project = os.environ.get("FTP_EVAL_LEAN_PROJECT")
    if not project:
        pytest.skip("set FTP_EVAL_LEAN_PROJECT to a built Std-capable Lake project")
    verifier = Lean4Verifier(project_dir=project, lake=os.environ.get("FTP_EVAL_LAKE", "lake"))
    if not verifier.info().available:
        pytest.skip("Lean toolchain unavailable")

    modules = [
        ModuleSource("FtpEvalBench.P001",
                     "namespace Problem\nabbrev Target : Prop := 2 + 2 = 4\nend Problem\n",
                     cacheable=True),
        ModuleSource("FtpEvalBench.Answer",
                     "import FtpEvalBench.P001\n/- note -/ import Lean\n"
                     "namespace Submission\ntheorem solution : Problem.Target := by decide\n"
                     "end Submission\n"),
    ]
    build = verifier.build_modules(modules)
    assert build is not None
    assert build.raw["module_imports"]["FtpEvalBench.Answer"] == ["FtpEvalBench.P001", "Lean"]
