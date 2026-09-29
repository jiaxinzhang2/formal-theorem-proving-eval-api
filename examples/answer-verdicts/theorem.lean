/-
The theorem file, in the formal-conjectures style: one theorem per file, with
the definitions it needs. `answer(sorry)` is the hole an answer fills;
the `sorry` body is the normal state of an unproved statement.
-/
module

public import FormalConjecturesUtil

/-!
# Least N for a 3-element sum-distinct set
-/

namespace Demo

/-- $A\subseteq\{1,\dots,N\}$ with all subset sums distinct. -/
abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop :=
    A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective

/--
The least $N$ admitting a sum-distinct set of three elements.
-/
@[category research open, AMS 5 11]
theorem demo_least_N_3 :
    IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } answer(sorry) := by
  sorry

end Demo
