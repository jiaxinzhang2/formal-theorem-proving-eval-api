/-
The theorem's text is byte-identical to the problem's. The definition it
rests on has been replaced by `True`, so the statement no longer says
anything -- the kind of subversion that statement comparison alone misses.
-/
module

public import FormalConjecturesUtil

namespace Demo

abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop := True

@[category research solved, AMS 5 11]
theorem demo_least_N_3 :
    IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } answer(4) := by
  decide

end Demo
