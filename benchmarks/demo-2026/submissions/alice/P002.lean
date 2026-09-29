/- alice's answer to P002. -/
module

public import FormalConjecturesUtil

namespace P002

@[category textbook, AMS 11]
theorem add_comm_nat : ∀ a b : ℕ, a + b = b + a := by
  intro a b
  omega

end P002
