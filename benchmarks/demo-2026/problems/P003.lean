/- Problem P003. Has an answer(...) hole: find the value and prove it. -/
module

public import FormalConjecturesUtil

/-!
# P003
- mathdb_id: 392323
- source: https://oeis.org/A000079
- difficulty: textbook
- author: jz
- checked: 2026-09-21
-/

namespace P003

/-- The number of subsets of a 3-element set. -/
@[category textbook, AMS 5]
theorem powerset_card_three (A : Finset ℕ) (h : A.card = 3) :
    A.powerset.card = answer(sorry) := by
  sorry

end P003
