/-
The same answer, but one helper the proof cites is left unproved, so the
whole proof is conditional on an assumption. It keeps the theorem file's vocabulary untouched and
brings its own machinery -- extra definitions and helper lemmas -- which is
normal for a real proof and is not penalised.

What it may not do is redefine `IsSumDistinctSet`, or leave a helper the
proof cites unproved.
-/
module

public import FormalConjecturesUtil

namespace Demo

/-- Unchanged from the theorem file: this is the problem's vocabulary. -/
abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop :=
    A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective

/-- The answer's own helper definition. -/
abbrev SubsetSums (A : Finset ℕ) : Finset ℕ :=
    A.powerset.image (fun S => S.sum id)

/-- Another of the answer's own. -/
def Witness : Finset ℕ := {1, 2, 4}

lemma witness_is_sum_distinct : IsSumDistinctSet Witness 4 := by
  constructor
  · decide
  · decide

lemma witness_card : Witness.card = 3 := by
  sorry

@[category research solved, AMS 5 11]
theorem demo_least_N_3 :
    IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } answer(4) := by
  refine ⟨⟨Witness, witness_is_sum_distinct, witness_card⟩, ?_⟩
  intro n hn
  decide

end Demo
