// Google Analytics 4 (gtag.js) for the ai-jury website.
//
// Scope note: this measures **the website only** — anonymous pageviews and
// the GA4 default events (scroll, outbound click, etc.). The `ai-jury` CLI
// itself sends no telemetry; the two are different surfaces.
//
// To rotate the GA4 property, change `MEASUREMENT_ID` and commit. To disable
// tracking entirely (e.g. for a fork or local preview), leave it as the
// placeholder string starting with `G-REPLACE_ME` — the loader short-circuits.

const MEASUREMENT_ID = "G-72JDED64T7";

if (!MEASUREMENT_ID.startsWith("G-REPLACE_ME")) {
  const s = document.createElement("script");
  s.async = true;
  s.src = "https://www.googletagmanager.com/gtag/js?id=" + MEASUREMENT_ID;
  document.head.appendChild(s);

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = gtag;
  gtag("js", new Date());
  gtag("config", MEASUREMENT_ID);
}
