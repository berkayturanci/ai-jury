## 2025-02-18 - Set Union Performance in Python
**Learning:** Computing set unions explicitly (`len(a | b)`) just to get the length is slow due to unnecessary O(N+M) object allocation.
**Action:** Use the inclusion-exclusion principle (`len(a) + len(b) - len(a & b)`) to calculate the union size without allocating a new set object. This improves performance significantly, e.g., in Jaccard similarity functions.

## 2025-06-09 - Avoid Generator Overhead in list.extend and str.join
**Learning:** Passing a generator expression directly into list.extend() or string.join() inside Python involves overhead. Since CPython implements these iteratively in C code, explicitly materializing the generator expression as a list comprehension (e.g. `[x for x in data]`) directly allows C functions to optimize and run quicker, bypassing Python-level generator instantiations.
**Action:** Use list comprehensions when populating existing lists via extend or when joining arrays to strings.

## 2024-06-08 - Fast regex evaluation for classification
**Learning:** Checking multiple static regexes via `any(rx.search(...) for rx in list_of_rx)` is much slower than combining them into a single regex `(rx1|rx2|...)` because it forces python to evaluate them sequentially in the interpreter rather than pushing the entire loop into the C regex engine.
**Action:** Always prefer `|`-combined regexes over looping for large sets of static search queries.

## 2025-06-10 - O(N) finding iteration
**Learning:** In PR-level deterministic classification functions, iterating sequentially over the findings list multiple times using `any()` expression overhead is significantly slower than doing one single explicit for-loop iteration pass through the list.
**Action:** Consolidate multiple sequential list iteration conditions (like `any(severity == "x")`, `any(severity == "y")`) into a single explicit loop over the findings array.

## 2025-06-11 - Single-pass Collection Aggregation
**Learning:** Using multiple `sum(generator_expression)` calls sequentially over the same collection forces Python to evaluate the collection multiple times, allocating generator objects for each call.
**Action:** Consolidate multiple sequential aggregations over the same list into a single-pass explicit `for` loop to compute all metrics simultaneously, bypassing redundant evaluations and generator overhead.
