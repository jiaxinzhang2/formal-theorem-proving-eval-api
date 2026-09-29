import FtpEvalBench.P003

namespace Submission

def answer (n : Problem.Input) : Problem.Output n :=
  (⟨n + 1, by omega⟩, ⟨n + 2, by omega⟩)

theorem solution : Problem.Target answer := by
  intro n
  change (n + 1) * (2 * n + 3) = (n + 2) * (2 * n + 1) + 1
  grind

end Submission
