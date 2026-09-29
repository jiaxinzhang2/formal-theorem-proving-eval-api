/-
- source: repository example
- prose: Prove that mirroring a binary tree twice restores it and that its leaf count is one more than its internal-node count.
-/
import Std

namespace Problem

inductive Tree where
  | leaf (label : Nat)
  | node (left right : Tree)

def mirror : Tree → Tree
  | .leaf n => .leaf n
  | .node l r => .node (mirror r) (mirror l)

def leaves : Tree → Nat
  | .leaf _ => 1
  | .node l r => leaves l + leaves r

def internalNodes : Tree → Nat
  | .leaf _ => 0
  | .node l r => 1 + internalNodes l + internalNodes r

abbrev Target : Prop :=
  (∀ t, mirror (mirror t) = t) ∧
  (∀ t, leaves t = internalNodes t + 1)

end Problem
