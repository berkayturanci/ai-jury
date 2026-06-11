## 2025-02-18 - Set Union Performance in Python
**Learning:** Computing set unions explicitly (`len(a | b)`) just to get the length is slow due to unnecessary O(N+M) object allocation.
**Action:** Use the inclusion-exclusion principle (`len(a) + len(b) - len(a & b)`) to calculate the union size without allocating a new set object. This improves performance significantly, e.g., in Jaccard similarity functions.

## 2024-06-08 - Fast regex evaluation for classification
**Learning:** Checking multiple static regexes via `any(rx.search(...) for rx in list_of_rx)` is much slower than combining them into a single regex `(rx1|rx2|...)` because it forces python to evaluate them sequentially in the interpreter rather than pushing the entire loop into the C regex engine.
**Action:** Always prefer `|`-combined regexes over looping for large sets of static search queries.
