# site/ — the public apex marketing page

Owner spec: `project-docs/specs/marketing_site.md` (board row **WS-33**). That
spec is the single authority for this surface; do not describe it elsewhere.

## What lives here

- `index.html` — the static home page served at `https://metorite.com`. Since
  2026-10-09 it is a product tour: one section for each live app, with a
  screenshot (`marketing_site.md` §6).
- `img/` — the screenshots, as WebP files. They come from the fictional demo
  workspace that `scripts/demo_seed/` builds, never from customer data.
- `privacy.html`, `terms.html`, `refunds.html`, `contact.html` — the legal pages.

## Binding rules for this subtree

- **Zero attack/consent surface.** No `<script>`, no cookies, no analytics, no
  external fetches of any kind (no CDN, no web fonts, no remote images). Imagery
  is inline SVG, a `data:` URI, or a same-origin file under `img/`. Typography
  uses a system font stack.
- **A screenshot shows only the fictional demo company.** Never a customer's
  data, a real person, or a real phone number or email address.
- **Every `<img>` has an `alt`.** Each file in `img/` stays under 300 KB, and the
  folder stays under 3 MB.
- **Only origin referenced is `https://app.metorite.com`.** The "Sign up" CTA is
  exactly `https://app.metorite.com/signup`; the "Sign in" link is exactly
  `https://app.metorite.com`. Relative anchors (`#features`) are fine.
- **Each HTML page < 100 KB.**
- The workbench DESIGN_SYSTEM (eight rules) does **not** attach — this is not a
  product surface. Look-and-feel is advisory taste (marketing_site.md §3 note).

## Fence

`tests/unit/test_marketing_site.py` (R7) enforces every rule above. Run
`uv run pytest tests/unit/test_marketing_site.py -q` after any edit here.

## Not in this scope

Serving/DNS/TLS are owner-gated deploy actions (the Caddy apex block +
Hostinger apex A record), tracked in `marketing_site.md` §3 items 3–5. Nothing
under `deploy/` is edited from here.
