## 2025-02-18 - Set Union Performance in Python
**Learning:** Computing set unions explicitly (`len(a | b)`) just to get the length is slow due to unnecessary O(N+M) object allocation.
**Action:** Use the inclusion-exclusion principle (`len(a) + len(b) - len(a & b)`) to calculate the union size without allocating a new set object. This improves performance significantly, e.g., in Jaccard similarity functions.

## 2025-06-09 - Avoid Generator Overhead in list.extend and str.join
**Learning:** Passing a generator expression directly into list.extend() or string.join() inside Python involves overhead. Since CPython implements these iteratively in C code, explicitly materializing the generator expression as a list comprehension (e.g. `[x for x in data]`) directly allows C functions to optimize and run quicker, bypassing Python-level generator instantiations.
**Action:** Use list comprehensions when populating existing lists via extend or when joining arrays to strings.

## 2024-06-08 - Fast regex evaluation for classification
**Learning:** Checking multiple static regexes via `any(rx.search(...) for rx in list_of_rx)` is much slower than combining them into a single regex `(rx1|rx2|...)` because it forces python to evaluate them sequentially in the interpreter rather than pushing the entire loop into the C regex engine.
**Action:** Always prefer `|`-combined regexes over looping for large sets of static search queries.
