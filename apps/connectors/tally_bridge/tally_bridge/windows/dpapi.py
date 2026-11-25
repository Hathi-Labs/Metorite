"""Windows DPAPI at machine scope, through ``ctypes``.

``Protector`` is the seam. ``DpapiProtector`` calls ``CryptProtectData`` and
``CryptUnprotectData`` of ``crypt32.dll``. Machine scope
(``CRYPTPROTECT_LOCAL_MACHINE``) lets the service account and the person at
the desk read the same secret on this machine, and no other machine can read
it (brief section 6.3). ``CRYPTPROTECT_UI_FORBIDDEN`` keeps the call silent
in a service. A fixed entropy ties each blob to this program, so another
program that also uses machine-scope DPAPI cannot read it by accident.

The calls load ``crypt32`` only when they run, so the module imports on any
system. A test on Linux uses a fake protector.
"""

from __future__ import annotations

import sys
from typing import Protocol

CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4
FLAGS = CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE

#: Ties each blob to this program. It is not a secret.
ENTROPY = b"tally-bridge/dpapi/v1"


class ProtectError(Exception):
    """DPAPI refused to protect or to read a blob."""


class Protector(Protocol):
    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, blob: bytes) -> bytes:
        """The plain bytes, or raise ``ProtectError``."""
        ...


class DpapiProtector:
    """The real protector. Windows only."""

    def __init__(self, entropy: bytes = ENTROPY, flags: int = FLAGS) -> None:
        if sys.platform != "win32":
            raise ProtectError("DPAPI exists on Windows only.")
        self._entropy = entropy
        self._flags = flags

    def protect(self, data: bytes) -> bytes:
        return _call("CryptProtectData", data, self._entropy, self._flags)

    def unprotect(self, blob: bytes) -> bytes:
        return _call("CryptUnprotectData", blob, self._entropy, self._flags)


def _call(
    name: str, data: bytes, entropy: bytes, flags: int
) -> bytes:  # pragma: no cover - Windows
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> tuple[Blob, object]:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    data_in, keep_in = blob(data)
    entropy_in, keep_entropy = blob(entropy)
    out = Blob()
    # The two calls take the same arguments: in, description (None), entropy,
    # reserved, prompt, flags, out.
    fn = getattr(crypt32, name)
    ok = fn(
        ctypes.byref(data_in), None, ctypes.byref(entropy_in), None, None, flags, ctypes.byref(out)
    )
    del keep_in, keep_entropy
    if not ok:
        raise ProtectError(f"{name} failed with error {ctypes.get_last_error()}.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)
