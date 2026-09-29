import FtpEvalBench.P001

namespace Submission

theorem mirror_twice (t : Problem.Tree) : Problem.mirror (Problem.mirror t) = t := by
  induction t with
  | leaf _ => rfl
  | node l r ihl ihr => simp [Problem.mirror, ihl, ihr]

theorem leaf_count (t : Problem.Tree) : Problem.leaves t = Problem.internalNodes t + 1 := by
  induction t with
  | leaf _ => rfl
  | node l r ihl ihr =>
    simp only [Problem.leaves, Problem.internalNodes, ihl, ihr]
    omega

theorem solution : Problem.Target := ⟨mirror_twice, leaf_count⟩

end Submission
