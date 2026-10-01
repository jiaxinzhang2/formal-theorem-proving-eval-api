import Mathlib

/-!
- mathdb_id: demo.greatest-universal-divisor-of-n5-minus-n
- source: demo-2026
- difficulty: contest
- prose: Find the greatest positive integer that divides n^5 - n for every
  integer n.
-/

namespace Problem

/-- `k` divides `n ^ 5 - n` for every integer `n`. -/
abbrev AlwaysDivides (k : ℕ) : Prop :=
  ∀ n : ℤ, (k : ℤ) ∣ n ^ 5 - n

/-- Find the greatest such `k`. The answer supplies the value. -/
abbrev Target (a : ℕ) : Prop :=
  IsGreatest {k | AlwaysDivides k} a

end Problem
