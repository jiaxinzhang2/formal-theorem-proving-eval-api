module
public import FormalConjecturesUtil

/-!
# P002
- mathdb_id: 392323
- source: https://example.org/paper
- difficulty: textbook
-/

namespace Demo

abbrev IsGood (n : ℕ) : Prop := 0 < n

@[category research open, AMS 11]
theorem second_thm : IsLeast { n | IsGood n } answer(sorry) := by
  sorry

end Demo
