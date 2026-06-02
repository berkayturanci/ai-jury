# Website

The static landing page for `ai-jury`. No build step — plain HTML/CSS plus
one image. It reuses the same hero asset as the README (`assets/hero.png`).

## Preview locally

```bash
cd website
python3 -m http.server 8000
# open http://localhost:8000
```

## Deploy

`.github/workflows/pages.yml` publishes this directory to **GitHub Pages** on every push
to `main` that touches `website/**` (and via manual `workflow_dispatch`).

One-time repository setup (Settings → Pages):

1. Set **Source** to **GitHub Actions**.
2. Trigger the *Deploy website* workflow (push to `main` or run it manually).
3. The default URL is `https://berkayturanci.github.io/ai-jury/`.

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
