-- An attribute aimed at a declaration the answer does not own. Attribute
-- commands may target only `Submission` declarations.
import FtpEvalBench.P001

namespace Submission

attribute [simp] Nat.add_comm

theorem solution : Problem.Target := fun n => Nat.add_zero n

end Submission
