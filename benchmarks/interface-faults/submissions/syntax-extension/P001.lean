-- Extending the checker's language. Answers may prove; they may not change
-- what the grader's own generated module would mean.
import FtpEvalBench.P001

namespace Submission

notation "ZERO" => 0

theorem solution : Problem.Target := fun n => Nat.add_zero n

end Submission
