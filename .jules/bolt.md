## 2025-02-18 - Set Union Performance in Python
**Learning:** Computing set unions explicitly (`len(a | b)`) just to get the length is slow due to unnecessary O(N+M) object allocation.
**Action:** Use the inclusion-exclusion principle (`len(a) + len(b) - len(a & b)`) to calculate the union size without allocating a new set object. This improves performance significantly, e.g., in Jaccard similarity functions.
