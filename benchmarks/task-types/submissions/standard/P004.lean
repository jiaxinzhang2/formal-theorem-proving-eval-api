import FtpEvalBench.P004

namespace Submission

def answer : Problem.Answer := fun n => ∃ k : Nat, n = 38 + 77 * k

theorem solution : Problem.Target answer := by
  intro n
  change (∃ k : Nat, n = 38 + 77 * k) ↔ n % 7 = 3 ∧ n % 11 = 5
  constructor
  · rintro ⟨k, hk⟩
    omega
  · rintro ⟨h7, h11⟩
    refine ⟨(n - 38) / 77, ?_⟩
    omega

end Submission
