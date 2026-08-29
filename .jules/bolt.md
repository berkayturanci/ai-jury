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

## 2025-06-12 - Ensure Single-Pass Consolidations Include Comments
**Learning:** When making code optimizations for single-pass collections or loop iterations, always verify that explicit comments are included to explain *why* the loop was implemented this way (e.g., `# bolt: Consolidate multiple metrics into a single-pass O(N) explicit loop`).
**Action:** Before submitting, ensure performance optimizations explicitly have their rationale documented in code via a comment as required by Bolt's guidelines.

## 2025-06-13 - Avoid splitlines() memory allocation on large text
**Learning:** Parsing large text or diffs using `splitlines()` allocates a huge list of strings, turning an O(1) memory operation into O(N). Iterating through it with Python generators is also significantly slower than C-optimized string operations.
**Action:** When counting line prefix occurrences across a large body of text, use `str.count("\nprefix")` instead of `splitlines()` to avoid memory allocation and benefit from C-level speeds, handling the first line specifically if it lacks a newline.

## 2025-06-14 - Limit Ruff Fixes to Modified Files
**Learning:** Running `uv run ruff check --fix .` and `uv run ruff format .` applies project-wide formatting, which pollutes the git history with unrelated files and breaks the rule to keep Bolt optimizations under 50 lines.
**Action:** When working on Bolt performance improvements, explicitly target only the files modified by the patch for ruff fixes and formatting (e.g., `uv run ruff format src/ai_jury/diffprofile.py`).

## 2025-02-19 - Avoid Generator Overhead in startswith/endswith
**Learning:** Using an `any()` generator expression with `startswith` or `endswith` (e.g., `any(s.startswith(p) for p in prefixes)`) incurs significant Python interpreter overhead compared to passing a tuple of prefixes directly to the method.
**Action:** For string prefix/suffix checking against multiple candidates, always pass a tuple directly to `.startswith()` or `.endswith()` to utilize the fast C-optimized iteration.
## 2025-06-16 - Consolidate Multiple Iterations into Explicit O(N) Loops
**Learning:** Generator expressions inside aggregators like `sum()` when looping over collections iteratively force Python interpreter overhead. Attempting multiple sequential generator loops through the same list (e.g. `sum(1 for x in collection)`) forces Python to traverse the list multiple times, which reduces performance.
**Action:** Bypass generator evaluation overhead and secondary O(N) evaluations by explicitly tracking sequential or aggregated metrics directly inside the primary loop traversing a list, computing multiple variables in a single-pass O(N) operation instead.
## 2025-06-17 - Avoid .count() on lists during multi-metric aggregations
**Learning:** Using `list.count()` multiple times sequentially on the same list forces multiple O(N) traversals.
**Action:** Consolidate multiple sequential `list.count()` calls into a single explicit loop that tallies all metrics simultaneously to achieve single-pass O(N) evaluation.

## 2025-06-18 - C-optimized Substring Checks for Patch Markers
**Learning:** Parsing large texts with `splitlines()` and iterating with `any()` is slow and memory-intensive. For simple line prefix matches across large bodies of text, using C-optimized string inclusion checks (`in`) is significantly faster.
**Action:** Use `fix.startswith(markers)` for the first line and `f'
{marker}' in fix` for subsequent lines to bypass generator overhead and list allocations.
