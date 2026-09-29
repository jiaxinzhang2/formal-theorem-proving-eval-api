/-
An answer. It may declare as much as it likes, in any shape, and none of it
has to correspond to anything in the problem -- Lean walks the dependency
closure itself.

The only requirement is the last declaration's name and type.
-/
import Problem

namespace Submission

def myBound : ℕ := 1

lemma myLemma : Problem.Good myBound := by
  decide

/-- Legitimate, and allowed. An instance can change how a proposition
elaborates, which is why it had to be policed when the target was
re-elaborated next to the answer. `Problem.Target` is past elaboration, so
this can only help *prove* it. -/
instance : Inhabited ℕ := ⟨0⟩

/-- The interface. This name, this type. -/
theorem solution : Problem.Target 1 := by
  exact ⟨myLemma, fun n hn => hn⟩

end Submission
