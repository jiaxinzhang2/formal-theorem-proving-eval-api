import Problem
import Problem.Goal
import Answer

-- The whole verdict. If this typechecks, the answer's proof term
-- inhabits the proposition the problem froze before the answer
-- existed -- whatever helpers it went through to get there.
theorem ftp_eval_target : _root_.FtpEvalGoal.G354e868b7ec5a424c2ef.goal :=
  Submission.solution

-- Typechecking is not enough: `sorry` elaborates to `sorryAx` and
-- a declared `axiom` passes the kernel with no complaint at all.
-- This listing is the only place either one shows up.
#print axioms ftp_eval_target
