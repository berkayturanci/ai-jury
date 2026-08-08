
## 2026-06-07 - Fixed header anchor scrolling
**Learning:** When using a fixed header (`position: fixed`), navigating to in-page anchor links (like `#faq`) causes the browser to scroll the element exactly to the top of the viewport, hiding it underneath the header.
**Action:** Always apply `scroll-padding-top` to the `html` or `body` element equivalent to the fixed header's height (e.g. 64px) to ensure native anchor jumps preserve top visibility.

## 2024-08-08 - Accessible Focus Rings on Custom Form Wrappers
**Learning:** When using custom form wrappers around native inputs (like styled labels around tiny radio buttons/checkboxes), default focus outlines on the inner inputs can be hard to see or look unpolished.
**Action:** Use the `:has(input:focus-visible)` pseudo-class on the wrapper element to apply a clear, container-sized focus ring, while simultaneously hiding the inner input's default outline to ensure robust keyboard navigation visibility.
