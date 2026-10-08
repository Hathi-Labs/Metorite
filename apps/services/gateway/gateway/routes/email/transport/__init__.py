"""transport (subpackage).

Split from a single large layer module into focused submodules; every
submodule name is flattened here so the parent package's public surface is
unchanged.
"""
from __future__ import annotations

from gateway.routes.email.transport import (
    accounts,
    attachments,
    contacts,
    folders,
    messages,
    oauth,
    search,
    send,
    storage,
    sync,
)  # noqa: F401

for _mod in (accounts, attachments, contacts, folders, messages, oauth, search,
             send, storage, sync):
    for _k, _v in vars(_mod).items():
        if not _k.startswith("__"):
            globals()[_k] = _v
del _mod, _k, _v
