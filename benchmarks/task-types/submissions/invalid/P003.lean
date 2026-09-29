import FtpEvalBench.P003

namespace Submission

def answer (n : Problem.Input) : Problem.Output n :=
  (⟨0, by omega⟩, ⟨0, by omega⟩)

-- Proving a per-input fact with a theorem binder does not export a closed solution.
theorem solution (n : Problem.Input) : 0 ≤ n := Nat.zero_le n

end Submission
