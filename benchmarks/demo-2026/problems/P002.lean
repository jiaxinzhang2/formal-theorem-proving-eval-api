/- Problem P002. One theorem per file. -/
module

public import FormalConjecturesUtil

/-!
# P002
- mathdb_id: 315509
- source: https://arxiv.org/abs/2608.13251
- source_locator: Lemma 3.2
- source_version: arXiv:2608.13251v1
- difficulty: textbook
- author: jz
- checked: 2026-09-20
- contamination: source is public; prior exposure not excluded

## Provenance
- mathdb_id: demo.002
- source: demo-2026
- source_locator: problems/P002.lean
- difficulty: unrated
- author: demo
- prose: Show that for every natural number n, the sum 1 + 2 + ... + n equals n(n+1)/2.
-/

namespace P002

/-- Addition on the naturals is commutative. -/
@[category textbook, AMS 11]
theorem add_comm_nat : ∀ a b : ℕ, a + b = b + a := by
  sorry

end P002
