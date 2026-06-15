// Cloudflare Web Analytics for the ai-jury website.
//
// Privacy: this is a **cookieless, privacy-first** measurement — it sets no
// cookies and collects no personal data / cross-site identifiers, so it needs no
// cookie-consent banner under GDPR / ePrivacy / PECR. It measures the website
// only (anonymous pageviews + Core Web Vitals); the `ai-jury` CLI itself sends
// no telemetry — the two are different surfaces.
//
// Setup: create a free site in Cloudflare → Web Analytics, copy its beacon
// token, and set `TOKEN` below. To disable tracking (a fork or local preview),
// leave the placeholder — the loader short-circuits.

const TOKEN = "f9a978b9ccc64ff49460fbcee8f722ef";

if (!TOKEN.startsWith("REPLACE_")) {
  const s = document.createElement("script");
  s.defer = true;
  s.src = "https://static.cloudflareinsights.com/beacon.min.js";
  s.setAttribute("data-cf-beacon", JSON.stringify({ token: TOKEN }));
  document.head.appendChild(s);
}
