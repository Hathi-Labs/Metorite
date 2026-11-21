"""The Tally reader: talks to TallyPrime over HTTP on this machine.

It knows nothing of MCP or of the transport. The tools call it through the
``TallyReader`` protocol, so a test can give the tools a fake reader.
"""

from tally_bridge.reader.client import TallyClient, TallyReader

__all__ = ["TallyClient", "TallyReader"]
