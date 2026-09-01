"""Ping every infra service and report status. Used by bootstrap and CI.

Usage:
    uv run python scripts/check_infra.py
Exits non-zero if any required service is unreachable.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.request import urlopen

import psycopg
import redis
from acb_common import get_settings

OK = "  OK   "
FAIL = "  FAIL "


def _retry(probe, *, attempts: int, delay: float = 2.0) -> bool:
    """Call probe() repeatedly until it returns True or attempts run out."""
    last_err: str | None = None
    for _ in range(attempts):
        try:
            if probe():
                return True
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(delay)
    if last_err:
        print(f"      last error: {last_err}")
    return False


def check_postgres(url: str) -> bool:
    libpq = url.replace("postgresql+psycopg://", "postgresql://", 1)

    def probe() -> bool:
        with psycopg.connect(libpq, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("select extname from pg_extension;")
            exts = {r[0] for r in cur.fetchall()}
            need = {"vector", "uuid-ossp"}
            missing = need - exts
            if missing:
                print(f"{FAIL}postgres up but missing extensions {sorted(missing)} (found {sorted(exts)})")
                return False
            print(f"{OK}postgres + extensions {sorted(need)}")
            return True

    if _retry(probe, attempts=15):
        return True
    print(f"{FAIL}postgres @ {libpq}")
    return False


def check_redis(url: str) -> bool:
    def probe() -> bool:
        redis.from_url(url, socket_connect_timeout=3).ping()
        print(f"{OK}redis")
        return True
    if _retry(probe, attempts=10):
        return True
    print(f"{FAIL}redis @ {url}")
    return False


def check_gateway(port: int = 8000) -> bool:
    """Check the gateway's health endpoint (no LiteLLM proxy needed)."""
    url = f"http://localhost:{port}/health"

    def probe() -> bool:
        with urlopen(url, timeout=3) as r:
            if r.status == 200:
                print(f"{OK}gateway @ http://localhost:{port}")
                return True
            return False

    if _retry(probe, attempts=10, delay=2):
        return True
    print(f"{FAIL}gateway @ {url}")
    return False


def check_env_duplicates(path: str = ".env") -> bool:
    """Fail if a key is defined twice in the env file.

    ⚠️ A DOUBLED KEY RESOLVES SILENTLY, BY LINE ORDER. systemd's
    EnvironmentFile and pydantic-settings both take the LAST value, so
    the file reads as correct while carrying a contradiction.

    Measured on production 2026-09-01: `ACB_ENV` appeared twice, `dev` at
    line 212 and `prod` at line 329. The box was serving correctly, and
    only because somebody appended the fix at the end. Anything inserting
    near line 212, or any reader taking the FIRST match, would have
    re-opened H-90 and republished the whole API schema.

    Nothing checked for this. One duplicate in 142 keys was found by
    accident, which is not a detection method.

    Prints key NAMES only, never values — this runs where secrets live.
    """
    p = Path(path)
    if not p.exists():
        print(f"{OK}env duplicates: no {path} to check")
        return True

    seen: dict[str, list[int]] = {}
    for n, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key and key.replace("_", "").isalnum():
            seen.setdefault(key, []).append(n)

    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if not dupes:
        print(f"{OK}env duplicates: none in {len(seen)} keys")
        return True

    for key, lines in sorted(dupes.items()):
        nums = ", ".join(str(n) for n in lines)
        # ASCII only. This prints to a Windows console often enough, and
        # cp1252 raises UnicodeEncodeError on an em-dash (CLAUDE.md §6).
        # A check that crashes while reporting a fault reports nothing.
        print(f"{FAIL}{key} defined {len(lines)}x (lines {nums}) - last wins")
    return False


def main() -> int:
    s = get_settings()
    print(f"env: {s.acb_env}")
    results = [
        check_env_duplicates(),
        check_postgres(s.database_url),
        check_redis(s.redis_url),
        check_gateway(s.gateway_port),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())