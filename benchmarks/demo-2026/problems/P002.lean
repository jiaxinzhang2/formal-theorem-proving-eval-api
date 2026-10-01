import Mathlib

/-!
- mathdb_id: demo.sum-of-odd-numbers
- source: demo-2026
- difficulty: textbook
- prose: Show that the sum of the first n odd numbers equals n squared.
-/

namespace Problem

/-- Nothing to supply: prove the statement as it stands. -/
abbrev Target : Prop :=
  ∀ n : ℕ, ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2

end Problem
