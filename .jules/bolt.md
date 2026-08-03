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


## 2025-06-15 - Fast str.startswith with tuples
**Learning:** Using `any(name.startswith(p) for p in prefixes)` forces Python to allocate a generator object and run a loop inside the interpreter. Python's native `str.startswith()` method can accept a tuple of strings directly, pushing the entire iteration into C-optimized code, which is significantly faster and more readable.
**Action:** When checking if a string starts with any of a static set of prefixes, pass the tuple directly to `str.startswith(prefixes)` instead of using `any()` with a generator expression.
