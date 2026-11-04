"""Seed one published AI Studio package with sample records on the LOCAL dev stack.

Slice STU-D2b-1 (`project-docs/specs/ai_studio.md` section 14). The preview route
`/studio/package-preview` needs a published package that holds records. No screen makes
one yet (A5 is routes only), so this script does it with the gateway routes.

It talks to the gateway of the local stack only (`127.0.0.1`), as the member of the
stack, with the bearer token of the stack. It refuses any other host. It makes:

1. a package from `MANIFEST` (POST /studio/packages),
2. a published version of it (POST /studio/packages/{id}/versions),
3. three records, one POST to the door for each (the `add` action). A create is a direct
   write, so the script signs the proof of the member with the direct key, as the web
   app does (`acb_auth.direct_proof.sign`).

Run it after the stack is up. The key is a value of 32 bytes or more that is not the
gateway token. The operator sets it in the shell for BOTH the stack and this script:

    export DEV_UP_STUDIO_DIRECT_KEY="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    bash scripts/dev_up.sh --stop; bash scripts/dev_up.sh      # the stack reads the key
    eval "$(bash scripts/dev_up.sh --env)"
    export STUDIO_DIRECT_KEY="$DEV_UP_STUDIO_DIRECT_KEY"
    uv run python scripts/dev_seed_studio.py

`STUDIO_ORGS=*` is already set by `dev_up.sh` for the gateway. The script is safe to run
twice: a second run finds the package and adds nothing. It never reads a `.env` file and
prints no key.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

from acb_auth.direct_proof import HEADER, sign

MEMBER = "dev@fracktal.in"

MANIFEST = {
    "studio": 1,
    "id": "dev-visits",
    "name": "Site visits (dev sample)",
    "purpose": "Plan and close customer site visits. A sample for the local preview.",
    "team": "service",
    "objects": [{
        "name": "visit",
        "label": "Visit",
        "fields": [
            {"key": "title", "type": "text", "label": "Title", "required": True},
            {"key": "status", "type": "select", "label": "Status",
             "options": ["Planned", "On site", "Closed"]},
            {"key": "qty", "type": "number", "label": "Pallets"},
            {"key": "done", "type": "checkbox", "label": "Done"},
            {"key": "site", "type": "url", "label": "Site"},
            {"key": "contact", "type": "email", "label": "Contact"},
            {"key": "notes", "type": "longText", "label": "Notes"},
        ],
    }],
    "actions": [
        {"id": "find", "uses": "object:visit.list"},
        {"id": "peek", "uses": "object:visit.get"},
        {"id": "add", "uses": "object:visit.create"},
        {"id": "change", "uses": "object:visit.update"},
        {"id": "remove", "uses": "object:visit.delete"},
    ],
}

RECORDS = [
    {"title": "Install the press", "status": "Planned", "qty": 12, "done": False,
     "site": "https://example.com/sites/pune", "contact": "ops@example.com",
     "notes": "Bring the second crane.\nGate code at the desk."},
    {"title": "Calibrate the line", "status": "Closed", "qty": 3, "done": True,
     "site": "https://example.com/sites/nashik"},
    {"title": "Train the night shift", "status": "On site", "done": False},
]


def call(base: str, token: str, method: str, path: str, body: dict | None = None,
         extra: dict[str, str] | None = None) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}", "X-User-Email": MEMBER,
               "Content-Type": "application/json", **(extra or {})}
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


def main() -> int:
    base = os.environ.get("DEV_UP_GATEWAY_URL", "").rstrip("/")
    token = os.environ.get("DEV_UP_GATEWAY_TOKEN", "")
    key = os.environ.get("STUDIO_DIRECT_KEY", "")
    if not base or not token:
        print("Run `eval \"$(bash scripts/dev_up.sh --env)\"` first.", file=sys.stderr)
        return 2
    host = urlparse(base).hostname
    if host not in ("127.0.0.1", "localhost"):
        print("This script talks to the local stack only.", file=sys.stderr)
        return 2
    if len(key.encode("utf-8")) < 32 or key == token:
        print("Set STUDIO_DIRECT_KEY: 32 bytes or more, and not the gateway token.", file=sys.stderr)
        return 2

    status, found = call(base, token, "GET", "/studio/packages")
    if status == 404:
        print("AI Studio is off for this org (STUDIO_ORGS). Nothing was made.", file=sys.stderr)
        return 1
    existing = next((p for p in found.get("packages", []) if p.get("manifest_id") == MANIFEST["id"]), None)
    if existing:
        print(f"The sample package exists: {existing['id']}")
        pid = existing["id"]
    else:
        status, made = call(base, token, "POST", "/studio/packages", MANIFEST)
        if status != 201:
            print(f"Create failed ({status}): {json.dumps(made)[:300]}", file=sys.stderr)
            return 1
        pid = made["package"]["id"]
        status, pub = call(base, token, "POST", f"/studio/packages/{pid}/versions", {"notes": "dev sample"})
        if status != 201:
            print(f"Publish failed ({status}): {json.dumps(pub)[:300]}", file=sys.stderr)
            return 1
        for record in RECORDS:
            path = f"/studio/apps/{pid}/actions/add"
            proof = sign(key, MEMBER, "POST", path)
            status, out = call(base, token, "POST", path, {"data": record}, {HEADER: proof})
            if status != 200:
                print(f"A record failed ({status}): {json.dumps(out)[:300]}", file=sys.stderr)
                return 1
        print(f"Made and published {pid} with {len(RECORDS)} records.")
    web = re.sub(r":\d+$", "", base)
    print(f"Open: /studio/package-preview?pkg={pid}&object=visit on the web port of the stack ({web}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
