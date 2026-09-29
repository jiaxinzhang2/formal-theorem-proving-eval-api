/- alice's answer to P001: fills the hole, keeps the vocabulary, proves it. -/
module

public import FormalConjecturesUtil

namespace Demo

/-- Unchanged from the problem. -/
abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop :=
    A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective

@[category research solved, AMS 5 11]
theorem demo_least_N_3 :
    IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } answer(4) := by
  refine ⟨⟨{1, 2, 4}, by decide, by decide⟩, ?_⟩
  intro n hn
  decide

end Demo
