/- An honest answer: the hole is filled and the theorem is proved. -/
module

public import FormalConjecturesUtil

namespace Demo

/-- $A\subseteq\{1,\dots,N\}$ with all subset sums distinct. -/
abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop :=
    A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective

/--
The least $N$ admitting a sum-distinct set of three elements.
-/
@[category research solved, AMS 5 11]
theorem demo_least_N_3 :
    IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } answer(4) := by
  refine ⟨⟨{1, 2, 4}, by decide, by decide⟩, ?_⟩
  intro n hn
  decide
end Demo
