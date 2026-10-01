-- Helpers but no `Submission.solution`. There is nothing to check: the one
-- declaration that is the whole interface was never exported.
import FtpEvalBench.P001

namespace Submission

theorem right_identity (n : Nat) : n + 0 = n := Nat.add_zero n

end Submission
