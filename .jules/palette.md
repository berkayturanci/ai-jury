
## 2026-06-07 - Fixed header anchor scrolling
**Learning:** When using a fixed header (`position: fixed`), navigating to in-page anchor links (like `#faq`) causes the browser to scroll the element exactly to the top of the viewport, hiding it underneath the header.
**Action:** Always apply `scroll-padding-top` to the `html` or `body` element equivalent to the fixed header's height (e.g. 64px) to ensure native anchor jumps preserve top visibility.
## 2026-06-08 - Container-sized focus rings on custom form wrappers
**Learning:** When using custom styled wrappers (like `.opt`) around native radio buttons or checkboxes, default focus outlines on the inner `<input>` are often too small, misaligned, or difficult to see for keyboard users.
**Action:** Use the `:has(input:focus-visible)` pseudo-class on the wrapper element to apply a clear, container-sized focus ring (`outline: 2px solid var(--accent)`), and simultaneously hide the inner input's default outline (`outline: none`). This provides robust keyboard navigation visibility.
