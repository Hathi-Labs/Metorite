# site/ — the public apex marketing page

Owner spec: `project-docs/specs/marketing_site.md` (board row **WS-33**). That
spec is the single authority for this surface; do not describe it elsewhere.

## What lives here

- `index.html` — one self-contained static page served at `https://metorite.com`.
  Its only job is to route a visitor into the signup flow.

## Binding rules for this subtree

- **Zero attack/consent surface.** No `<script>`, no cookies, no analytics, no
  external fetches of any kind (no CDN, no web fonts, no remote images). Imagery
  is inline SVG or `data:` URIs; typography uses a system font stack.
- **Only origin referenced is `https://app.metorite.com`.** The "Sign up" CTA is
  exactly `https://app.metorite.com/signup`; the "Sign in" link is exactly
  `https://app.metorite.com`. Relative anchors (`#features`) are fine.
- **Total page < 100 KB.**
- The workbench DESIGN_SYSTEM (eight rules) does **not** attach — this is not a
  product surface. Look-and-feel is advisory taste (marketing_site.md §3 note).

## Fence

`tests/unit/test_marketing_site.py` (R7) enforces every rule above. Run
`uv run pytest tests/unit/test_marketing_site.py -q` after any edit here.

## Not in this scope

Serving/DNS/TLS are owner-gated deploy actions (the Caddy apex block +
Hostinger apex A record), tracked in `marketing_site.md` §3 items 3–5. Nothing
under `deploy/` is edited from here.
