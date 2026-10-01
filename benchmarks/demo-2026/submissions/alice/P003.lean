import FtpEvalBench.P003

namespace Submission

theorem solution : Problem.Target := by
  intro n
  induction n with
  | zero => norm_num
  | succ k ih =>
    obtain ⟨c, hc⟩ := ih
    refine ⟨10 * c + 1, ?_⟩
    rw [Problem.step, hc]
    ring

end Submission
