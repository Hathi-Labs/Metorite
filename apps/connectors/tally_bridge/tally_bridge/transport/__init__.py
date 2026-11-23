"""How Metorite reaches the tools.

Address mode (rung 1) serves Streamable HTTP at ``/mcp`` on 127.0.0.1, and a
tunnel publishes it. Link mode (rung 2, ``integrations_console.md`` section
3.8) comes later, beside it, with no change to the tools.
"""

from tally_bridge.transport.address import build_address_app

__all__ = ["build_address_app"]
