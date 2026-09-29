/- bob's answer to P001: weakens IsLeast to plain membership. -/
module

public import FormalConjecturesUtil

namespace Demo

abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop :=
    A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective

@[category research solved, AMS 5 11]
theorem demo_least_N_3 :
    (4 : ℕ) ∈ { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } := by
  exact ⟨{1, 2, 4}, by decide, by decide⟩

end Demo
