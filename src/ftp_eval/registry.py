"""Backend discovery.

Backends are looked up by name so that the CLI, config files and eval
scripts can all say ``--backend lean4`` without importing anything
prover-specific. Third-party backends can register themselves either by
calling :func:`register` at import time or by publishing an entry point
in the ``ftp_eval.backends`` group.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Iterator

from .verifier import Verifier

__all__ = ["register", "unregister", "create", "available", "load_entry_points"]

#: name -> zero-arg-constructible factory taking **config
_REGISTRY: dict[str, Callable[..., Verifier]] = {}

#: Backends shipped with this package, imported lazily so that a missing
#: optional dependency in one of them cannot break the others.
_BUILTINS: dict[str, tuple[str, str]] = {
    "mock": ("ftp_eval.backends.mock", "MockVerifier"),
    "lean4": ("ftp_eval.backends.lean4", "Lean4Verifier"),
    "axle": ("ftp_eval.backends.axle", "AxleVerifier"),
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
