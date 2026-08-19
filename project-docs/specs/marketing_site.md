# Marketing Site — the public face on the apex (`metorite.com`)

**Status: 🟡 SPEC MINTED 2026-08-19 (D46, owner directive) — verified against
code 2026-08-19. Nothing is built.** Owning spec for board row **WS-33**. The
apex has been reserved for exactly this since D41 (`work_plan.md:658`: *"The
apex is reserved for a future marketing site"*). This spec is deliberately
small: the owner asked for a **basic** website whose one job is to route a
visitor into the signup flow (CP-2c, `customer_console.md`). Everything a real
marketing site wants — content, SEO, pricing pages, a blog — is a non-goal
here and gets its own ticket when the owner asks.

---

## 1. Scope

One static page served by the existing Caddy instance at `https://metorite.com`
(+ `www.` redirecting to the apex), containing: the product name, a one-line
description, a **"Sign up"** call-to-action pointing at the signup entry URL
CP-2c owns (`https://app.metorite.com/signup`), and a **"Sign in"** link to
`https://app.metorite.com`. No JavaScript, no cookies, no analytics, no
external assets — a page with zero consent surface and zero attack surface.

**Non-goals:** real marketing content, SEO, pricing/feature pages, a blog, a
CMS, analytics or tracking of any kind, cookie banners, screenshots that would
need refreshing, per-tenant subdomains (MT-1f), and the signup flow itself
(CP-2c). Also NOT this spec's: making `app.metorite.com/signup` exist — that
is CP-2c; until it lands, deploying this page ships a CTA that 404s, so the
board row sequences this **with or after** CP-2c.

## 2. Current state (verified 2026-08-19)

- DNS: `app.` / `api.` / wildcard `*.metorite.com` A records are live
  (verified 2026-08-19 — `anycustomer.metorite.com` resolves). ⚠️ **The
  wildcard does not cover the apex**; `metorite.com` itself needs its own A
  (or ALIAS) record — an owner act in the Hostinger DNS panel.
- Caddy serves two vhosts from `deploy/hostinger/caddy/Caddyfile:13-24`
  (`api.` → `127.0.0.1:8080`, `app.` → `127.0.0.1:3001`) with auto-TLS. The
  apex is one added block.
- ⚠️ Finding, recorded not fixed (deploy/ is plan-guard-gated): the ACME
  registration email at `Caddyfile:8` is still `ops@fracktal.in` — a rebrand
  leftover; certificate-expiry alerts for metorite.com go to the old domain.
- No `site/` directory exists in the repo; nothing serves the apex today.

## 3. Deliverables and acceptance

| # | Item | Done when | Gate |
|---|---|---|---|
| 1 | `site/index.html` — one self-contained page | The file exists at the repo root under `site/`; contains the two links with EXACT hrefs `https://app.metorite.com/signup` and `https://app.metorite.com`; contains no `<script` tag, no cookie use, and references no origin other than `app.metorite.com` (links only — no fetched assets at all); total size < 100 KB | AGENT-SAFE |
| 2 | The fence (R7) | `tests/unit/test_marketing_site.py` reads `site/index.html` (with `encoding="utf-8"`) and fails on: a `<script` tag · any `src=`/`href=` to an origin other than `https://app.metorite.com` · absence of either exact CTA href · file ≥ 100 KB. Verified red-first by planting each violation | AGENT-SAFE |
| 3 | Caddy apex block | A ready-made patch travels in the PR description (NOT a commit — `deploy/` is plan-guard-gated): `metorite.com { root * /opt/acb/app/site, encode zstd gzip, file_server }` plus `www.metorite.com { redir https://metorite.com{uri} permanent }`. Applied on the box by the owner or a D45-granted session, then `sudo systemctl reload caddy` | OWNER-GATE (§6 deploy) |
| 4 | Apex DNS | `metorite.com` A record → the VPS, added in the Hostinger panel; `nslookup metorite.com` resolves to the box | OWNER-GATE |
| 5 | Live verification | `curl -s -o /dev/null -w '%{http_code}' https://metorite.com` → `200` and `curl -s https://metorite.com | grep -c "app.metorite.com/signup"` → ≥ 1, run on the box or from any host after 3+4 | OWNER-GATE (needs 3+4) |

**Design note (argued deviation from the DESIGN_SYSTEM contract):** the
workbench design system (`workbench/control_plane/DESIGN_SYSTEM.md`) binds
product surfaces — apps projected from one platform. This page is not a
product surface and imports none of the app's code, so the eight rules and
their fences do not attach; it should still *look* like Metorite (same
name treatment, restrained palette), which is a taste requirement, labelled
advisory per R7.

## 4. Verification commands

```
uv run pytest tests/unit/test_marketing_site.py -q      # the fence, hermetic
# after owner acts 3+4:
curl -s -o /dev/null -w '%{http_code}\n' https://metorite.com   # 200
curl -s https://metorite.com | grep -c "app.metorite.com/signup" # >= 1
```

## 5. Ownership

This spec is the single owner of the apex surface. `work_plan.md` WS-33 is the
board row. CP-2c (`customer_console.md`) owns the URL the CTA points at;
D41/D40 own hostname topology; MT-1f owns per-tenant subdomains. No other doc
may describe this page.
