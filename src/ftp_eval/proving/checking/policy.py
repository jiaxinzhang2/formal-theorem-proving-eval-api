"""Benchmark decisions about allowed imports, axioms, instances and required replay."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from ...backends.soundness import DEFAULT_ALLOWED_IMPORTS, LEGITIMATE_AXIOMS, HackClass, SoundnessReport, audit_axioms, label


@dataclass(frozen=True)
class ContestPolicy:
    """Where "what counts as a proof" is a decision rather than a fact.

    Four knobs. Each one is a place a setter could reasonably choose
    differently, so each is data with its argument attached rather than a
    default buried in the code.
    """

    #: Module prefixes an answer may import, besides the problem's own.
    #: An unlisted import is refused because a self-supplied module can
    #: carry its own axioms -- the whole audit is worthless if the answer
    #: can bring a file that declares `axiom cheat : False`.
    allowed_imports: tuple[str, ...] = DEFAULT_ALLOWED_IMPORTS

    #: Axioms a proof may depend on. The three defaults are Lean's own
    #: classical foundations, which essentially all of Mathlib rests on;
    #: refusing them would refuse mathematics. Anything else in the listing
    #: was introduced by the answer, and a declared axiom passes the kernel
    #: with no complaint at all -- which is why this listing, not the
    #: kernel's verdict, is the real check.
    allowed_axioms: frozenset[str] = LEGITIMATE_AXIOMS

    #: Whether an answer may declare file-level `instance`s.
    #:
    #: Allowed, and that is a consequence of freezing the target rather
    #: than a concession. An instance can change how a proposition
    #: *elaborates*, which is why it had to be policed when the target was
    #: re-elaborated alongside the answer. `Problem.Target` is elaborated
    #: inside a sealed module, so an answer's instances can only help it
    #: *prove* the target -- and plenty of legitimate Lean proofs need one.
    #: The dependency closure is still audited, so an instance cannot
    #: smuggle anything past the axiom listing.
    allow_global_instances: bool = True

    #: Whether each answer must build in its own directory, with only its
    #: own artifacts and the problem's on the search path.
    #:
    #: On by default and there is no good reason to turn it off: sharing a
    #: build directory would let one participant's `.olean` be importable by
    #: another's, which makes a verdict depend on grading order.
    isolate_builds: bool = True
    #: Require recorded kernel replay, in addition to ordinary compilation.
    require_replay: bool = False

    def __post_init__(self) -> None:
        if not self.isolate_builds:
            raise ValueError("non-isolated builds are not supported")
        # This class is a public export, so it gets built by hand as well as
        # from a manifest. A list or tuple here used to survive construction
        # and then fail inside the axiom audit -- which only runs once a proof
        # has been *accepted*, so the wrong type broke correct answers and
        # left refusals working. Normalise instead of trusting the annotation.
        object.__setattr__(self, "allowed_imports", tuple(self.allowed_imports))
        object.__setattr__(self, "allowed_axioms", frozenset(self.allowed_axioms))

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_imports": list(self.allowed_imports),
            "allowed_axioms": sorted(self.allowed_axioms),
            "allow_global_instances": self.allow_global_instances,
            "isolate_builds": self.isolate_builds,
            "require_replay": self.require_replay,
        }

    def audit(self, axioms: "Sequence[str] | None") -> "SoundnessReport":
        """Judge an axiom listing. ``None`` means the audit did not run.

        A missing listing is never a pass: the whole point of the audit is
        that a term can typecheck without being a proof, so "we did not
        look" has to be distinguishable from "we looked and it was clean".
        """
        if axioms is None:
            return SoundnessReport().with_violation(
                label(
                    HackClass.PLACEHOLDER,
                    "kernel.audit_missing",
                    "no `#print axioms` listing was obtained, so nothing here is "
                    "evidence that the proof is free of `sorry` or of a declared "
                    "axiom",
                )
            )
        return audit_axioms(axioms, allowed=self.allowed_axioms)
