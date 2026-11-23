"""Run the bridge.

    python -m tally_bridge new-token   # print a new read token and its hash, once
    python -m tally_bridge serve       # address mode on BIND_HOST:BIND_PORT
    python -m tally_bridge setup-link  # a new setup key, and the address that opens the page
    python -m tally_bridge record-requests <folder> --company "<name>" --ledger "<name>"
    python -m tally_bridge delete-copy # delete every local copy (TLY-4)

On a dev box the settings and the hashes come from the environment
(``TALLY_BRIDGE_*``), or from two files: ``--settings-file`` (the settings,
which the setup page writes) and ``--dev-secret-file`` (the hashes, DEV ONLY).
The local copy (TLY-4) is on when COPY_DIR is set. On a dev box its key is in
``--dev-copy-key-file`` (DEV ONLY). Step 4 of the build order adds the Windows stores
(DPAPI) and the installer, which runs ``setup-link`` itself.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from tally_bridge import __version__
from tally_bridge.calllog import open_call_log
from tally_bridge.localcopy import CopyStore, DevFileKeyProvider
from tally_bridge.reader import TallyClient
from tally_bridge.secret_store import (
    DevFileSecretStore,
    EnvSecretStore,
    ReadOnlySecretStore,
    SecretStore,
    new_token,
    token_sha256,
)
from tally_bridge.settings import (
    EnvSettingsStore,
    FileSettingsStore,
    LiveSettings,
    Settings,
    SettingsError,
    SettingsStore,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tally_bridge")
    parser.add_argument(
        "command",
        choices=["serve", "new-token", "version", "record-requests", "setup-link", "delete-copy"],
    )
    parser.add_argument("folder", nargs="?", help="record-requests: where to write the requests")
    parser.add_argument("--company", help="record-requests: the exact Tally company name")
    parser.add_argument("--ledger", help="record-requests: a busy ledger with a known Dr balance")
    parser.add_argument("--settings-file", help="serve, setup-link: the settings file (JSON)")
    parser.add_argument(
        "--dev-secret-file", help="serve, setup-link: DEV ONLY, a file that holds the hashes"
    )
    parser.add_argument(
        "--dev-copy-key-file", help="serve, delete-copy: DEV ONLY, the key of the local copy"
    )
    args = parser.parse_args(argv)

    if args.command == "record-requests":
        return _record(args)
    if args.command == "setup-link":
        return _setup_link(args)
    if args.command == "delete-copy":
        return _delete_copy(args)

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
    return _serve(args)


def _record(args: argparse.Namespace) -> int:
    from pathlib import Path

    from tally_bridge.reader.recording import write_requests

    if not (args.folder and args.company and args.ledger):
        print("record-requests needs a folder, --company and --ledger.", file=sys.stderr)
        return 2
    for path in write_requests(Path(args.folder), args.company, args.ledger):
        print(path)
    return 0


def _stores(args: argparse.Namespace) -> tuple[SettingsStore, SecretStore]:
    settings_file = args.settings_file
    secret_file = args.dev_secret_file
    settings: SettingsStore = (
        FileSettingsStore(settings_file) if settings_file else EnvSettingsStore()
    )
    secrets: SecretStore = DevFileSecretStore(secret_file) if secret_file else EnvSecretStore()
    return settings, secrets


def _copy_store(args: argparse.Namespace, settings: Settings) -> CopyStore | None:
    """The local copy, or None when COPY_DIR is empty. Step 4 adds the DPAPI
    key on Windows. Until then the copy needs the DEV ONLY key file."""
    if not settings.copy_dir:
        return None
    if not args.dev_copy_key_file:
        raise SettingsError("COPY_DIR is set. Pass --dev-copy-key-file (DEV ONLY) for its key.")
    return CopyStore(settings.copy_dir, DevFileKeyProvider(args.dev_copy_key_file))


def _delete_copy(args: argparse.Namespace) -> int:
    settings_store, _secrets = _stores(args)
    folder = settings_store.load().copy_dir
    if not folder:
        print("The local copy is off (COPY_DIR is empty). Nothing to delete.")
        return 0
    # Deleting needs no key: the files go, readable or not.
    gone = CopyStore(folder, DevFileKeyProvider(args.dev_copy_key_file or "")).delete_all()
    print(f"Deleted {gone} local copies.")
    return 0


def _setup_link(args: argparse.Namespace) -> int:
    settings_store, secrets = _stores(args)
    key = new_token()
    try:
        secrets.set_setup_secret_sha256(token_sha256(key))
    except ReadOnlySecretStore:
        print(
            "setup-link needs a store that can write. Pass --dev-secret-file.",
            file=sys.stderr,
        )
        return 2
    port = settings_store.load().bind_port
    print("Open this address on this computer (shown once):")
    print(f"http://127.0.0.1:{port}/setup?k={key}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from tally_bridge.transport import build_address_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store, secrets = _stores(args)
    settings = store.load()
    open_call_log(settings.log_dir)
    if isinstance(secrets, DevFileSecretStore):
        logging.getLogger("tally_bridge").warning(
            "DEV ONLY: the hashes are in a plain file, not in DPAPI. Never ship this."
        )
    if secrets.read_token_sha256() is None and secrets.setup_secret_sha256() is None:
        print(
            "No read token and no setup key. Run `setup-link` or `new-token` first.",
            file=sys.stderr,
        )
        return 2
    try:
        copy = _copy_store(args, settings)
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    reader = TallyClient(settings.tally_url, settings.tally_timeout_s)
    live = LiveSettings(settings)
    app = build_address_app(live, secrets, reader, store, copy)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.bind_host,
            port=settings.bind_port,
            proxy_headers=False,
            server_header=False,
            # The setup page takes its key in the address. No access log, so
            # the key never reaches a log file.
            access_log=False,
        )
    )
    asyncio.run(_serve_with_copy(server, reader, copy, live))
    return 0


async def _serve_with_copy(server: Any, reader: Any, copy: CopyStore | None, live: Any) -> None:
    """Run the server, and the copy loop beside it when the copy is on."""
    from tally_bridge.localcopy.schedule import run_forever

    loop_task = asyncio.create_task(run_forever(reader, copy, live)) if copy else None
    try:
        await server.serve()
    finally:
        if loop_task is not None:
            loop_task.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
