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
| `tally_bridge/transport/` | How Metorite reaches the tools (address mode now, link mode in rung 2) | `tools`, `reader`, `setup`, `settings`, `secret_store` |
| `tally_bridge/setup/` | The setup page on 127.0.0.1 (brief §6.7) | `reader`, `settings`, `secret_store` |

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
`TALLY_TIMEOUT_S`, `CALL_DEADLINE_S`, `MAX_RANGE_DAYS`, `MAX_ROWS`,
`CALLS_PER_MINUTE` and `LOG_DIR`.

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
| §6.4 `find_ledgers`, and `status` with the allowed companies | Built (PR 3). Each tool takes the optional `company` input. The `tools/list` answer is about 1.1 KB for 2 tools (1,127 bytes over HTTP) |
| §6.5 rule 4: the company allow-list | Built (PR 3). `ALLOWED_COMPANIES`, exact names. With no allowed company, no tool reads the books |
| §6.4 read tools 3 to 9 | Built (PR 3, later commits). `tools/reports.py`. All 9 tools fit in 4,758 bytes of `tools/list` (PR 4) |
| §6.5 rule 5: 60 calls a minute for each token | Built (PR 3). `calllog.CallLimiter`, one for each MCP server, so one for each token. `CALLS_PER_MINUTE` changes it. A call past the limit never reaches Tally |
| §6.5 rule 6: one log line for each call, no token | Built (PR 3). `calllog.record`: time, tool, inputs, row count, duration and a result code. With `LOG_DIR` set, `calls.log` rotates at midnight and keeps 90 days |
| §6.7 setup page, address-mode form: find Tally, choose companies, token and address, done | Built (PR 4). `setup/`. The allow-list that it saves holds from the next tool call |
| §6.7 per-install secret and the Origin check (brief test 18) | Built (PR 4). `setup/guard.py` |
| §6.3 settings store that the setup page writes | Built (PR 4). `FileSettingsStore` (JSON, atomic write) |
| §6.3 secret store | Built (PR 4). `DevFileSecretStore` holds hashes only, mode 600, DEV ONLY |
| §6.7 installer, Windows service, DPAPI store, start-at-sign-in, keep awake | Step 4. Needs Windows |

## Brief tests at the unit level (PR 1)

`tests/unit/test_tally_bridge_address.py` covers these at the unit level. A
unit pass is not the brief test. The brief test runs on the public address.

| Brief test | Unit test |
|---|---|
| 2: no token, a wrong token, the read token on `/mcp-write` | `test_no_token_is_401`, `test_wrong_token_is_401`, `test_read_token_on_mcp_write_is_404` |
| 4: `tools/list` with TallyPrime closed, in under 2 seconds | `test_tools_list_with_tally_closed_is_fast` |
| 5: `status` with TallyPrime closed, a plain error in under 25 seconds | `test_status_with_tally_closed_is_a_plain_error`, `test_status_with_tally_hung_stops_at_the_deadline` |
| 15: one log line for each call, and no token in it | `test_brief_test_15_no_token_in_any_log_line`, `test_one_line_for_each_call_with_the_six_facts` |
| 10: `<`, `&` and a quote in an input give no Tally error | `test_every_input_stays_text_and_every_request_is_an_export`, and `test_brief_test_10_special_characters_find_nothing_and_never_reach_tally`: `find_ledgers` filters in the bridge, so the query never reaches Tally |

`tests/unit/test_tally_bridge_reader.py` holds the fence of §6.5 rule 7: no
read path can build an Import request.

## The setup page (dev box)

```bash
cd apps/connectors/tally_bridge
uv run --project . python -m tally_bridge setup-link --settings-file s.json --dev-secret-file k.json
uv run --project . python -m tally_bridge serve --settings-file s.json --dev-secret-file k.json
```

`setup-link` makes a new per-install key, stores only its hash, and prints
the address that opens the page. On Windows the installer does this (step 4).
The bridge serves the page at `/setup` on 127.0.0.1 only. Each request must
pass four checks (`setup/guard.py`):

1. The `Host` header is exactly `127.0.0.1:<port>`. A request through the
   tunnel carries the tunnel host name, so the tunnel cannot reach the page.
2. No forwarding header (`X-Forwarded-For`, `Tailscale-Funnel-Request`,
   `CF-Connecting-IP` and others).
3. The key: in the address for the page, and in `X-Setup-Key` for each API
   call. The bridge compares hashes in constant time.
4. An API call carries `Origin: http://127.0.0.1:<port>`.

The access log of the server is off, so the key in the address reaches no
log. The page writes every value as text, never as HTML.

**`--dev-secret-file` is for a dev box only.** It holds hashes, never a token,
in a file that only its owner reads. A customer's machine uses Windows DPAPI
(build step 4).

