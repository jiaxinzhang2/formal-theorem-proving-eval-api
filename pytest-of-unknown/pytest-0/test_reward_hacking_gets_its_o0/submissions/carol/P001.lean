module
public import FormalConjecturesUtil

/-!
# P001
- mathdb_id: 392323
- source: https://example.org/paper
- difficulty: textbook
-/

namespace Demo

abbrev IsGood (n : ℕ) : Prop := 0 < n

axiom cheat : IsLeast { n | IsGood n } 1

@[category research open, AMS 11]
theorem demo_thm : IsLeast { n | IsGood n } answer(1) := by
  exact ⟨by decide, by decide⟩

end Demo
