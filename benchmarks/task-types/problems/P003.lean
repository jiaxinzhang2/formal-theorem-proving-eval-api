/-
- source: repository example
- prose: For every n, construct bounded natural coefficients x and y certifying x * (2*n + 3) = y * (2*n + 1) + 1, hence the consecutive odd numbers are coprime.
-/
import Std

namespace Problem

abbrev Input := Nat
abbrev Output (n : Input) := Fin (4 * n + 6) × Fin (4 * n + 6)
abbrev Answer := (n : Input) → Output n
abbrev Valid (n : Input) (c : Output n) : Prop :=
  c.1.val * (2 * n + 3) = c.2.val * (2 * n + 1) + 1
abbrev Target (f : Answer) : Prop := ∀ n, Valid n (f n)

end Problem
