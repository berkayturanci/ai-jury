import re

authored = """💡 **What**: Refactored the generic `<div>` containers used for form field groups into semantic `<fieldset>` and `<legend>` tags in the interactive demo's configuration form in `website/index.html`.
🎯 **Why**: Using semantic HTML provides better structural context for screen readers. It clearly associates related inputs (like radio buttons for Depth or target selection) with their appropriate group title. This prevents screen readers from simply reading a sequence of inputs without conveying the overarching category they belong to.
♿ **Accessibility**: Significant improvement for screen reader users by properly grouping complex form controls, enhancing both keyboard navigation focus announcements and general form comprehension without modifying existing visuals.

no issue

---
*PR created automatically by Jules for task [8105512229494844451](https://jules.google.com/task/8105512229494844451) started by @berkayturanci*"""

has_ref = bool(re.search(r"#\d+", authored)) or bool(
    re.search(r"\bno[ -]?issue\b", authored, re.I)
)
print("has_ref:", has_ref)
