"""Backend discovery.

Backends are looked up by name so that the CLI, config files and eval
scripts can all say ``--backend lean4`` without importing anything
prover-specific. Third-party backends can register themselves either by
calling :func:`register` at import time or by publishing an entry point
in the ``ftp_eval.backends`` group.

That group name is deliberately short and does not track the internal
module layout: it is a public contract that third-party packages write
into their own metadata, so it should not move when this package is
reorganized.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Iterator

from .proving.verifier import Verifier

__all__ = [
    "register",
    "unregister",
    "create",
    "available",
    "load_entry_points",
    "register_judge",
    "create_judge",
    "available_judges",
]

#: name -> zero-arg-constructible factory taking **config
_REGISTRY: dict[str, Callable[..., Verifier]] = {}

#: Backends shipped with this package, imported lazily so that a missing
#: optional dependency in one of them cannot break the others.
_BUILTINS: dict[str, tuple[str, str]] = {
    "mock": ("ftp_eval.proving.backends.mock", "MockVerifier"),
    "lean4": ("ftp_eval.proving.backends.lean4", "Lean4Verifier"),
    "axle": ("ftp_eval.proving.backends.axle", "AxleVerifier"),
}

_entry_points_loaded = False


def register(name: str, factory: Callable[..., Verifier], *, overwrite: bool = False) -> None:
    """Make ``name`` resolvable by :func:`create`."""
    if not overwrite and name in _REGISTRY:
        raise ValueError("backend %r is already registered" % name)
    _REGISTRY[name] = factory


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def load_entry_points() -> None:
    """Import third-party backends advertised via entry points.

    Failures are swallowed on purpose: a broken plugin should not stop
    you from running the backend you actually asked for.
    """
    global _entry_points_loaded
    if _entry_points_loaded:
        return
    _entry_points_loaded = True
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover - Python < 3.8
        return
    try:
        eps = entry_points(group="ftp_eval.backends")
    except TypeError:  # pragma: no cover - older selectable API
        eps = entry_points().get("ftp_eval.backends", ())  # type: ignore[attr-defined]
    for ep in eps:
        if ep.name in _REGISTRY:
            continue
        try:
            register(ep.name, ep.load())
        except Exception:
            continue


def _resolve(name: str) -> Callable[..., Verifier]:
    if name in _REGISTRY:
        return _REGISTRY[name]
    if name in _BUILTINS:
        module_name, attr = _BUILTINS[name]
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise ImportError("backend %r failed to import: %s" % (name, exc)) from exc
        factory = getattr(module, attr)
        _REGISTRY[name] = factory
        return factory
    load_entry_points()
    if name in _REGISTRY:
        return _REGISTRY[name]
    known = ", ".join(sorted(set(_REGISTRY) | set(_BUILTINS)))
    raise KeyError("unknown backend %r; known backends: %s" % (name, known))


def create(name: str, **config: Any) -> Verifier:
    """Instantiate a backend by name, passing ``config`` to its factory."""
    return _resolve(name)(**config)


def available() -> Iterator[str]:
    """All backend names that can be resolved, builtin or registered."""
    load_entry_points()
    return iter(sorted(set(_REGISTRY) | set(_BUILTINS)))


# ---------------------------------------------------------------------
# Judges
#
# A separate namespace from backends on purpose: a backend answers "does
# this proof close this goal?" and a judge answers "does this goal say
# what the problem said?". Conflating them would make `--backend claude`
# look like a prover, which it is not.
# ---------------------------------------------------------------------

_JUDGES: dict[str, Callable[..., Any]] = {}

_BUILTIN_JUDGES: dict[str, tuple[str, str]] = {
    "mock": ("ftp_eval.formalizing.judge", "MockJudge"),
    "claude": ("ftp_eval.formalizing.judges.claude", "ClaudeJudge"),
}


def register_judge(name: str, factory: Callable[..., Any], *, overwrite: bool = False) -> None:
    if not overwrite and name in _JUDGES:
        raise ValueError("judge %r is already registered" % name)
    _JUDGES[name] = factory


def create_judge(name: str, *, consensus: int = 1, recheck: int = 2, **config: Any) -> Any:
    """Instantiate a judge, optionally wrapped in consensus voting.

    ``consensus > 1`` wraps it in
    :class:`~ftp_eval.formalizing.judge.ConsensusJudge`, which votes across samples
    and re-examines its own rejections before letting one stand.
    """
    if name in _JUDGES:
        factory = _JUDGES[name]
    elif name in _BUILTIN_JUDGES:
        module_name, attr = _BUILTIN_JUDGES[name]
        try:
            factory = getattr(importlib.import_module(module_name), attr)
        except Exception as exc:
            raise ImportError("judge %r failed to import: %s" % (name, exc)) from exc
        _JUDGES[name] = factory
    else:
        known = ", ".join(sorted(set(_JUDGES) | set(_BUILTIN_JUDGES)))
        raise KeyError("unknown judge %r; known judges: %s" % (name, known))

    judge = factory(**config)
    if consensus > 1:
        from .formalizing.judge import ConsensusJudge

        return ConsensusJudge(judge, samples=consensus, recheck_rejections=recheck)
    return judge


def available_judges() -> Iterator[str]:
    return iter(sorted(set(_JUDGES) | set(_BUILTIN_JUDGES)))
