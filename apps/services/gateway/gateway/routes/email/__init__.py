"""Email gateway routes (package).

Split out of a single 7k-line module into an acyclic layering:

    core        shared kernel: router, models, DB/provider infra, mappers
    transport   mail sync: accounts, folders, messages, send, oauth, webhook
    automation  inbox-zero layer: rules, drafting, categorization, reply-zero…
    digest      inbox digest (formerly email_digest.py)

The full historical import surface of ``gateway.routes.email`` is preserved:
every top-level name from the submodules is flattened into this package
namespace, so ``from gateway.routes.email import X`` and the scheduler/app/test
imports keep working unchanged. Submodules are imported in dependency order so
their routes register on the shared ``core.router``.
"""
from __future__ import annotations

from gateway.routes.email import automation, core, digest, transport  # noqa: F401

# WS-17 SG-1: imported for its routes only (the upload, the retire and the
# public image). Its names stay in its own module.
from gateway.routes.email import signature_images as _signature_images

_ = _signature_images

# Flatten submodule namespaces into the package so the historical public surface
# (incl. private ``_helpers`` the scheduler/tests import by name) is preserved.
# NOTE: this copies BINDINGS once, at import. A name later reassigned on its
# submodule (or monkeypatched there by a test) diverges from the flattened copy
# — read and patch those via ``email.<submodule>.<name>``, never through the
# package namespace.
for _mod in (core, transport, automation, digest):
    for _k, _v in vars(_mod).items():
        if not _k.startswith("__"):
            globals()[_k] = _v
del _mod, _k, _v
