# Tally bridge

<!-- ste-tier: strict -->

An MCP server that runs on the machine with TallyPrime. It reads Tally on
`127.0.0.1:9000`, and it serves read tools to the Metorite chat.

- Build brief: `docs/tally_bridge_brief.md` (version 2).
- Spec: `project-docs/specs/integrations_console.md` §3.8. Where the two
  differ, the spec wins.
- Task: TLY-1, Metorite task 3656.

## The three modules

| Module | Job | It may import |
|---|---|---|
| `tally_bridge/reader/` | Talks to TallyPrime over HTTP | `settings` only |
| `tally_bridge/tools/` | The MCP tools | `reader`, `settings` |
| `tally_bridge/transport/` | How Metorite reaches the tools (address mode now, link mode in rung 2) | `tools`, `reader`, `settings`, `secret_store` |

`settings.py` and `secret_store.py` each hold a store interface. A dev box
reads the environment. Step 4 of the build order adds the Windows stores
(DPAPI and the setup screen) behind the same interfaces.

## Run it on a dev box

```bash
cd apps/connectors/tally_bridge
uv run --project . python -m tally_bridge new-token   # shows the token once
export TALLY_BRIDGE_READ_TOKEN_SHA256=<the hash it printed>
uv run --project . python -m tally_bridge serve       # 127.0.0.1:8765
curl -s http://127.0.0.1:8765/health
```

Settings (environment, with the prefix `TALLY_BRIDGE_`): `TALLY_URL`,
`BIND_HOST`, `BIND_PORT`, `PUBLIC_HOST`, `ALLOWED_COMPANIES` (split by `|`),
`TALLY_TIMEOUT_S`, `CALL_DEADLINE_S`, `MAX_RANGE_DAYS`, `MAX_ROWS` and
`LOG_DIR`.

**Behind a tunnel, set `PUBLIC_HOST`.** Set it to the tunnel host name,
such as `office-pc.<tailnet>.ts.net`. The MCP layer refuses a request whose
`Host` header is not a loopback name or this name.

## What is built

| Brief item | State |
|---|---|
| §6.1 stack: FastMCP, stateless, JSON answers | Built (PR 1) |
| §6.2 `/mcp`, `/health`, and `/mcp-write` answers 404 | Built (PR 1) |
| §6.4 tool `status` | Built (PR 1). Tally up or down and the bridge version. The release and the open companies come with the envelopes (PR 2) |
| §6.5 rule 1: bind to a loopback address | Built (PR 1). The settings refuse another address |
| §6.5 rule 2: hashed token, constant-time compare | Built (PR 1) |
| §5.4 envelopes, §5.5 traps, §6.5 rules 3 and 7 | Built (PR 2). `reader/envelope.py`, `reader/parse.py`, `reader/values.py`, the queue of one in `reader/client.py` |
| §6.4 read tools 2 to 9 | PR 3 and later |
| §6.5 rule 4 (allow-list), rule 5 (rate limit), rule 6 (call log) | Later PRs |
| §6.7 installer, Windows service, DPAPI, setup screen | Step 4. Needs Windows |

## Brief tests at the unit level (PR 1)

`tests/unit/test_tally_bridge_address.py` covers these at the unit level. A
unit pass is not the brief test. The brief test runs on the public address.

| Brief test | Unit test |
|---|---|
| 2: no token, a wrong token, the read token on `/mcp-write` | `test_no_token_is_401`, `test_wrong_token_is_401`, `test_read_token_on_mcp_write_is_404` |
| 4: `tools/list` with TallyPrime closed, in under 2 seconds | `test_tools_list_with_tally_closed_is_fast` |
| 5: `status` with TallyPrime closed, a plain error in under 25 seconds | `test_status_with_tally_closed_is_a_plain_error`, `test_status_with_tally_hung_stops_at_the_deadline` |
| 10: `<`, `&` and a quote in an input give no Tally error | `test_every_input_stays_text_and_every_request_is_an_export` (the envelope half only) |

`tests/unit/test_tally_bridge_reader.py` holds the fence of §6.5 rule 7: no
read path can build an Import request.

## Fixtures

- `fixtures/synthetic/`: answers that we wrote by hand. Each file name starts
  with `SYNTHETIC_`, and each first line says SYNTHETIC. A match against one is
  never a pass of brief tests 6, 7 or 8.
- `fixtures/requests/`: the requests to record, as the builders make them,
  with the placeholders `COMPANY NAME` and `LEDGER NAME`. A test keeps them
  equal to the builders.
- `fixtures/recorded/`: the real answers, when they exist.

## Answers to record on the office PC

A synthetic fixture is not proof. Record each answer below on the office PC,
and save it under `fixtures/recorded/`. Remove the matching SYNTHETIC file
when the recorded one lands.

| # | Request | Why |
|---|---|---|
| R01 | `curl -s -i http://127.0.0.1:9000/` (a GET with no body) | `is_up` reads "up" from any 200 answer. Record the exact body, so that the bridge can tell TallyPrime from another program on port 9000 |
| R02 to R10 | The files that `record-requests` writes (below) | Each tool builds on one of them. `reader/recording.py` says why for each |

Write the requests with the real names. Then send each one, and keep the
answer and its headers:

```bat
python -m tally_bridge record-requests rec --company "<exact company name>" --ledger "<a busy ledger with a known Dr balance>"
cd rec
for %f in (R*.xml) do curl -s -D %~nf.headers.txt -X POST http://127.0.0.1:9000 --data-binary @%f -o %~nf.answer.xml
```

For each answer, note the encoding in the headers. For R06, note if the
amounts of the Dr ledger are negative. That proves the sign rule of brief §5.5.
