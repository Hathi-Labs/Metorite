"""The MCP tools of the bridge. They call the reader, and know nothing of HTTP."""

from tally_bridge.tools.server import READ_ONLY, build_read_server

__all__ = ["READ_ONLY", "build_read_server"]
