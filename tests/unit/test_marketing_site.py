"""R7 fence for the marketing landing page (WS-33, ``marketing_site.md`` §3).

The apex page at ``site/index.html`` is deliberately a page with **zero consent
surface and zero attack surface**: no JavaScript, no cookies, no external
assets, and exactly two outbound links — both to ``https://app.metorite.com``.
This fence is the machine check that makes those promises fail loudly if a
future edit breaks them (root ``AGENTS.md`` R7: name the test that makes
breaking the rule fail).

The four rules this fence enforces, from the spec's acceptance row:
  * no ``<script`` tag anywhere;
  * every ``href=``/``src=`` that names an origin points at
    ``https://app.metorite.com`` and nowhere else (no fetched assets, no
    third-party origins, no cookies-by-beacon);
  * both exact call-to-action hrefs are present — the ``/signup`` CTA and the
    bare sign-in link;
  * the whole page stays under 100 KB.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

# tests/unit/test_marketing_site.py -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_HTML = REPO_ROOT / "site" / "index.html"

MAX_BYTES = 100 * 1024  # 100 KB hard ceiling from the spec.
ALLOWED_ORIGIN = "https://app.metorite.com"
# The one host every remote reference must resolve to. Derived from the origin
# so there is a single source of truth; urlsplit lower-cases the host for us.
ALLOWED_HOST = urlsplit(ALLOWED_ORIGIN).hostname  # "app.metorite.com"
SIGNUP_HREF = "https://app.metorite.com/signup"

# Every attribute value that could name an origin. Matches both quote styles.
_URL_ATTR = re.compile(r"""(?:href|src)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def _read_html() -> str:
    # encoding="utf-8" is mandatory on Windows dev boxes (cp1252 default crashes
    # on the non-ASCII glyphs in the page). See root CLAUDE.md environment notes.
    return SITE_HTML.read_text(encoding="utf-8")


def test_site_html_exists() -> None:
    assert SITE_HTML.is_file(), f"marketing page missing at {SITE_HTML}"


def test_signup_cta_href_is_exact() -> None:
    html = _read_html()
    assert SIGNUP_HREF in html, (
        f"the sign-up CTA must link to the exact URL {SIGNUP_HREF!r}"
    )


def test_signin_link_is_its_own_exact_href() -> None:
    # ``https://app.metorite.com/signup`` contains ``https://app.metorite.com``
    # as a prefix, so a bare substring check would pass even without a real
    # sign-in link. Require the sign-in link as its own complete href value.
    html = _read_html()
    assert f'href="{ALLOWED_ORIGIN}"' in html, (
        f'the sign-in link must exist as its own href="{ALLOWED_ORIGIN}" '
        "(not merely as a prefix of the sign-up URL)"
    )


def test_no_script_tag() -> None:
    html = _read_html()
    assert "<script" not in html.lower(), (
        "the marketing page must contain no <script tag (no JavaScript at all)"
    )


def test_no_foreign_origin_referenced() -> None:
    # Scan every href/src value. Any value that references a remote host must
    # resolve to the EXACT origin https://app.metorite.com. We PARSE each URL and
    # compare its real scheme + host rather than matching a string prefix, so
    #   * a look-alike prefix  https://app.metorite.com.evil.com/... (host is
    #     app.metorite.com.evil.com),
    #   * a userinfo smuggle    https://app.metorite.com@evil.com/...  (host is
    #     evil.com),
    #   * a protocol-relative   //evil.com/...  (host is evil.com, no https),
    #   * and any non-https scheme pointing at a host
    # are all rejected. Values that reference no host — anchors (#top), relative
    # paths, and self-contained data: URIs (site/AGENTS.md permits inline SVG /
    # data: imagery) — name no origin and are allowed.
    html = _read_html()
    offenders: list[str] = []
    for value in _URL_ATTR.findall(html):
        v = value.strip()
        try:
            parts = urlsplit(v)
            references_host = parts.hostname is not None or v.startswith("//")
            allowed = (
                parts.scheme == "https"
                and parts.hostname == ALLOWED_HOST
                and parts.port is None
                and parts.username is None
                and parts.password is None
            )
        except ValueError:
            # Malformed URL (e.g. a bad port). A zero-surface page has no such
            # thing, so treat it as an offender rather than letting it slip past.
            references_host, allowed = True, False
        if references_host and not allowed:
            offenders.append(v)
    assert not offenders, (
        "the marketing page may only reference the exact origin "
        f"{ALLOWED_ORIGIN}; foreign or malformed origins found: {offenders}"
    )


def test_no_cookie_use() -> None:
    # A static page has no business setting cookies; guard the obvious vectors.
    html = _read_html().lower()
    assert "document.cookie" not in html, "the page must not touch cookies"
    assert 'http-equiv="set-cookie"' not in html, "the page must not set cookies"


def test_size_under_100kb() -> None:
    size = SITE_HTML.stat().st_size
    assert size < MAX_BYTES, (
        f"the marketing page is {size} bytes; it must stay under {MAX_BYTES}"
    )


if __name__ == "__main__":  # pragma: no cover - convenience runner
    raise SystemExit(pytest.main([__file__, "-q"]))
