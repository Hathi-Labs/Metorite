"""The entry of ``tally_bridge_service.exe``: the Windows service.

Windows starts it with no argument. ``install``, ``remove``, ``start`` and
``stop`` manage the service (pywin32). The service serves until Windows
stops it, with the product stores of ``--windows``.
"""

import sys

from tally_bridge.__main__ import serve_until
from tally_bridge.windows.service import service_main

if __name__ == "__main__":
    raise SystemExit(service_main(sys.argv, serve_until))
