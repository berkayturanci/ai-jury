
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

## 2026-06-15 - Add focus visible styles to summary elements
**Learning:** `<summary>` elements are inherently interactive and receive keyboard focus during tab navigation, but depending on browser defaults and CSS resets, they may lack a visible focus indicator. This makes them inaccessible to keyboard users who cannot tell which FAQ or details item they are currently focused on.
**Action:** When adding global `:focus-visible` styles to interactive elements like `a`, `button`, and `input`, explicitly include `summary:focus-visible` to ensure accordions and collapsible sections remain keyboard-accessible.

## 2026-06-20 - Explicit State Communication for Theme Toggles
**Learning:** When implementing theme toggle buttons, generic labels like 'Toggle light/dark theme' are insufficient for accessibility. It is critical to explicitly communicate the resulting state dynamically (e.g., 'Switch to light theme' or 'Switch to dark theme') based on the current active theme.
**Action:** Use MutationObserver on the global theme state (like document.documentElement's data-theme attribute) to dynamically update the aria-label and title properties of theme toggles, keeping them in sync across click events, page loads, and OS-level preference changes.

## 2026-06-18 - Dynamically updating state text without losing underlying labels
**Learning:** When changing the text of an interactive element to reflect an updated state temporarily (such as changing a "copy" button to "copied"), changing the text can obscure the underlying static label, or the new state might be obscured from screen readers entirely if the static `aria-label` continues to be announced.
**Action:** Always read and save the original `aria-label` and `title` attribute values before updating them dynamically. That way, the accessibility properties accurately reflect the current state and can be cleanly restored when reverting the visual state without accidentally wiping out preexisting labels.
## 2023-10-27 - Redundant ARIA attributes on dynamic text buttons
**Learning:** Adding temporary `aria-label` or `title` attributes (e.g., "Copied code") to buttons whose visible text dynamically changes (e.g., from "copy" to "copied") is redundant and detrimental to UX. The visible text change automatically updates the accessible name for screen readers, making the `aria-label` redundant, and native tooltips take too long to appear for transient states (1-2 seconds).
**Action:** When visible text on a button dynamically updates to indicate a temporary state, rely on the text change to convey the state to screen readers and avoid adding or updating `aria-label` or `title` attributes for that state. Ensure any original `aria-label` or `title` attributes are restored when the state reverts.
## 2024-05-18 - Dynamic Accessible Names for Theme Toggles
**Learning:** For theme toggle buttons (and similar state-toggling UI elements), using a static generic `aria-label` like "Toggle light/dark theme" is bad for accessibility because it does not communicate the resulting state of activating the button. Screen reader users need to know what will happen when they interact with it.
**Action:** Always ensure that toggle buttons explicitly communicate their resulting state by dynamically updating the `aria-label` and `title` attributes based on the current state (e.g., dynamically setting it to "Switch to light theme" or "Switch to dark theme").

## 2024-07-01 - Explaining disabled states
**Learning:** It is a good UX practice to disable call-to-action buttons (like "Run review") when the form state is invalid (e.g., 0 reviewers selected), rather than letting users click and showing an error note afterward. When disabling the button, providing an explicit `title` attribute on the button itself (e.g., "Pick at least one reviewer to run.") is essential so that users know exactly *why* it is disabled and how to fix it.
**Action:** When dynamically disabling a button due to missing prerequisites, always set a `title` explaining the missing prerequisite directly on the button, and ensure it is removed when the button is re-enabled.
## 2026-07-02 - Skip-to-content for Keyboard Accessibility
**Learning:** For websites with extensive navigation menus or sidebars (like docs), keyboard and screen reader users must tab through every single navigation link before reaching the main content. This is tedious and repetitive.
**Action:** Implement a 'skip-to-content' link as the first interactive element in the `<body>`. Ensure it remains visually hidden (e.g., positioned off-screen) until focused (`:focus-visible`), at which point it should become prominently visible and positioned correctly to allow immediate skipping to the main content container.

## 2026-07-08 - Icon-only buttons need native tooltips
**Learning:** Icon-only buttons (like hamburger menus or copy buttons) often have `aria-label`s for screen readers, but sighted mouse users rely on native tooltips (`title` attributes) for context. Omitting the `title` attribute leaves sighted users guessing the button's function.
**Action:** Always provide a `title` attribute (in addition to `aria-label`) for icon-only buttons, and ensure it dynamically updates if the button's state changes.

## 2026-10-25 - Arrow key navigation for WAI-ARIA tablists
**Learning:** Simply applying `role="tablist"` and `role="tab"` to elements does not automatically make them accessible. Keyboard users expect to use Arrow keys (Left/Right or Up/Down) to navigate between tabs, and the roving `tabindex` technique (where only the active tab has `tabindex="0"` and the others have `-1`) must be implemented manually via JavaScript.
**Action:** Whenever using `role="tablist"`, write custom JavaScript to intercept arrow keys (`keydown` events) to move focus and toggle the active tab, and dynamically update `tabindex` attributes to ensure the active tab remains in the document's tab order while inactive tabs are removed from it.

## 2026-10-26 - Missing ARIA states on custom toggle button groups
**Learning:** For sets of buttons functioning as single-choice filters or sort controls (like "Worst first", "Best first"), applying a visual class like `class="on"` visually communicates the active choice but leaves screen readers completely unaware of the selected state. Standard anchor navigation implies its own state with `aria-current="page"`, but interactive toggle buttons on the same view need `aria-pressed="true"` (or `aria-selected` if a tab list).
**Action:** When building custom groups of toggle buttons that filter or sort without navigating away, ensure the active button gets `aria-pressed="true"` and inactive buttons get `aria-pressed="false"` dynamically whenever the visual `on`/`active` class is toggled.

## 2026-11-04 - Escape key for mobile menus
**Learning:** Flyout menus and mobile hamburger menus often trap keyboard users if they cannot be dismissed with the `Escape` key. Users expect to be able to press `Escape` to close temporary navigation overlays and return focus to the trigger button.
**Action:** Whenever implementing a custom flyout menu, modal, or mobile sidebar, always add a `keydown` event listener for the `Escape` key that closes the overlay and returns focus to the button that opened it.

## 2024-05-18 - Allow pointer events on disabled elements with tooltips
**Learning:** Using `pointer-events: none` to disable form wrappers or elements prevents mouse events, which completely blocks native `title` attribute tooltips from appearing on hover. This leaves users frustrated because they cannot see *why* an option is disabled.
**Action:** Never use `pointer-events: none` on elements that carry a `title` attribute or need to display a tooltip. Instead, rely on the native `disabled` attribute on the element and use `cursor: not-allowed` on both the wrapper and the input for visual feedback.

## 2024-05-18 - Container-sized focus rings for custom form wrappers
**Learning:** For frontend accessibility with custom form wrappers (like styled labels `.opt` around tiny native inputs), relying on the global `input:focus-visible` outline results in a small, hard-to-see focus ring around just the radio button or checkbox. Keyboard users need larger, clearer visual indicators to track their focus across form options.
**Action:** Use the `:has(input:focus-visible)` pseudo-class on the wrapper element to apply a clear, container-sized focus ring (`outline: 2px solid var(--accent)`), while simultaneously hiding the inner input's default outline (`outline: none`). This ensures robust keyboard navigation visibility.
## 2026-06-07 - Fixed header anchor scrolling
**Learning:** When using a fixed header (`position: fixed`), navigating to in-page anchor links (like `#faq`) causes the browser to scroll the element exactly to the top of the viewport, hiding it underneath the header.
**Action:** Always apply `scroll-padding-top` to the `html` or `body` element equivalent to the fixed header's height (e.g. 64px) to ensure native anchor jumps preserve top visibility.
## 2026-08-07 - Active Link aria-current
**Learning:** While `.active` classes visually indicate the current page in navigation, screen readers need explicit semantic markup to announce it.
**Action:** Ensure dynamically or statically active navigation links also receive `aria-current="page"` (or `"location"` for in-page anchors).

## 2026-11-05 - Skip-to-content targets need tabindex
**Learning:** When implementing a 'skip-to-content' link, if the target element (like `<main>`) does not have `tabindex="-1"`, native browsers will scroll to it but will not transfer keyboard focus. When the user presses 'Tab' again, focus erroneously resets to the top of the page.
**Action:** Always add `tabindex="-1"` to the target container of a skip link (e.g., `<main id="main-content" tabindex="-1">`) to ensure the sequential keyboard navigation starting point is correctly moved.

## 2026-11-06 - Faint text color contrast failure
**Learning:** Using very low-contrast colors for supplementary text (like `--faint` used for meta text, timestamps, and table headers) makes the application unreadable for users with low vision or when viewed on poor displays. A 2.8:1 contrast ratio fails WCAG AA standards (4.5:1), even for secondary text.
**Action:** Always ensure that even "faint" or "muted" text tokens in the design system meet the minimum 4.5:1 WCAG AA contrast ratio against their respective background colors in both light and dark themes.
## 2026-08-16 - Focus indicators for custom interactive elements
**Learning:** When creating custom interactive elements (e.g., `div` or `span` with `role="button"` and `tabindex="0"`), they often lack visual focus indicators because global CSS focus resets (like `button:focus-visible`) don't target them. This makes keyboard navigation very difficult for users who rely on visual focus.
**Action:** Always ensure that custom interactive elements with a `tabindex` explicitly define a `:focus-visible` state in CSS (e.g., `.custom-card:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }`) that aligns with the site's global focus styles.

## 2026-08-20 - Global search shortcuts for discovery
**Learning:** For long lists of items (like integrations) that have a search bar, power users naturally try to press `/` or `Cmd+K` to start typing immediately. Providing a visible shortcut hint (`<kbd>`) not only speeds up workflows but also educates users about the app's keyboard accessibility, improving overall perceived usability.
**Action:** Whenever implementing a prominent text filter or search bar, add a `/` keyboard event listener to focus it, and include a visual `<kbd>` hint in the input container to make the shortcut discoverable.

## 2026-08-22 - Modal Focus Restoration
**Learning:** We implemented `Escape` to close the integration modal, but forgot to return focus to the integration card that opened it. When keyboard users closed the modal, they lost their place in the grid.
**Action:** Always store `document.activeElement` before opening a modal and restore focus to it inside the generic `closeModal()` function so that `Escape`, click-outside, and close button actions all correctly return the user to their previous context.
