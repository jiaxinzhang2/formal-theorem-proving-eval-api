/-
An honest non-answer: the statement is right and the answer is given, but
the proof is absent. This must not be scored as tampering or as cheating.
-/
module

public import FormalConjecturesUtil

namespace Demo

abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop :=
    A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective

@[category research open, AMS 5 11]
theorem demo_least_N_3 :
    IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } answer(4) := by
  sorry

end Demo
