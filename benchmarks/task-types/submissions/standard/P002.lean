import FtpEvalBench.P002

namespace Submission

def answer (r c : Fin 3) : Fin 10 :=
  match r.val, c.val with
  | 0, 0 => 8
  | 0, 1 => 1
  | 0, _ => 6
  | 1, 0 => 3
  | 1, 1 => 5
  | 1, _ => 7
  | _, 0 => 4
  | _, 1 => 9
  | _, _ => 2

theorem solution : Problem.Target answer := by decide

end Submission
