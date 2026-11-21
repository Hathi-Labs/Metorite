"""Run the bridge.

    python -m tally_bridge new-token   # print a new read token and its hash, once
    python -m tally_bridge serve       # address mode on BIND_HOST:BIND_PORT

On a dev box the settings and the token hash come from the environment
(``TALLY_BRIDGE_*``). Step 4 of the build order adds the Windows stores and
the setup screen.
"""

from __future__ import annotations

import argparse
import logging
import sys

from tally_bridge import __version__
from tally_bridge.reader import TallyClient
from tally_bridge.secret_store import EnvSecretStore, new_token, token_sha256
from tally_bridge.settings import EnvSettingsStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tally_bridge")
    parser.add_argument("command", choices=["serve", "new-token", "version"])
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "new-token":
        token = new_token()
        print("Read token (shown once; give it to the Metorite admin):")
        print(token)
        print("Its SHA-256 (set TALLY_BRIDGE_READ_TOKEN_SHA256 to this):")
        print(token_sha256(token))
        return 0
    return _serve()


def _serve() -> int:
    import uvicorn

    from tally_bridge.transport import build_address_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = EnvSettingsStore().load()
    secrets = EnvSecretStore()
    if secrets.read_token_sha256() is None:
        print("No read token. Run `python -m tally_bridge new-token` first.", file=sys.stderr)
        return 2
    reader = TallyClient(settings.tally_url, settings.tally_timeout_s)
    app = build_address_app(settings, secrets, reader)
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        proxy_headers=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
