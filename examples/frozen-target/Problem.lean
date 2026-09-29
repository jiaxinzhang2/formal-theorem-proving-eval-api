/-
The sealed side. Compiled BEFORE any answer exists, which is the whole
point: once `Target` is elaborated here, nothing an answer declares can
change what it means.

Write `Target` by hand. No tool transforms it, so no tool can get it wrong.
-/
import Mathlib

namespace Problem

/-- The problem's vocabulary. An answer cannot redefine this: `Problem.Good`
is already a compiled constant, and a `Submission.Good` is a different one. -/
abbrev Good (n : ℕ) : Prop := 0 < n

/-- Shipped, proved. An answer may use it or ignore it. -/
lemma givenLemma : Good 1 := by decide

/-- The frozen proposition. Parameterized by the value the problem asks for,
so that value lands in the answer's *type* and the kernel compares it --
nothing has to decide whether `4` and `2 + 2` are the same string. -/
abbrev Target (a₀ : ℕ) : Prop :=
  IsLeast { n | Good n } a₀

end Problem
