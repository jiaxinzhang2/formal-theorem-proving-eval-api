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

theorem ftp_eval_match_probe : IsLeast { n | IsGood n } answer(1) := by

  exact ⟨by decide, by decide⟩

end Demo
