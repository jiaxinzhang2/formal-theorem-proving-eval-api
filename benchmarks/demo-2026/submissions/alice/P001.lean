import FtpEvalBench.P001

namespace Submission

/-- The value this answer claims. -/
def answer : ℕ := 30

/-- Every residue mod 30 satisfies the congruence, checked exhaustively. -/
theorem residues : ∀ x : ZMod 30, x ^ 5 - x = 0 := by decide

theorem answer_divides : Problem.AlwaysDivides answer := by
  intro n
  have h : ((n ^ 5 - n : ℤ) : ZMod 30) = 0 := by
    push_cast
    exact residues (n : ZMod 30)
  exact_mod_cast (ZMod.intCast_zmod_eq_zero_iff_dvd (n ^ 5 - n) 30).mp h

/-- Testing `n = 2` alone already forces `k ∣ 30`. -/
theorem answer_greatest (k : ℕ) (hk : Problem.AlwaysDivides k) : k ≤ answer := by
  have h : (k : ℤ) ∣ 30 := by
    have h2 := hk 2
    norm_num at h2
    exact h2
  have hn : k ∣ 30 := by exact_mod_cast h
  exact Nat.le_of_dvd (by norm_num [answer]) hn

theorem solution : Problem.Target answer := by
  refine ⟨answer_divides, ?_⟩
  intro k hk
  exact answer_greatest k hk

end Submission