## The local copy (TLY-4)

With TallyPrime closed, `find_ledgers`, `list_vouchers` and `get_voucher`
answer from an encrypted local copy, and each answer holds a `source` with the
copy's time and age. `status` shows `copy_age_minutes` for each company. The
other tools still need TallyPrime. A write never uses the copy.

The owner answered on task 3769. The copy stays on this machine (LNK-Q8). It runs every
30 minutes, and its maximum age is 7 days (LNK-Q9).

| Setting | Default | What it does |
|---|---|---|
| `COPY_DIR` | empty | The folder of the copy. Empty turns the copy off |
| `COPY_INTERVAL_MIN` | 30 | Minutes between two copies, 5 to 1440 |
| `COPY_MAX_AGE_DAYS` | 7 | A copy older than this answers nothing, 1 to 90 |

On a dev box, pass `--dev-copy-key-file <path>` to `serve`. It holds the key
in a file with mode 600, and it is DEV ONLY. Build step 4 keeps the key with
DPAPI at machine scope. `python -m tally_bridge delete-copy` deletes every
copy. A company that leaves the allow-list loses its copy when the setup page
saves.

How it copies:

1. The first copy of the day reads the ledgers. Then it reads the vouchers of
   this financial year and the last one, one month in each request.
2. Each later copy reads the ledgers again. Then it reads only the vouchers
   with an `AlterID` above the highest one in the copy (recording R13).
3. A voucher that someone deletes in TallyPrime moves no `AlterID`. The copy
   keeps it until the next first copy of the day. This is a known gap.
4. This slice copies the ledgers and the vouchers, because only those tools
   read the copy. Groups and stock items wait for a tool that needs them.



- The date inputs are `from_date` and `to_date`, because `from` is a reserved
  word in Python.
- `get_voucher` takes a `guid`, or a `voucher_type` and a `number` (PR 4).
  The second form sends a collection with a fixed filter formula. The type
  and the number go in only as the values of two TDL variables, so they never
  become part of the formula. Two vouchers with one number answer a request
  for the GUID. Recording R12 proves the request on a real TallyPrime.
- `ledger_statement` answers `opening` and `closing` as null. Recording R06
  shows where Tally puts them, and then the tool reads them.
- `trial_balance` reads from 1 April of the financial year that holds
  `as_of`.
- `profit_and_loss` takes 366 days or less.

## On Windows (TLY-5)

On a customer's machine, pass `--windows` to each command. The program then
keeps its files under `%ProgramData%\TallyBridge`:

| File | What it holds |
|---|---|
| `settings.json` | The settings. It holds no secret |
| `secrets.bin` | The read token hash and the setup secret hash, protected by DPAPI at machine scope |
| `copy.key` | The key of the local copy, protected by DPAPI at machine scope |

A file that DPAPI cannot read (a new machine, a damaged file) reads as empty.
The bridge then asks for a new setup link, and the local copy starts again.

Two settings keep Tally reachable (`integrations_console.md` §3.8.13). Each
one is off until the customer turns it on:

| Setting | What it does |
|---|---|
| `START_TALLY_AT_SIGNIN` with `TALLY_EXE` | A Task Scheduler task starts TallyPrime when the person signs in. TallyPrime loads the companies of its own start-up setting |
| `KEEP_AWAKE` | While the bridge runs, it asks Windows not to sleep. It changes no power plan |

Two commands change the machine. Run each one as an administrator:

- `python -m tally_bridge apply-windows --windows` makes the sign-in task
  match the setting. It also makes one firewall rule that blocks TCP 9000
  (Tally) and 8765 (the bridge) from the network. Windows does not filter
  loopback, so the bridge still reaches Tally.
- `python -m tally_bridge uninstall-windows --windows` removes the task, the
  firewall rule, the stored hashes, the copy key and the local copy.

The service and the installer follow in TLY-5 PR B.

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
| R02 to R13 | The files that `record-requests` writes (below) | Each tool builds on one of them. `reader/recording.py` says why for each. In R11, put the GUID of one voucher from the R09 answer in place of the zeros. In R12, put the type and the number of that voucher in place of the placeholders. R13 must return the January vouchers with an `ALTERID` in each |

Write the requests with the real names. Then send each one, and keep the
answer and its headers:

```bat
python -m tally_bridge record-requests rec --company "<exact company name>" --ledger "<a busy ledger with a known Dr balance>"
cd rec
for %f in (R*.xml) do curl -s -D %~nf.headers.txt -X POST http://127.0.0.1:9000 --data-binary @%f -o %~nf.answer.xml
```

For each answer, note the encoding in the headers. For R06, note if the
amounts of the Dr ledger are negative. That proves the sign rule of brief §5.5.
