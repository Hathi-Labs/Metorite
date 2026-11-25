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
        choices=[
            "serve",
            "new-token",
            "version",
            "record-requests",
            "setup-link",
            "delete-copy",
            "apply-windows",
            "uninstall-windows",
        ],
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
    parser.add_argument(
        "--windows",
        action="store_true",
        help="The product stores of a customer's machine: the settings, the DPAPI hashes"
        " and the DPAPI copy key under %%ProgramData%%\\TallyBridge (TLY-5)",
    )
    args = parser.parse_args(argv)

    if args.command == "record-requests":
        return _record(args)
    if args.command == "setup-link":
        return _setup_link(args)
    if args.command == "delete-copy":
        return _delete_copy(args)
    if args.command == "apply-windows":
        return _apply_windows(args)
    if args.command == "uninstall-windows":
        return _uninstall_windows(args)

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
    if getattr(args, "windows", False):
        from tally_bridge.windows import stores
        from tally_bridge.windows.dpapi import DpapiProtector

        folder = stores.data_dir()
        return (
            FileSettingsStore(folder / "settings.json"),
            stores.DpapiSecretStore(folder / stores.SECRETS_FILE, DpapiProtector()),
        )
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
    if getattr(args, "windows", False):
        from tally_bridge.windows import stores
        from tally_bridge.windows.dpapi import DpapiProtector

        key = stores.DpapiKeyProvider(stores.data_dir() / stores.COPY_KEY_FILE, DpapiProtector())
        return CopyStore(settings.copy_dir, key)
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


def _apply_windows(args: argparse.Namespace, runner: Any = None) -> int:
    """Make the machine match the settings (TLY-5): the sign-in task of
    TallyPrime, and one firewall rule. The installer runs it, and so does a
    person after a change of the settings."""
    from tally_bridge.windows import system

    run = runner or system.run
    settings = _stores(args)[0].load()
    failed = 0
    if settings.start_tally_at_signin or _had_startup(run):
        failed += system.apply_startup(settings.start_tally_at_signin, settings.tally_exe, run) != 0
    # Delete, then add: one rule, whatever ran before.
    run(system.firewall_off())
    failed += run(system.firewall_on()) != 0
    print("Applied." if not failed else f"{failed} change(s) failed. Run as an administrator.")
    return 0 if not failed else 1


def _had_startup(run: Any) -> bool:
    """True when the sign-in task may exist, so that turning the setting off
    removes it. ``schtasks /Query`` answers 0 for a task that exists."""
    from tally_bridge.windows import system

    return run(["schtasks", "/Query", "/TN", system.TASK_NAME]) == 0


def _uninstall_windows(args: argparse.Namespace, runner: Any = None) -> int:
    """Undo every change of the program (brief §6.7): the sign-in task, the
    firewall rule, the stored hashes, the copy key and the local copy."""
    from tally_bridge.windows import stores, system

    run = runner or system.run
    # The machine changes go first, so a damaged settings file cannot leave
    # the task or the firewall rule behind (review finding 2). Each delete
    # answers non-zero when there is nothing to delete, so the exit codes say
    # nothing here.
    run(system.startup_off())
    run(system.firewall_off())
    settings_store, secrets = _stores(args)
    if isinstance(secrets, stores.DpapiSecretStore):
        secrets.delete()
    if getattr(args, "windows", False):
        (stores.data_dir() / stores.COPY_KEY_FILE).unlink(missing_ok=True)
    try:
        copy_dir = settings_store.load().copy_dir
    except SettingsError as exc:
        print(
            f"Removed the sign-in task, the firewall rule and the stored secrets. "
            f"The settings file is damaged ({exc}), so delete the local copy by hand."
        )
        return 1
    if copy_dir:
        CopyStore(copy_dir, DevFileKeyProvider("")).delete_all()
    print("Removed the sign-in task, the firewall rule, the stored secrets and the local copy.")
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
    _awake_while(
        settings.keep_awake, lambda: asyncio.run(_serve_with_copy(server, reader, copy, live))
    )
    return 0


def _awake_while(keep_awake: bool, run: Any, awake: Any = None) -> None:
    """Run *run*, and keep the machine awake meanwhile when the setting is on.
    The request ends when *run* returns or raises (TLY-5)."""
    from tally_bridge.windows.system import KeepAwake

    awake = awake or KeepAwake()
    if keep_awake:
        awake.start()
    try:
        run()
    finally:
        awake.stop()


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
