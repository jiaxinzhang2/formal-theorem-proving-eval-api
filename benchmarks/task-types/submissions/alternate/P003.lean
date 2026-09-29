import FtpEvalBench.P003

namespace Submission

def chosen (n : Problem.Input) : Problem.Output n :=
  (⟨3 * n + 2, by omega⟩, ⟨3 * n + 5, by omega⟩)

theorem solution : Problem.Target chosen := by
  intro n
  change (3 * n + 2) * (2 * n + 3) = (3 * n + 5) * (2 * n + 1) + 1
  grind

end Submission
