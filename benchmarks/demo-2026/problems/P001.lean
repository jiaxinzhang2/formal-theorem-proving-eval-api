/-!
- mathdb_id: demo.P001
- source: demo-2026
- prose: Show that the requested natural number is one.
-/
namespace Problem
abbrev Target (n : Nat) : Prop := n = 1
end Problem
