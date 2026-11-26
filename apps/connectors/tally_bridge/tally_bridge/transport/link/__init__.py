"""Link mode (rung 2, TLY-2; ``integrations_console.md`` §3.8).

The program calls out to Metorite over HTTPS, and the office opens no port.

- ``protocol``: the wire format of the five routes, and the errors.
- ``api``: one HTTP client for the five routes.
- ``pairing``: the device grant of §3.8.4 (RFC 8628): a user code for the
  customer's admin, and a poll until the approval.
- ``store``: the link state (the credential, the connector id, the company
  name and the customer's confirmation). Windows keeps it with DPAPI.
- ``dispatch``: runs one request of Metorite against the MCP layer of this
  process.
- ``runner``: the long poll. It serves nothing before the customer confirms
  the company name (rule j).
"""
