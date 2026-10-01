-- A file-level `instance`, which this benchmark's manifest forbids
-- (`allow_global_instances: false`). Instances are allowed by default --
-- the frozen target makes them harmless -- so this answer is also the
-- only end-to-end evidence that the manifest's policy is really applied.
import FtpEvalBench.P001

namespace Submission

instance : Inhabited Nat := ⟨0⟩

theorem solution : Problem.Target := fun n => Nat.add_zero n

end Submission
