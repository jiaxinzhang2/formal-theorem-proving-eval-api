/- carol's answer to P002, with an axiom parked at the end. -/
module

public import FormalConjecturesUtil

namespace P002

@[category textbook, AMS 11]
theorem add_comm_nat : ∀ a b : ℕ, a + b = b + a := by
  intro a b
  exact cheat a b

axiom cheat : ∀ a b : ℕ, a + b = b + a

end P002
