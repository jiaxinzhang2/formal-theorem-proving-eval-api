import FtpEvalBench.P002

namespace Submission

-- Reflection gives another valid square; there is no canonical matrix.
def chosen (r c : Fin 3) : Fin 10 :=
  match r.val, c.val with
  | 0, 0 => 4
  | 0, 1 => 9
  | 0, _ => 2
  | 1, 0 => 3
  | 1, 1 => 5
  | 1, _ => 7
  | _, 0 => 8
  | _, 1 => 1
  | _, _ => 6

theorem solution : Problem.Target chosen := by decide

end Submission
