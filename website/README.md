# Website

The static landing site for `ai-jury`, served at <https://ai-jury.dev/>. It began
as a Claude Design handoff bundle and has been edited in place since. No build step.

- **Pages:** `index.html`, `docs.html` (renders `docs/*.md` from the release tag),
  `coverage.html`, `coverage-report.html`, the article `agent-pr-said-one-line.html`,
  and `404.html`.
- **Code and style:** `app.js`, `styles.css`, `docs.css`.
- **For crawlers and agents:** `sitemap.xml`, `robots.txt`, `llms.txt`, and the
  IndexNow key file (`<key>.txt`, see `pages.yml`).
- **Images:** the favicon set, `apple-touch-icon.png`, `site.webmanifest`; under
  `assets/` the convergence logo, the OG banner, the README hero and the two theater
  GIFs; under `logos/` the integration-card logos.
- **Domain:** `CNAME` (`ai-jury.dev`, see [Custom domain](#custom-domain)).

## Preview locally

```bash
cd website
python3 -m http.server 8000
# open http://localhost:8000
```

## Deploy

`.github/workflows/pages.yml` publishes this directory to **GitHub Pages** on every push
to `main`, whatever it touches — there is no path filter, so the published coverage
report stays current with the code — and via manual `workflow_dispatch`. Before the
upload the same workflow writes `website/coverage/` (HTML coverage report) and
`website/coverage-badge.json` (shields-endpoint badge), which `index.html` links to,
and copies the repository's `install.sh` in as `website/install.sh`. After the deploy
it pings IndexNow with the URLs in `sitemap.xml`.

One-time repository setup (Settings → Pages):

1. Set **Source** to **GitHub Actions**.
2. Trigger the *Deploy website* workflow (push to `main` or run it manually).
3. The site is served at the custom domain `https://ai-jury.dev/` (see below).

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

## Custom domain

The site is served at `ai-jury.dev`, and `website/CNAME` holds that bare hostname;
the workflow uploads the whole directory, so the file ships with every deploy. For a
Pages site deployed from a GitHub Actions workflow, GitHub's documentation says the
custom domain is the one set under Settings → Pages → Custom domain and that a
`CNAME` file in the artifact is not what configures it, so a change of domain is made
there (and in DNS), with `CNAME` updated to match. Keep "Enforce HTTPS" enabled.
