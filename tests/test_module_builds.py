"""Isolated module builds, cache identity and audit process failures."""
from types import SimpleNamespace
from pathlib import Path

import pytest

from ftp_eval.backends.lean4 import Lean4Verifier
from ftp_eval.backends.types import BackendInfo, ModuleBuild, ModuleSource, Status

def backend(tmp_path, monkeypatch):
    verifier = Lean4Verifier(project_dir=tmp_path)
    monkeypatch.setattr(verifier, "info", lambda: BackendInfo("lean4", "lean4", True))
    monkeypatch.setattr(verifier, "supports_module_builds", lambda: True)
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

@pytest.mark.parametrize("name", ["../outside", ".", "A..B", "A/B", "C:\\unsafe"])
def test_module_names_cannot_escape_build_root(name):
    with pytest.raises(ValueError, match="module name"):
        ModuleSource(name, "def x := 1")
