## 2025-02-18 - Set Union Performance in Python
**Learning:** Computing set unions explicitly (`len(a | b)`) just to get the length is slow due to unnecessary O(N+M) object allocation.
**Action:** Use the inclusion-exclusion principle (`len(a) + len(b) - len(a & b)`) to calculate the union size without allocating a new set object. This improves performance significantly, e.g., in Jaccard similarity functions.

## 2025-06-09 - Avoid Generator Overhead in list.extend and str.join
**Learning:** Passing a generator expression directly into list.extend() or string.join() inside Python involves overhead. Since CPython implements these iteratively in C code, explicitly materializing the generator expression as a list comprehension (e.g. `[x for x in data]`) directly allows C functions to optimize and run quicker, bypassing Python-level generator instantiations.
**Action:** Use list comprehensions when populating existing lists via extend or when joining arrays to strings.
