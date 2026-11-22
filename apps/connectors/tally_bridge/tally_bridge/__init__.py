"""The Tally bridge: an MCP server on the machine that runs TallyPrime.

Three modules, kept apart (build brief section 8, docs/tally_bridge_brief.md):

- ``reader``: talks to TallyPrime on 127.0.0.1:9000. It knows nothing of MCP.
- ``tools``: the MCP tools. They call the reader and know nothing of HTTP.
- ``transport``: how Metorite reaches the tools. Address mode (rung 1) serves
  Streamable HTTP at ``/mcp``. Link mode (rung 2) comes later.

The fence is tests/unit/test_tally_bridge_fences.py.
"""

__version__ = "0.1.0"
