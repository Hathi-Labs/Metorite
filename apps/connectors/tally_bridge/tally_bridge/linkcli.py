"""The commands of link mode (TLY-2), and the link loop of ``serve``.

    python -m tally_bridge pair --domain example.com   # show a code for the admin
    python -m tally_bridge confirm-link --company "<the name it showed>"
    python -m tally_bridge unlink                      # refuse, or disconnect

Rule j (``integrations_console.md`` §3.8.7): after the pairing, the program
shows the company name that the approval returned, and serves nothing until
the customer confirms it. ``confirm-link`` needs the customer to type that
name. ``unlink`` revokes the credential at Metorite and deletes it here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from tally_bridge import __version__
from tally_bridge.settings import Settings
from tally_bridge.transport.link import protocol as p
from tally_bridge.transport.link.api import LinkApi
from tally_bridge.transport.link.pairing import pair
from tally_bridge.transport.link.store import (
    BlobLinkStore,
    DevFileLinkStore,
    LinkState,
    LinkStore,
)

log = logging.getLogger("tally_bridge.link")


def link_store(args: argparse.Namespace) -> LinkStore | None:
    """The DPAPI blob on Windows, the DEV ONLY file on a dev box, or None."""
    if getattr(args, "windows", False):
        from tally_bridge.windows import stores
        from tally_bridge.windows.dpapi import DpapiProtector

        return BlobLinkStore(
            stores.DpapiBlobFile(stores.data_dir() / stores.LINK_FILE, DpapiProtector())
        )
    path = getattr(args, "dev_link_file", None)
    return DevFileLinkStore(path) if path else None


def do_pair(
    args: argparse.Namespace,
    settings: Settings,
    store: LinkStore | None,
    api: LinkApi | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    if not settings.link_url:
        print("Set LINK_URL, the address of Metorite, first.", file=sys.stderr)
        return 2
    if store is None:
        print(
            "pair needs a link store. Pass --windows, or --dev-link-file (DEV ONLY).",
            file=sys.stderr,
        )
        return 2
    if not getattr(args, "domain", None):
        print("pair needs --domain, the company domain, such as example.com.", file=sys.stderr)
        return 2
    old = store.load()
    if old is not None and old.confirmed:
        print(f"This machine is linked to {old.company_name}. Run `unlink` first.", file=sys.stderr)
        return 2
    api = api or LinkApi(settings.link_url)
    if old is not None:
        # §3.8.4 step 11: an unconfirmed pairing that the customer does not
        # take is revoked, so it does not stay live at Metorite.
        try:
            asyncio.run(api.revoke(old.credential))
        except Exception:
            print("The old pairing could not be revoked. An admin can remove it.", file=sys.stderr)
        store.delete()

    def show(code: str) -> None:
        print("Give this code to an admin of your company. They type it in Metorite:")
        print(f"    {code}")
        print("The code ends in 10 minutes. This window waits for the approval.")
        sys.stdout.flush()

    try:
        paired = asyncio.run(pair(api, args.domain, platform.node(), __version__, show, sleep))
    except p.LinkError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    store.save(LinkState(paired.credential, paired.connector_id, paired.company_name))
    print(f"Approved by: {paired.company_name}")
    print("Check that this is your company. To accept it, run:")
    print(f'    confirm-link --company "{paired.company_name}"')
    print("If you do not know this company, run `unlink`. The program serves nothing until then.")
    return 0


def do_confirm(args: argparse.Namespace, store: LinkStore | None) -> int:
    state = store.load() if store else None
    if state is None:
        print("This machine has no pairing. Run `pair` first.", file=sys.stderr)
        return 2
    typed = (getattr(args, "company", None) or "").strip()
    if typed != state.company_name:
        print(
            f"Type the company name that the pairing showed: {state.company_name}", file=sys.stderr
        )
        return 2
    store.save(state.confirm())  # type: ignore[union-attr]
    print(f"Linked to {state.company_name}. Start or restart the bridge to serve it.")
    return 0


def do_unlink(
    settings: Settings,
    store: LinkStore | None,
    api: LinkApi | None = None,
    delete_copy: Any = None,
) -> int:
    """Revoke the credential at Metorite, then delete it here, and the local
    copy of the books with it (TLY-4, §3.8.13 point 6). The local delete
    happens even when Metorite cannot be reached."""
    state = store.load() if store else None
    if state is None:
        print("This machine has no link.")
        return 0
    revoked = True
    if settings.link_url:
        try:
            asyncio.run((api or LinkApi(settings.link_url)).revoke(state.credential))
        except Exception as exc:
            revoked = False
            print(
                f"Metorite could not be reached ({type(exc).__name__}). An admin can "
                "disconnect the connector there.",
                file=sys.stderr,
            )
    store.delete()  # type: ignore[union-attr]
    if delete_copy is not None:
        try:
            delete_copy()
        except Exception as exc:
            print(
                f"The local copy could not be deleted ({type(exc).__name__}). Run delete-copy.",
                file=sys.stderr,
            )
            revoked = False
    print("Unlinked. The credential and the local copy are deleted on this machine.")
    return 0 if revoked else 1


def link_controller(args: argparse.Namespace, live: Any, reader: Any, copy: Any) -> Any:
    """The link controller of ``serve`` (TLY-2b), or None with no link store.
    It reads LINK_URL from the live settings, so the page sees a change."""
    from tally_bridge.transport.link.controller import LinkController

    store = link_store(args)
    if store is None:
        return None

    def copy_at() -> str | None:
        if copy is None:
            return None
        when = copy.oldest_copied_at(live.current.allowed_companies)
        return when.isoformat(timespec="seconds") if when else None

    return LinkController(
        store,
        lambda: live.current.link_url,
        reader.is_up,
        __version__,
        platform.node(),
        copy_at=copy_at,
        on_disconnect=copy.delete_all if copy is not None else None,
    )
