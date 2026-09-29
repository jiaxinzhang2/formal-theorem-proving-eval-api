import Bench.P001
namespace Submission
def helper : Nat := 1
lemma supporting : helper = 1 := rfl
theorem solution : Problem.Target helper := supporting
end Submission
