## 2025-02-14 - Semantic Form Grouping
**Learning:** Using `div` elements with class names `fieldset` and `legend` for grouped form controls (like radio buttons or checkboxes) strips semantic meaning. Screen readers fail to announce the group context (e.g., "Target") when navigating between related inputs, making the form harder to understand for non-visual users.
**Action:** Always use native `<fieldset>` and `<legend>` elements for related form controls to ensure screen readers announce the group name. Apply existing CSS classes to them to maintain visual consistency without sacrificing accessibility.

## 2024-06-11 - Missing keyboard focus & async disabled states
**Learning:** Found that the custom button component (`.btn`) disabled state was missing entirely, which meant the async "Run review" action button looked visually identical and continued to receive hover translation animations even while running (and logically disabled by the script). In addition, interactive elements across the site lacked a globally visible `:focus-visible` ring, hindering keyboard navigation.
**Action:** When working on small vanilla JS apps or sites without a robust framework, always audit button states (`:disabled`) and ensure a global `:focus-visible` rule exists early on.

## 2026-06-11 - Textual feedback for disabled async buttons
**Learning:** Even with opacity drops and `:not-allowed` cursors, disabling an asynchronous action button (like a "Run review" button) without changing its text leaves the user guessing whether the application is processing or just stuck. The lack of textual feedback (e.g., changing "Run review" to "Running review...") makes the wait feel longer and the UI feel unresponsive, reducing user confidence.
**Action:** Whenever a primary action button triggers a long-running async operation, explicitly change its text content to describe the current state (e.g., "Running...", "Saving...", "Submitting...") in addition to disabling it.

## 2026-06-12 - Explicit tooltips for disabled form inputs
**Learning:** Disabled form elements (like radio buttons or checkboxes) offer no native way to explain *why* they are disabled. Users might think the UI is broken if they click an option and it's grayed out without context.
**Action:** Always add a `title` attribute tooltip to disabled form elements explaining the condition that disabled them (e.g., 'Debate requires at least 2 reviewers').

## 2026-06-13 - Context tooltips directly on disabled inputs
**Learning:** When adding `title` tooltips to disabled form elements, putting the tooltip only on the parent wrapper (like `.opt` or `fieldset`) may result in keyboard users and screen readers missing the context, since they navigate directly to the `<input>` element.
**Action:** Always apply the `title` attribute directly on the disabled `<input>` element itself (in addition to parent wrappers, if necessary for mouse users) so the context is announced or displayed properly when navigating.
