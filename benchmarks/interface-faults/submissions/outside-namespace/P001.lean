-- A top-level declaration. Across a contest it can collide with the
-- problem's own names, or with another answer's.
import FtpEvalBench.P001

theorem right_identity (n : Nat) : n + 0 = n := Nat.add_zero n

namespace Submission

theorem solution : Problem.Target := right_identity

end Submission
