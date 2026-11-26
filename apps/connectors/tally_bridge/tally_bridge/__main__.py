"""Run the bridge.

    python -m tally_bridge new-token   # print a new read token and its hash, once
    python -m tally_bridge serve       # address mode on BIND_HOST:BIND_PORT
    python -m tally_bridge setup-link  # a new setup key, and the address that opens the page
    python -m tally_bridge record-requests <folder> --company "<name>" --ledger "<name>"
    python -m tally_bridge delete-copy # delete every local copy (TLY-4)
    python -m tally_bridge apply-windows --windows      # TLY-5: sign-in task, firewall
    python -m tally_bridge uninstall-windows --windows  # TLY-5: undo every change
    python -m tally_bridge pair --domain <domain>   # link mode (TLY-2): see linkcli.py
    python -m tally_bridge confirm-link --company "<name>"
    python -m tally_bridge unlink

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
import os
import sys
from pathlib import Path
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
            "pair",
            "confirm-link",
            "unlink",
        ],
    )
    parser.add_argument("folder", nargs="?", help="record-requests: where to write the requests")
    parser.add_argument(
        "--company",
        help="record-requests: the exact Tally company name; confirm-link: the company"
        " name that the pairing showed",
    )
    parser.add_argument("--domain", help="pair: the company domain, such as example.com")
    parser.add_argument(
        "--dev-link-file", help="pair, serve: DEV ONLY, a file that holds the link credential"
    )
    parser.add_argument("--ledger", help="record-requests: a busy ledger with a known Dr balance")
    parser.add_argument("--settings-file", help="serve, setup-link: the settings file (JSON)")
    parser.add_argument(
        "--dev-secret-file", help="serve, setup-link: DEV ONLY, a file that holds the hashes"
    )
    parser.add_argument(
        "--dev-copy-key-file", help="serve, delete-copy: DEV ONLY, the key of the local copy"
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="setup-link: wait until the bridge answers (the installer)",
    )
    parser.add_argument(
        "--url-file",
        help="setup-link: write the address to this file and print nothing (the installer)",
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
    if args.command in ("pair", "confirm-link", "unlink"):
        return _link_command(args)

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


def _apply_windows(args: argparse.Namespace, runner: Any = None, check_folder: Any = None) -> int:
    """Make the machine match the settings (TLY-5): the sign-in task of
    TallyPrime, and one firewall rule. The installer runs it, and so does a
    person after a change of the settings."""
    from tally_bridge.windows import system

    run = runner or system.run
    settings = _stores(args)[0].load()
    failed = 0
    refused = False
    from tally_bridge.windows.acl import UnsafeTallyFolder

    if settings.start_tally_at_signin or _had_startup(run):
        try:
            failed += (
                system.apply_startup(
                    settings.start_tally_at_signin, settings.tally_exe, run, check_folder
                )
                != 0
            )
        except UnsafeTallyFolder as exc:  # task 3974: the folder is not locked
            print(str(exc), file=sys.stderr)
            refused = True
    # Delete, then add: one rule, whatever ran before.
    run(system.firewall_off())
    failed += run(system.firewall_on()) != 0
    if refused:
        print("The firewall rule is applied. The sign-in task is off: see the message above.")
        return 1
    print("Applied." if not failed else f"{failed} change(s) failed. Run as an administrator.")
    return 0 if not failed else 1


def _machine_check(windows: bool, check_folder: Any = None) -> Any:
    """Task 3974: for the setup page, why the sign-in task is off, or None.
    Without ``--windows`` there is no task, so there is no check."""
    if not windows:
        return None

    def problem(settings: Settings) -> str | None:
        from tally_bridge.windows import acl

        try:
            (check_folder or acl.check)(settings.tally_exe)
        except acl.UnsafeTallyFolder as exc:
            return str(exc)
        return None

    return problem


def _signin_guard_at_start(
    settings: Settings, runner: Any = None, check_folder: Any = None
) -> str | None:
    """Task 3974: at each start of the service, check the TallyPrime folder
    again. When a user can now change the program (a support person loosened
    the folder after setup), remove the sign-in task. Returns the message."""
    if not settings.start_tally_at_signin:
        return None
    from tally_bridge.windows import acl, system

    try:
        (check_folder or acl.check)(settings.tally_exe)
    except acl.UnsafeTallyFolder as exc:
        (runner or system.run)(system.startup_off())
        logging.getLogger("tally_bridge").warning("%s", exc)
        return str(exc)
    return None


def _machine_applier(
    windows: bool, awake: Any, runner: Any = None, check_folder: Any = None
) -> Any:
    """The callback of the setup page (TLY-2d). It makes the machine match the
    two switches at once, and returns an error text, or None. Without
    ``--windows`` (a dev box) it only saves, so it returns None to the caller."""
    if not windows:
        return None

    def startup(new: Settings) -> str | None:
        """The schtasks part. It runs in a worker thread (two processes)."""
        from tally_bridge.windows import system
        from tally_bridge.windows.acl import UnsafeTallyFolder

        run = runner or system.run
        wanted = new.start_tally_at_signin or _had_startup(run)
        try:
            if wanted and (
                system.apply_startup(new.start_tally_at_signin, new.tally_exe, run, check_folder)
                != 0
            ):
                return "Windows did not take the sign-in task. Restart the bridge, then try again."
        except UnsafeTallyFolder as exc:  # task 3974: the folder is not locked
            return str(exc)
        return None

    async def apply(new: Settings) -> str | None:
        # Keep-awake stays on this thread: the request handlers run on the
        # thread of ``_awake_while``, which ``SetThreadExecutionState`` binds to.
        if new.keep_awake:
            awake.start()
        else:
            awake.stop()
        # schtasks blocks for up to a second, so it runs off the event loop.
        try:
            return await asyncio.to_thread(startup, new)
        except Exception as exc:
            logging.getLogger("tally_bridge").warning("the sign-in task failed (%s)", exc)
            return "The switches are saved, but the sign-in task did not change."

    return apply


def _had_startup(run: Any) -> bool:
    """True when the sign-in task may exist, so that turning the setting off
    removes it. ``schtasks /Query`` answers 0 for a task that exists."""
    from tally_bridge.windows import system

    return run(["schtasks", "/Query", "/TN", system.TASK_NAME]) == 0


def _link_command(args: argparse.Namespace) -> int:
    from tally_bridge import linkcli

    try:
        store = linkcli.link_store(args)
        if args.command == "confirm-link":
            return linkcli.do_confirm(args, store)
        settings = _stores(args)[0].load()
        if args.command == "pair":
            return linkcli.do_pair(args, settings, store)
        return linkcli.do_unlink(settings, store)
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except PermissionError:
        # The data folder gives a user no right at all (TLY-2).
        print("Run this command as an administrator.", file=sys.stderr)
        return 2


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
    _unlink_quietly(args, settings_store)
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


def _unlink_quietly(args: argparse.Namespace, settings_store: SettingsStore) -> None:
    """TLY-2 (§3.8.6): an uninstall revokes the link credential while the
    link is up, and deletes it here in any case."""
    from tally_bridge import linkcli

    store = linkcli.link_store(args)
    if store is None or store.load() is None:
        return
    try:
        settings = settings_store.load()
    except SettingsError:
        settings = Settings()
    linkcli.do_unlink(settings, store)


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
    url = f"http://127.0.0.1:{port}/setup?k={key}"
    if getattr(args, "wait", False) and not _wait_up(f"http://127.0.0.1:{port}/health"):
        print("The bridge did not start. Open the setup page later.", file=sys.stderr)
        if not getattr(args, "url_file", None):
            print(url, file=sys.stderr)
        return 1
    if getattr(args, "url_file", None):
        # The installer: the elevated step writes the address here, and a
        # second step opens it as the person who signed in (Inno
        # runasoriginaluser), so the key never opens in an administrator's
        # browser.
        _write_private(Path(args.url_file), url)
        return 0
    print("Open this address on this computer (shown once):")
    print(url)
    return 0


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def _wait_up(health_url: str, timeout_s: float = 60.0, get: Any = None) -> bool:
    """True once *health_url* answers 200, within *timeout_s*."""
    import time

    import httpx

    get = get or (lambda u: httpx.get(u, timeout=2.0).status_code)
    end = time.monotonic() + timeout_s
    while True:
        try:
            if get(health_url) == 200:
                return True
        except httpx.HTTPError:
            pass
        if time.monotonic() >= end:
            return False
        time.sleep(1.0)


def serve_until(stop: Any) -> None:
    """The service's server (TLY-5 PR B): the product stores of ``--windows``,
    until *stop* (a ``threading.Event``) is set. Unlike ``serve``, it starts
    with no token and no setup key yet: the installer starts the service
    before the customer opens the setup page, and both guards refuse every
    request until a key exists."""
    args = argparse.Namespace(
        windows=True, settings_file=None, dev_secret_file=None, dev_copy_key_file=None
    )
    code = _serve(args, stop=stop, need_secret=False)
    if code != 0:
        raise RuntimeError(f"The bridge could not start (code {code}).")


def _serve(args: argparse.Namespace, stop: Any = None, need_secret: bool = True) -> int:
    import uvicorn

    from tally_bridge.transport import build_address_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Link mode (TLY-2): one httpx line per poll and per request is noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    store, secrets = _stores(args)
    settings = store.load()
    open_call_log(settings.log_dir)
    if isinstance(secrets, DevFileSecretStore):
        logging.getLogger("tally_bridge").warning(
            "DEV ONLY: the hashes are in a plain file, not in DPAPI. Never ship this."
        )
    if (
        need_secret
        and secrets.read_token_sha256() is None
        and secrets.setup_secret_sha256() is None
    ):
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
    from tally_bridge import linkcli

    controller = linkcli.link_controller(args, live, reader, copy)
    from tally_bridge.windows.system import KeepAwake

    awake = KeepAwake()
    app = build_address_app(
        live,
        secrets,
        reader,
        store,
        copy,
        link=controller,
        on_machine_saved=_machine_applier(getattr(args, "windows", False), awake),
        machine_check=_machine_check(getattr(args, "windows", False)),
    )
    if getattr(args, "windows", False):
        _signin_guard_at_start(settings)
    link = None
    if controller is not None:
        from tally_bridge.transport.link.dispatch import McpDispatcher

        controller.dispatch = McpDispatcher(app.inner, settings.bind_port)
        link = controller.run
    server = uvicorn.Server(uvicorn.Config(app, **_server_options(settings)))
    _awake_while(
        settings.keep_awake,
        lambda: asyncio.run(_serve_with_copy(server, reader, copy, live, stop, link)),
        awake=awake,
    )
    return 0


#: TLY-5. Seconds that a stop waits for open requests (a slow Tally call)
#: before uvicorn cancels them, so a service stop ends well inside the 30 s
#: that Windows waits.
GRACEFUL_SHUTDOWN_S = 10


def _server_options(settings: Settings) -> dict[str, Any]:
    """The options of ``uvicorn.Config``, one place for ``serve`` and the
    service."""
    return {
        "host": settings.bind_host,
        "port": settings.bind_port,
        "proxy_headers": False,
        "server_header": False,
        # The setup page takes its key in the address. No access log, so the
        # key never reaches a log file.
        "access_log": False,
        "timeout_graceful_shutdown": GRACEFUL_SHUTDOWN_S,
    }


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


async def _serve_with_copy(
    server: Any,
    reader: Any,
    copy: CopyStore | None,
    live: Any,
    stop: Any = None,
    link: Any = None,
) -> None:
    """Run the server, and the copy loop beside it when the copy is on. With
    *stop* (a ``threading.Event``), the server ends when it is set: the
    Windows service sets it on a stop request. With *link* (TLY-2), the link
    loop runs beside them, and it ends with the server."""
    from tally_bridge.localcopy.schedule import run_forever

    loop_task = asyncio.create_task(run_forever(reader, copy, live)) if copy else None
    link_stop = asyncio.Event()
    stop_task = (
        asyncio.create_task(_stop_when(stop, server, link_stop.set)) if stop is not None else None
    )
    link_task = asyncio.create_task(link(link_stop)) if link else None
    try:
        await server.serve()
    finally:
        link_stop.set()
        for task in (loop_task, stop_task):
            if task is not None:
                task.cancel()
        if link_task is not None:
            try:
                await asyncio.wait_for(link_task, 5)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                logging.getLogger("tally_bridge.link").exception("the link loop failed")


async def _stop_when(stop: Any, server: Any, on_stop: Any = None) -> None:
    """Ask *server* to exit once *stop* is set. ``uvicorn.Server`` reads
    ``should_exit`` in its loop. *on_stop* runs first: the link stops polling
    at once, not after the graceful window of the server (TLY-2)."""
    while not stop.is_set():
        await asyncio.sleep(0.2)
    if on_stop is not None:
        on_stop()
    server.should_exit = True


if __name__ == "__main__":
    raise SystemExit(main())
