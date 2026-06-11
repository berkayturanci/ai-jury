## 2025-02-14 - Semantic Form Grouping
**Learning:** Using `div` elements with class names `fieldset` and `legend` for grouped form controls (like radio buttons or checkboxes) strips semantic meaning. Screen readers fail to announce the group context (e.g., "Target") when navigating between related inputs, making the form harder to understand for non-visual users.
**Action:** Always use native `<fieldset>` and `<legend>` elements for related form controls to ensure screen readers announce the group name. Apply existing CSS classes to them to maintain visual consistency without sacrificing accessibility.

## 2024-06-11 - Missing keyboard focus & async disabled states
**Learning:** Found that the custom button component (`.btn`) disabled state was missing entirely, which meant the async "Run review" action button looked visually identical and continued to receive hover translation animations even while running (and logically disabled by the script). In addition, interactive elements across the site lacked a globally visible `:focus-visible` ring, hindering keyboard navigation.
**Action:** When working on small vanilla JS apps or sites without a robust framework, always audit button states (`:disabled`) and ensure a global `:focus-visible` rule exists early on.
