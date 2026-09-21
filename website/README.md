# Website

The static landing site for `ai-jury`. Originated as a Claude Design handoff
bundle and shipped here verbatim — `index.html`, `docs.html`,
`coverage.html`, `coverage-report.html`, `404.html`, plus `styles.css`,
`docs.css`, `app.js`, the favicon set, `site.webmanifest`, and the
convergence logo + OG banner under `assets/`. No build step.

## Preview locally

```bash
cd website
python3 -m http.server 8000
# open http://localhost:8000
```

## Deploy

`.github/workflows/pages.yml` publishes this directory to **GitHub Pages** on every push
to `main` that touches `website/**` (and via manual `workflow_dispatch`). The same
workflow also writes `website/coverage/` (HTML coverage report) and
`website/coverage-badge.json` (shields-endpoint badge), which `index.html` links to.

One-time repository setup (Settings → Pages):

1. Set **Source** to **GitHub Actions**.
2. Trigger the *Deploy website* workflow (push to `main` or run it manually).
3. The default URL is `https://ai-jury.dev/`.

## Analytics

Every page loads Cloudflare Web Analytics through one inline tag in its `<head>`
(`static.cloudflareinsights.com/beacon.min.js`). It is cookieless — no cookies, no
cross-site identifiers — so the site needs no consent banner, and there is no other
analytics on it. An earlier revision of this file described a different setup that
no page has carried since June 2026; `tests/test_site_seo.py` now pins this section
to the files.

The pages report into a Cloudflare site of this site's own (`ai-jury.dev`, since
2026-09-21). Until then they shared a dashboard with the sibling project's site,
created when both lived under github.io; the history before that date stays there.

A Cloudflare token is not bound to the hostname it was created for: a beacon from
any host that carries it is recorded, and the endpoint answers 204 either way. So a
fork or a local preview that keeps the token also reports — filter by hostname in
the dashboard — and a page left on a different token would go missing without any
error, which is why every page must carry the same one, exactly once. To publish a
fork without reporting, delete the tag.

This only measures the **website**; the `ai-jury` CLI itself sends no
telemetry.

## Assets

Favicons and the README hero are regenerated from their SVG sources via
`make assets` (requires `rsvg-convert` — `brew install librsvg` /
`apt install librsvg2-bin`). The OG banner (`assets/og-banner.png`) is a
one-shot designer asset and is **not** rebuilt by `make assets`.

## Custom domain (optional)

To serve under a maintainer-owned subdomain such as
`jury.berkayturanci.com` or `ai-jury.berkayturanci.com`:

1. **DNS** (at your domain provider): add a `CNAME` record for the subdomain pointing to
   `berkayturanci.github.io` (do not append the repo path).
2. **GitHub** (Settings → Pages → Custom domain): enter the subdomain and save. GitHub
   writes a `CNAME` file into the published site and provisions HTTPS.
3. Keep "Enforce HTTPS" enabled once the certificate is issued.

> A `CNAME` file is intentionally **not** committed here: committing one before DNS is
> configured would override the working `github.io` URL. Configure the domain in the
> Pages settings instead, which manages the `CNAME` for you. If you prefer to commit it,
> add a `website/CNAME` file containing only the bare hostname and update the workflow
> to include it.
