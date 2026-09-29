/- bob's answer to P003: states it correctly, has not proved it yet. -/
module

public import FormalConjecturesUtil

namespace P003

@[category textbook, AMS 5]
theorem powerset_card_three (A : Finset ℕ) (h : A.card = 3) :
    A.powerset.card = answer(8) := by
  sorry

end P003
