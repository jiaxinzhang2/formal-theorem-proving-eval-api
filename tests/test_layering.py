"""The layering, enforced rather than described.

Three layers, and every import must go strictly downward::

    spec/                the contracts
    backends/            talking to a prover, and screening what it is
                         given: types, verifier, mock/lean4/axle, plus
                         soundness screening and the comment scanner it
                         needs
    proving/             API 1 -- does this answer prove this theorem?
    autoformalization/   API 2 -- is this statement faithful? A judge and
                         nothing else; it takes one dataclass from below

This file exists because the rule was broken twice while nobody was
checking. ``proving/verifier.py`` imported statement metrics from
``autoformalization/``, and ``autoformalization/checker.py`` imported the
Verifier from ``proving/`` -- so the "two separate APIs" were a circle. The
second one also hid a bug: metrics computed in the backend layer were
computed on the one code path grading does not use.

There was a fourth layer, ``source/``, holding the comment scanner and the
soundness screen. It was justified by both APIs needing it, and that stopped
being true when the faithfulness check became pure judge -- so it folded
into ``backends/``, where the verifier applies the screen a backend may not
opt out of.

A docstring cannot prevent any of that from happening again. A test can.
"""

from __future__ import annotations

import pathlib
import re

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

#: Indented too, because a function-local import evades the rule just as
#: effectively as a top-level one -- and one did.
IMPORT_RE = re.compile(r"^[ \t]*from (\.+)([\w.]*) import ", re.M)


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
        for match in IMPORT_RE.finditer(module.read_text(encoding="utf-8")):
            target = _resolve(module, match.group(1), match.group(2))
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


def test_source_layer_imports_nothing_else_in_the_package():
    # What makes it safe for everything above to stand on.
    reaching_out = [
        "%s imports %s/" % (module, dst) for module, src, dst in _edges() if src == "source"
    ]
    assert not reaching_out, "source/ reached upward:\n  " + "\n  ".join(reaching_out)


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
