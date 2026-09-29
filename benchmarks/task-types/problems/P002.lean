/-
- source: repository example
- prose: Construct a normal 3 by 3 magic square: distinct entries from 1 through 9, with every row, column and both diagonals summing to 15.
-/
import Std

namespace Problem

abbrev Answer := Fin 3 → Fin 3 → Fin 10

abbrev Target (m : Answer) : Prop :=
  (∀ i j, 0 < (m i j).val) ∧
  (∀ i j k l, m i j = m k l → i = k ∧ j = l) ∧
  (∀ i, (m i 0).val + (m i 1).val + (m i 2).val = 15) ∧
  (∀ j, (m 0 j).val + (m 1 j).val + (m 2 j).val = 15) ∧
  ((m 0 0).val + (m 1 1).val + (m 2 2).val = 15) ∧
  ((m 0 2).val + (m 1 1).val + (m 2 0).val = 15)

end Problem
