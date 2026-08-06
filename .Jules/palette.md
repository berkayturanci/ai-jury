
## 2026-06-07 - Fixed header anchor scrolling
**Learning:** When using a fixed header (`position: fixed`), navigating to in-page anchor links (like `#faq`) causes the browser to scroll the element exactly to the top of the viewport, hiding it underneath the header.
**Action:** Always apply `scroll-padding-top` to the `html` or `body` element equivalent to the fixed header's height (e.g. 64px) to ensure native anchor jumps preserve top visibility.
