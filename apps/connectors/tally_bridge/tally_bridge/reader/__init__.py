"""The Tally reader: talks to TallyPrime over HTTP on this machine.

It knows nothing of MCP or of the transport. The tools call it through the
``TallyReader`` protocol, so a test can give the tools a fake reader.

- ``envelope``: the requests, built from typed inputs only. Export only.
- ``client``: the HTTP client, with the queue of one and the timeouts.
- ``parse``: the answer, cleaned and parsed with defusedxml.
- ``values``: the date and sign rules.
"""

from tally_bridge.reader.client import TallyClient, TallyDown, TallyReader, TallyTimeout
from tally_bridge.reader.parse import TallyAnswerError, TallyRefused

__all__ = [
    "TallyAnswerError",
    "TallyClient",
    "TallyDown",
    "TallyReader",
    "TallyRefused",
    "TallyTimeout",
]
