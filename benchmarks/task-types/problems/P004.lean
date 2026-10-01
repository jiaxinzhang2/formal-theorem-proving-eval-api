import Std
/-!
- source: repository example
- prose: Describe all natural numbers congruent to 3 modulo 7 and to 5 modulo 11, and prove that the submitted predicate characterizes exactly this infinite solution set.
-/

namespace Problem

abbrev Valid (n : Nat) : Prop := n % 7 = 3 ∧ n % 11 = 5
abbrev Answer := Nat → Prop
abbrev Target (S : Answer) : Prop := ∀ n, S n ↔ Valid n

end Problem
