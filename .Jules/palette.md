## 2024-07-13 - [WAI-ARIA Tablist Navigation]
**Learning:** Simply applying role="tablist" and role="tab" is insufficient for accessibility. Implementing custom JavaScript to intercept arrow keys (`keydown` events) and manage roving tabindex is required for proper WAI-ARIA tab navigation.
**Action:** Always implement a roving tabindex (active tab has tabindex="0", others -1) and custom JavaScript arrow key interceptors when building tablists.
