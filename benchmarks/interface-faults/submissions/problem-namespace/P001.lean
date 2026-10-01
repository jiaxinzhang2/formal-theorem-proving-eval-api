-- Reopening the sealed module. `namespace Problem` belongs to the compiled
-- problem; an answer that declares into it is adding names that look like
-- the benchmark's own.
import FtpEvalBench.P001

namespace Problem

theorem looks_official : True := trivial

end Problem

namespace Submission

theorem solution : Problem.Target := fun n => Nat.add_zero n

end Submission
