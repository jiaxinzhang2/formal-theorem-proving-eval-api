"""Enforce three dependency layers with AST imports, including absolute and nested imports.
The two APIs may not import each other; benchmark and artifact contracts must be implemented."""

from __future__ import annotations

import pathlib
import ast

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "ftp_eval"

#: Lower number = deeper. A module may import from a strictly lower layer
#: and from its own, never from an equal-or-higher one.
LAYER = {
    "spec": 0,
    "backends": 1,
    "proving": 2,
    "autoformalization": 2,
}

def _modules() -> list[pathlib.Path]:
    return sorted(p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _resolve(module: pathlib.Path, dots: str, tail: str) -> str:
    """The dotted path, relative to ftp_eval, that a relative import names."""
    parts = list(module.relative_to(ROOT).parts[:-1])
    up = len(dots) - 1
    if up:
        parts = parts[: len(parts) - up]
    return ".".join([*parts, tail]) if tail else ".".join(parts)


def _edges() -> list[tuple[str, str, str]]:
    """Every (module, importer layer, imported layer) crossing a boundary."""
    out = []
    for module in _modules():
        parts = module.relative_to(ROOT).parts
        if len(parts) < 2 or parts[0] not in LAYER:
            continue  # a top-level file: cli.py, registry.py, io.py
        source = parts[0]
        tree = ast.parse(module.read_text(encoding="utf-8"))
        targets = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    targets.append(_resolve(module, "." * node.level, node.module or ""))
                elif node.module and node.module.startswith("ftp_eval."):
                    targets.append(node.module.removeprefix("ftp_eval."))
            elif isinstance(node, ast.Import):
                targets.extend(alias.name.removeprefix("ftp_eval.") for alias in node.names if alias.name.startswith("ftp_eval."))
        for target in targets:
            destination = target.split(".")[0]
            if destination not in LAYER or destination == source:
                continue
            out.append(("/".join(parts), source, destination))
    return out


def test_every_import_goes_strictly_downward():
    violations = [
        "%s imports %s/ (layer %d -> %d)" % (module, dst, LAYER[src], LAYER[dst])
        for module, src, dst in _edges()
        if LAYER[dst] >= LAYER[src]
    ]
    assert not violations, "layering broken:\n  " + "\n  ".join(violations)


def test_the_two_apis_do_not_import_each_other():
    # The whole point of splitting them. Grading a contest never asks
    # whether a statement is faithful, and auditing a problem set never
    # asks whether anybody proved it.
    crossings = [
        "%s imports %s/" % (module, dst)
        for module, src, dst in _edges()
        if {src, dst} == {"proving", "autoformalization"}
    ]
    assert not crossings, "the two APIs reference each other:\n  " + "\n  ".join(crossings)


def test_no_catch_all_layer_came_back():
    """`shared/` and `source/` were both folders defined by who used them.

    That is how two APIs ended up importing each other behind one of them.
    A module belongs in the layer that describes *what it is*, or inside
    the single API that uses it -- never in a folder named for a
    relationship, because the relationship changes and the folder does not.
    """
    for name in ("shared", "source", "common", "utils", "core"):
        assert not (ROOT / name).is_dir(), (
            "%s/ is back; put the module in backends/ if the prover layer "
            "needs it, or inside the one API that uses it" % name
        )


@pytest.mark.parametrize("layer", sorted(LAYER))
def test_every_layer_exists_and_is_a_package(layer):
    directory = ROOT / layer
    assert directory.is_dir(), "%s/ is missing" % layer
    assert (directory / "__init__.py").exists(), "%s/ has no __init__.py" % layer


# -- spec/ is a contract, not a description of one --------------------


def test_the_spec_abcs_are_actually_implemented():
    """An ABC nobody subclasses cannot stop anything from drifting.

    Both of these said in their docstrings which class implemented them,
    and neither was subclassed -- so the docstrings were simply false, and
    `spec/` was decoration. Wiring them up immediately surfaced two name
    collisions (`manifest`, `problems`), which is the whole argument for
    having the check.
    """
    from ftp_eval.proving.grading.artifacts import RunDirectory
    from ftp_eval.proving.grading.contest import ProblemSet
    from ftp_eval.spec import ArtifactWriter, Benchmark

    assert issubclass(ProblemSet, Benchmark)
    assert issubclass(RunDirectory, ArtifactWriter)


def test_a_dataclass_field_never_shadows_a_contract_method():
    """The collision that wiring the ABC up revealed.

    ``ProblemSet`` holds ``problems`` (a mapping) and ``manifest`` (a typed
    object) as fields. If the ABC named its iterator ``problems()`` or its
    accessor ``manifest()``, the fields would shadow them and a caller
    would get "'dict' object is not callable" at runtime rather than
    anything a type checker flags.
    """
    from ftp_eval.proving.grading.contest import ProblemSet
    from ftp_eval.spec import Benchmark

    fields = set(ProblemSet.__dataclass_fields__)
    methods = {
        name
        for name in vars(Benchmark)
        if not name.startswith("_") and callable(getattr(Benchmark, name, None))
    }
    clashes = fields & methods
    assert not clashes, "field shadows a contract method: %s" % ", ".join(sorted(clashes))

    problem_set = ProblemSet(problems={"P001": "theorem t : True := by sorry"})
    assert [p.problem_id for p in problem_set.iter_problems()] == ["P001"]
    assert problem_set.problem("P001").content_hash
    assert "language" in problem_set.manifest_fields()
