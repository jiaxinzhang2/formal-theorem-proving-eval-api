/-
Generated, not written by hand. This is the entire verdict.

Note what is absent: the answer's text. The check cites one constant and
states the type it must have. If this typechecks, every helper the answer
went through really does combine into a proof of the asked proposition.
-/
import Problem
import Answer

theorem ftp_eval_target : Problem.Target (1) :=
  Submission.solution

-- Typechecking is not the bar. `sorry` elaborates to `sorryAx` and Lean
-- reports a warning; a declared `axiom` passes with no complaint at all.
-- This listing is the only place either one shows up.
#print axioms ftp_eval_target
