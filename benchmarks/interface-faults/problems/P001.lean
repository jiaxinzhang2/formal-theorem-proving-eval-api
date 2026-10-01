import Std
/-!
- source: repository example
- prose: Prove that adding zero on the right leaves every natural number unchanged.
-/

namespace Problem

/-- Deliberately trivial. This benchmark exercises the refusal reasons, not
the mathematics: every answer beside `solved/` is refused before a prover is
ever asked, so the Target only has to be real enough to cite. -/
abbrev Target : Prop := ∀ n : Nat, n + 0 = n

end Problem
