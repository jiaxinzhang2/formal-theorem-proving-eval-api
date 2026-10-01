import Mathlib

/-!
- mathdb_id: demo.nine-divides-ten-pow-minus-one
- source: demo-2026
- difficulty: textbook
- prose: Show that 9 divides 10^n - 1 for every natural number n.
-/

namespace Problem

/-- Shipped already proved. An answer may cite this or prove its own way. -/
theorem step (n : ℕ) : (10 : ℤ) ^ (n + 1) - 1 = 10 * (10 ^ n - 1) + 9 := by ring

abbrev Target : Prop :=
  ∀ n : ℕ, (9 : ℤ) ∣ 10 ^ n - 1

end Problem
