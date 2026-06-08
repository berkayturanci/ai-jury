## 2024-06-08 - Semantic Form Elements
**Learning:** The configuration form for the interactive demo used `div` tags for grouped fields. Replacing them with `<fieldset>` and `<legend>` is a critical accessibility fix that improves how screen readers announce grouped inputs (like radios and checkboxes).
**Action:** Always check form structures for grouped inputs and ensure they use native semantic HTML (`<fieldset>`, `<legend>`) instead of generic containers, retaining existing classes for visual consistency.
