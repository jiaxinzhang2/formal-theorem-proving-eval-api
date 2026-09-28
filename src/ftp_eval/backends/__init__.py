"""Bundled verifier backends.

Imported lazily through :mod:`ftp_eval.registry` so that a backend whose
optional dependency or toolchain is missing cannot break the others.

* ``mock``  -- deterministic fake prover; the reference implementation.
* ``lean4`` -- Lean 4 via ``lake env lean``.
* ``axle``  -- generic HTTP verification service (Axiom's Axle et al.).

To add your own, see ``docs/adding-a-backend.md``.
"""

from __future__ import annotations

__all__ = ["mock", "lean4", "axle"]
