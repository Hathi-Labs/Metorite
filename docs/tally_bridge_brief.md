# Tally bridge for Metorite — build brief

> **In the repo since 2026-11-21, for TLY-1 (task 3656).** Before this date the
> brief lived only on the owner's PC, in a git-ignored folder. The text below is
> version 2, unchanged.

> **The spec wins.** `project-docs/specs/integrations_console.md` §3.8 (PR 2550,
> task 3653) owns the final contract of link mode. §3.8.10 lists where it changes
> the draft contract of this brief, and §3.8.13 adds the owner's rules of
> 2026-11-18. The answers to §10 below are on task 3656.

**Date:** 2026-11-17 (revised the same day, version 2)
**Status:** Ready to build rung 1. Rung 1 is read only.
**For:** the developer who builds the Tally connector.
**Metorite task:** 3653. That task plans the Metorite side of rung 2. Metorite has not built it yet.

---

## 0. What changed in version 2

**The connector is a product, not a service for one office.** The owner decided
this on 2026-11-17. Any Metorite customer downloads the program, installs it on
the machine that runs their TallyPrime, and connects their own Tally to Metorite.

**A customer never does technical setup.** This is a standing owner rule. So a
customer does not run a tunnel, own a DNS zone, open a port or edit a file. The
customer does three things:

1. Installs the program.
2. Types their company domain, for example `acme.com`.
3. Asks their Metorite admin to approve the code that the program shows.

So build ONE program for every customer, from the first day. Do not build a
HathiLabs-only service and rewrite it later. The Tally reader (§5) and the tools
(§6.4) do not change. Version 2 adds the setup screen, the outbound link to
Metorite, and the packaging (§6.7 to §6.9).

---

## 1. What to build

Build one Windows program for the machine that runs TallyPrime. This brief
calls it the **Tally bridge**. The product name can change later.

1. The bridge reads TallyPrime over XML, at `http://127.0.0.1:9000`, on the same machine.
2. The bridge serves the Tally tools as an **MCP server**.
3. The bridge reaches Metorite in one of two modes. One MCP layer serves both.
   - **Link mode, the product default (rung 2).** The bridge opens an
     **outbound** HTTPS connection to Metorite and asks it for work. The
     customer needs no tunnel,
     no public address and no open port. Metorite must build its side first
     (task 3653). §6.8 holds the draft contract.
   - **Address mode, our own test (rung 1).** The bridge serves Streamable HTTP
     at `/mcp` on `127.0.0.1`. A tunnel publishes it at a public HTTPS address.
     A Metorite admin registers that address in **Admin → Integrations → MCP
     servers**. This works with today's Metorite, with no change.
4. A member asks the Metorite chat a question, for example "What does Acme owe us?".
   The chat agent calls the bridge, and the bridge reads Tally.

### 1.1 The ladder

Each rung ends in a demo that the owner uses on production Metorite.

| Rung | The demo | Mode | Needs Metorite work |
|---|---|---|---|
| 1 | Our office: a member asks the chat for a ledger balance, and the answer equals the Tally screen | Address mode, through our tunnel | No |
| 2 | A second company installs the program, types its domain, its admin approves the code, and its chat reads its own Tally | Link mode | Yes, task 3653 |
| 3 | A member asks the chat to make a sales voucher, approves the card, and the voucher shows in Tally | Link mode | Yes |

**Rung 1 is read only.** Writes come in rung 3, with a second endpoint and a
second credential. Do not start rung 3 before rung 2 works on production Metorite.

**Rung 1 already uses the product program.** Build the setup screen, the
installer and the settings store of §6.7 in rung 1. Only the transport differs.

In address mode, Metorite already has the client side. It keeps a registry of
MCP servers for each organization, and its chat agents have an MCP client. So
rung 1 should need no change inside Metorite. Step 1 of §8 proves this before
you build any Tally code.

---

## 2. The picture

```
 OFFICE MACHINE (Windows)                                   METORITE (cloud)
 +-----------------------------------------------+
 | TallyPrime                                    |
 |   HTTP server on :9000 (no password!)         |
 |        ^  XML over HTTP POST                  |
 |        |                                      |
 | Tally bridge  (MCP, Streamable HTTP)          |
 |   listens on 127.0.0.1:8765  /mcp             |
 |        ^                                      |
 |        |                                      |
 | cloudflared  or  tailscaled                   |
 |   opens an OUTBOUND connection  --------------+--> https://tally.<our-domain>/mcp
 +-----------------------------------------------+         ^   port 443, public
                                                           |
                                          Metorite chat agent (MCP client)
                                          registered server name: "tally"
```

The tunnel program opens an outbound connection. So the office router needs no
open port, and a change of the office IP address does not matter.

That picture is **address mode** (rung 1). **Link mode** (rung 2) removes the
tunnel. The bridge itself holds the outbound connection:

```
 CUSTOMER MACHINE (Windows)                                 METORITE (cloud)
 +-----------------------------------------------+
 | TallyPrime  :9000 (locked to 127.0.0.1)       |
 |        ^  XML over HTTP POST                  |
 | Tally bridge (Windows service)                |
 |   no listening port, except the setup page    |
 |   on 127.0.0.1                                |
 |   asks Metorite for work over  ---------------+--> https://<metorite>/connectors/link
 |   OUTBOUND HTTPS (long poll)                  |         |
 +-----------------------------------------------+   Metorite answers a poll with the
                                                     MCP calls that wait, and the chat
                                                     agent sees the org's "tally" server
```

---

## 3. What Metorite expects

These rules come from the Metorite code on 2026-11-17. Each one is a hard limit.
A server that breaks one rule does not connect.

| Rule | Value |
|---|---|
| **Transport** | MCP Streamable HTTP: one endpoint that answers POST. The Metorite form calls it `http-sse`. It means Streamable HTTP. Metorite refuses stdio servers. |
| **Scheme and port** | `https` on port **443** only. |
| **Address** | Public addresses only. Metorite refuses private, loopback, link-local, reserved and CGNAT addresses. A Tailscale `100.x.y.z` address is CGNAT, so Metorite refuses it. |
| **Redirects** | Metorite refuses every 3xx answer. Two traps: a framework that sends `/mcp` to `/mcp/`, and a login redirect from Cloudflare Access. |
| **DNS** | Metorite looks up the host once for each chat run and pins the address. |
| **Auth** | Fixed headers. Metorite stores them encrypted. Use `Authorization: Bearer <token>`. You can add more headers, for example the two Cloudflare Access headers. Metorite also supports OAuth. Do not use OAuth for this server. |
| **Connect time** | 10 seconds for the connect and for `tools/list` together. |
| **Call time** | 30 seconds for one tool call. Then Metorite stops waiting. |
| **Tool count** | 12 tools or fewer, with all tool schemas in 1,500 tokens or fewer. Then the agent sees every tool at once. A larger server goes to on-demand loading, which costs the agent more turns. The hard cap is 128 tools. |
| **Tool names** | Metorite adds the server name as a prefix. So `trial_balance` becomes `tally_trial_balance` for the agent. Use lowercase letters, digits and `_` only. |
| **Tool hints** | Each tool sends the MCP `annotations`. A read tool sends `readOnlyHint: true` and `destructiveHint: false`. |
| **Server name** | 2 to 50 lowercase letters, digits or hyphens. Use `tally`. |
| **A server that is down** | The chat does not fail. The agent runs without the Tally tools. |

### 3.1 The two switches on a registered server

The Metorite admin sets two switches on each MCP server.

- **Always confirm.** It is OFF for a new server (Metorite PR 2530, merged on
  2026-11-17). With it OFF, a tool runs with no card. With it ON, each tool call
  shows the member a confirmation card, whatever the tool hints say.
- **Read-only key.** It is OFF for a new server. With it ON, the admin states
  that the token can only read. Then no tool call needs a card.

Metorite refuses the two switches ON at the same time.

A tool that needs a card works only in the web chat. WhatsApp, the email chat
and runs with no member present refuse it.

**So register rung 1 with Always confirm OFF and Read-only key ON.** That
statement must be true. The bridge makes it true: the read token cannot reach
any write path (§6.2).

### 3.2 Limits to know

- **Main web chat first.** The spec says that a delegated run (a sub-agent of the
  chat) can get no MCP server, and that WhatsApp and the email chat differ.
  Rung 1 targets the main web chat. Step 1 of §8 checks the other places.
- **A chat turn that holds the email tools gets no MCP server** (the Metorite
  code, 2026-11-17). So "read this mail and check the party's balance in Tally"
  in one turn does not reach Tally today. Ask about Tally in its own turn.
- **The card rule changed on 2026-11-17.** Metorite PR 2530 made "Always
  confirm" OFF by default, so a tool runs with no card. Rung 1 is read only, so
  this does not change rung 1. Rung 3 (writes) must turn Always confirm ON.
- **Every member sees the books by default.** The access key is
  `integrations:use:mcp-tally`. The seeded roles hold `integrations:use:*`, so
  each member has it at the start. The owner must **deny**
  `integrations:use:mcp-tally` to each member who must not see the accounts.
  Test 14 of §7 proves it.
- **The Test button proves less than you think.** The admin's Test button sends
  a GET with the stored headers. It passes on any answer below 500, except 401
  and 403. So a 405 passes. The Test proves the address and the token. It does
  not prove MCP. Only a call from the chat proves MCP.

---

## 4. Choose the tunnel (address mode only)

**This section is for rung 1, our own office test.** A customer never sets up a
tunnel (§0). Link mode (§6.8) replaces this section for customers. A large
customer with its own IT team may still choose address mode on its own domain.
That is an option for them, never the default.

First answer one question: **does Cloudflare run the DNS of one of our domains?**

| Option | Works with Metorite? | Needs | Use it when |
|---|---|---|---|
| **A. Cloudflare Tunnel + Cloudflare Access** (recommended) | Yes | A domain whose DNS zone is on Cloudflare. A free Cloudflare account. | We have such a domain. You get two locks: Access at the Cloudflare edge, and the bridge token. |
| **B. Tailscale Funnel** | Yes | Tailscale on the office machine. Funnel turned on in the tailnet policy. HTTPS certificates turned on. No domain. | No domain is on Cloudflare. |
| **C. Plain Tailscale (tailnet only)** | **No** | — | Tests from our own laptops only. Metorite refuses the `100.x` address. |
| **D. An open port on the office router** | **No** | — | Never. It exposes the machine, and the office IP address can change. |

### 4.1 Option A: Cloudflare Tunnel + Access

1. Install `cloudflared` on the office machine as a Windows service.
2. Make a **named** tunnel. Route `tally.<our-domain>` to `http://127.0.0.1:8765`.
3. In Cloudflare Zero Trust, make a self-hosted Access application for that host.
4. Make a **service token**. Add a policy with the action **Service Auth** that
   accepts only that token.
5. Metorite then sends two more headers on each call:
   `CF-Access-Client-Id` and `CF-Access-Client-Secret`.

Do not use a quick tunnel (`*.trycloudflare.com`). Its address changes at each start.

**Prove one thing with curl:** what does Access answer to a wrong service token?
It can answer with a redirect to a login page. Metorite then reports "URL refused"
and not "unauthorized". The owner needs to know which message means a bad token.

### 4.2 Option B: Tailscale Funnel

1. Install Tailscale on the office machine. Join our tailnet.
2. In the tailnet policy, give this machine the `funnel` attribute. Turn on HTTPS certificates.
3. Publish the bridge on port 443 with Funnel. The address is
   `https://<machine-name>.<tailnet-name>.ts.net/mcp`.
4. Funnel has no Access layer. The bridge token is the only lock, so make it long (§6.5).

Check the exact Funnel commands and port rules against the Tailscale docs (§12).
This brief did not test them.

---

## 5. The Tally side

### 5.1 Turn on the Tally HTTP server

1. In TallyPrime, open **F1 (Help) → Settings → Connectivity → Client/Server Configuration**.
2. Set TallyPrime to act as **Server** or **Both**. Set the port to **9000**.
3. One Tally help page puts this setting under **Advanced Configuration**. The
   menu path differs between releases.

Tally answers only while TallyPrime runs and holds the company open. Set
TallyPrime to start at login and to load the company at start. Check how your
release does this.

### 5.2 Lock port 9000

**The Tally HTTP server has no password.** Any machine that reaches port 9000
can read the books, and it can also change them. So:

1. Add a Windows Firewall rule that blocks inbound port 9000.
2. Allow only `127.0.0.1`, and any office machine that uses port 9000 today.
3. The bridge talks to `127.0.0.1:9000` only.

### 5.3 XML or JSON

- **Use XML.** XML over HTTP POST works on all TallyPrime releases.
- Tally help says JSON integration exists from **TallyPrime Release 7.0**. Use it
  only if the office runs 7.0 or later, and you prove each request. A JSON
  Content-Type alone does not turn an XML request into a JSON request.

### 5.4 Request shapes

**Prove each envelope on the office Tally before you build on it.** The Tally help
pages list the tags. They do not give complete samples for these reports.

A report export, for example the trial balance on one date:

```xml
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>Trial Balance</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVCURRENTCOMPANY>Our Company Name</SVCURRENTCOMPANY>
        <SVFROMDATE>20250401</SVFROMDATE>
        <SVTODATE>20260331</SVTODATE>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
    </DESC>
  </BODY>
</ENVELOPE>
```

A collection export with an inline TDL definition, for example all ledgers:

```xml
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>BridgeLedgers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVCURRENTCOMPANY>Our Company Name</SVCURRENTCOMPANY>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="BridgeLedgers" ISMODIFY="No">
            <TYPE>Ledger</TYPE>
            <FETCH>Name, Parent, OpeningBalance, ClosingBalance</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>
```

A Tally response carries a `STATUS` value. `1` means success, and `0` means failure.

### 5.5 Traps to plan for

These come from common field reports. This brief did not test them. Prove each one.

- **One request at a time.** Tally handles one request at a time. A long export
  blocks the next request, and it can freeze the Tally screen of the person at
  the desk. Send requests through a queue of one. Keep date ranges short.
- **Dates** go in and come out as `YYYYMMDD`.
- **Signs.** In Tally XML, a debit amount is usually negative and a credit amount
  positive. Prove it on a known ledger. The bridge returns an amount and a side
  (`Dr` or `Cr`), never a bare signed number.
- **Bad characters.** A response can hold control characters that an XML parser
  refuses. Remove them before you parse.
- **Encoding.** Read the encoding from the response. Do not assume UTF-8.
- **Names.** Company and ledger names must match exactly, with spaces and case.

---

## 6. The bridge

### 6.1 Stack

- **Python 3.12** and the official MCP Python SDK (`mcp`, with `FastMCP`).
  Metorite's backend is Python, so we can read and reuse the code.
  The TypeScript SDK is also acceptable.
- Streamable HTTP with `stateless_http=True` and `json_response=True`. A stateless
  JSON answer has no long SSE stream for a tunnel to buffer.
- `httpx` for the calls to Tally. `defusedxml` for parsing, because it refuses
  XML attacks.
- Run the bridge as a **Windows service** that restarts itself, for example with
  NSSM or WinSW. Write logs to a rotating file.

### 6.2 Endpoints

| Path | Token | Tools | Rung |
|---|---|---|---|
| `/mcp` | read token | read tools only | 1 |
| `/mcp-write` | write token | write tools only | 3 |
| `/health` | none | `{"ok": true, "tally": "up"}` or `"down"`. No book data. | 1 |

These endpoints are for **address mode**. In **link mode** (§6.8), the bridge
listens on no endpoint except the setup page. Each link credential then
carries the same split: a read connector reaches the read tools only, and a
write connector (rung 3) reaches the write tools only.

The read token must never open `/mcp-write`. That rule makes the Read-only key
switch of §3.1 true.

### 6.3 Configuration

**The setup screen (§6.7) writes these settings. A customer never edits a file.**
Keep the secrets (the tokens and the link credential of §6.8) out of this file.
Store them with Windows DPAPI at machine scope, or in the Windows Credential
Manager. The file below shows the settings that exist, not a file to hand-edit.

```ini
TALLY_URL=http://127.0.0.1:9000
BIND_HOST=127.0.0.1
BIND_PORT=8765
READ_TOKEN_SHA256=<hash of the read token>
WRITE_TOKEN_SHA256=            # empty means /mcp-write is off
ALLOWED_COMPANIES=Our Company Name
TALLY_TIMEOUT_S=20
CALL_DEADLINE_S=25
MAX_RANGE_DAYS=92
MAX_ROWS=200
LOG_DIR=C:\ProgramData\TallyBridge\logs
```

### 6.4 Rung 1 tools

Keep **9 tools or fewer**, with short descriptions. The size limit of §3 binds
before the count does (rule 8 below).

Add a `company` input only when the office has more than one company. With one
allowed company, leave the input out, and the bridge uses that company.

| Tool | Inputs | Returns |
|---|---|---|
| `status` | none | Tally up or down, the Tally release, the open companies, the bridge version |
| `find_ledgers` | `query`, `group` (optional), `limit` (50 or fewer) | name, group, closing balance |
| `ledger_statement` | `ledger`, `from`, `to` (92 days or fewer) | opening balance, rows (date, voucher type, number, narration, amount, side), closing balance |
| `trial_balance` | `as_of` | each group with its debit and credit totals |
| `profit_and_loss` | `from`, `to` | income and expense groups, and the net result |
| `outstanding` | `side` (`receivable` or `payable`), `as_of`, `party` (optional) | open bills with due date, age in days and amount |
| `list_vouchers` | `from`, `to` (31 days or fewer), `voucher_type`, `party` (both optional), `cursor` | number, date, type, party, amount, GUID, one page at a time |
| `get_voucher` | `guid`, or `voucher_type` and `number` | the full voucher with its ledger and stock entries |
| `stock_summary` | `as_of`, `item` (optional) | item, quantity, unit, rate, value |

**Rules for each tool:**

1. Send `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true` and `openWorldHint: false`.
2. Answer with compact JSON. Write amounts as strings with two decimals, plus a
   `side` of `Dr` or `Cr`. Write dates as `YYYY-MM-DD`.
3. Put `company`, the date or the range, `row_count` and `truncated` in each answer.
   Add `next_cursor` when the answer is cut.
4. Keep one answer under 20 KB. The answer goes into the model context.
5. Return an error as an MCP tool error (`isError: true`), in one plain sentence
   that a member can act on. For example:
   - "TallyPrime is not running on the office machine."
   - "The company 'X' is not open in TallyPrime."
   - "The range is 120 days. Ask for 92 days or less."
6. `tools/list` never calls Tally. It answers from a fixed list, so it stays
   inside the 10-second connect time even when Tally is down.
7. Each call ends in 25 seconds or returns a timeout error. Metorite stops
   waiting at 30 seconds.
8. **Keep the whole `tools/list` answer small.** Metorite shows the agent all
   tools at once only when their schemas fit in about 1,500 tokens. That is
   about 6 KB of JSON for all tools together. Write each tool description in one
   short sentence. Give each input a description of a few words. Measure the
   `tools/list` answer, and put the size in the handover.

### 6.5 Security rules

1. **Bind to `127.0.0.1`.** Only the tunnel reaches the bridge.
2. **Long tokens.** Make each token from 32 or more random bytes. Store only the
   SHA-256 hash. Compare in constant time.
3. **No raw XML tool, ever.** Each tool builds its own envelope from typed inputs.
   Escape every input before it goes into the XML. The reason: text that an agent
   reads, such as an email, can carry instructions. A raw XML or TDL tool would
   let that text change the books.
4. **Allow-list the companies.** Refuse every other company name.
5. **Rate limit.** Allow 60 calls a minute for each token, and one Tally request
   at a time.
6. **Log each call:** time, tool, inputs, row count, duration and result code.
   Never log a token. Keep the logs for 90 days.
7. **The read path never sends an import.** A test fails when any read tool can
   build a request with `TALLYREQUEST` set to `Import`.

### 6.6 Rung 3: writes (later, after the owner says go)

- Serve the write tools at `/mcp-write`, behind the write token. The owner registers
  it as a second server, `tally-write`, with **Always confirm ON**. Then each write
  shows the member a card in the web chat, and the member approves it.
- Start with `create_sales_voucher` and `create_receipt`.
- Each write takes an `idempotency_key`. The bridge stores the keys and refuses a repeat.
- A write tool sends `readOnlyHint: false` and `destructiveHint: false`.
- After each write, read the voucher back and return its GUID and number.
- **Out of scope:** alter, cancel and delete of a voucher.

### 6.7 The program as a product (rung 1 builds it)

**One installer for every customer.**

1. Ship one Windows installer (MSI, or an EXE that wraps it). It installs the
   bridge as a Windows service that restarts itself, and it opens the setup
   screen when it ends.
2. Sign the installer and the program with a code-signing certificate.
   Windows SmartScreen warns on an unsigned download, and a customer stops
   there. The certificate costs money, so the owner buys it (§10).
3. Bundle Python and every library. A customer installs nothing else.
4. Keep HathiLabs out of the program. No company name, domain, ledger or
   address of ours is in the code or the defaults.

**The setup screen.** Serve it as a local web page at `http://127.0.0.1:8765/setup`.
The installer opens it in the default browser. It has four steps:

| Step | What the customer sees | What the bridge does |
|---|---|---|
| 1. Find Tally | "TallyPrime found: Release 6.1, 2 companies open", or a guide with pictures that turns on the Tally server (§5.1) | Calls Tally on `127.0.0.1:9000`. Reads the release and the open companies |
| 2. Choose companies | One checkbox for each open company. The page ticks none at the start | Writes the allow-list (§6.5 rule 4) |
| 3. Connect to Metorite | A field: "Your company domain", for example `acme.com`. Then a code, such as `KMTR-4821`, and the words "Ask your Metorite admin to enter this code in Admin → Integrations → Connectors". The page shows no company name until the approval | Starts the pairing of §6.8. Waits for the approval. Stores the link credential. Then shows the company name that Metorite returns |
| 4. Done | "Connected to Acme Pvt Ltd on Metorite. Tally is up. Last call: 2 minutes ago." A **Disconnect** button | Holds the link open |

In rung 1 (address mode), step 3 shows the tunnel address and the token
instead. Our own office uses that. A customer never sees it.

**Protect the setup page.** Any web page in the customer's browser can send a
request to `127.0.0.1`. So:

- Bind the page to `127.0.0.1` only.
- The installer makes a random secret and opens the page with it. Each request
  must carry that secret, and must come with the `Origin` of the setup page.
  Refuse every other request.
- The page never shows a stored secret again.

**More than one company or machine.**

- With two or more allowed companies, each tool gets the optional `company`
  input (§6.4).
- A customer can pair two machines, for example two branches. Each paired
  machine is one connector in Metorite. The admin names it, such as `tally` or
  `tally-pune`.

**Health and updates.**

- The bridge reports "Tally up" or "Tally down" over the link each minute. The
  Metorite admin then sees "TallyPrime is closed on OFFICE-PC" without a call.
- The bridge checks for a new version once a day, from an address that
  Metorite serves. It installs a version only when its signature checks. It
  restarts its own service after the update. A customer can turn automatic
  updates off.
- Uninstall deletes the stored credential, and tells Metorite to revoke the
  connector when the link is up.

**Added 2026-11-18 (owner): keep Tally reachable, the tray icon and the local
copy.** `integrations_console.md` §3.8.13 holds the rules. In short:

- The setup page offers two settings: start TallyPrime at sign-in with the
  chosen companies loaded, and keep the machine awake. The customer turns each
  one on.
- TLY-3: a tray icon shows Connected, Tally closed or Not connected. A click
  opens the setup page.
- TLY-4: while TallyPrime is open, the bridge keeps an encrypted copy (DPAPI)
  of the masters and of the vouchers of this financial year and the last one.
  It copies only changes, by `AlterID`, at the admin's interval. With Tally
  closed, a read answers from the copy and names its age. A copy older than
  the maximum age answers nothing. A write never uses the copy. Disconnect and
  uninstall delete it.

**Windows and Tally coverage.** Support Windows 10 and 11, and Windows Server
2016 or later, 64-bit. Support TallyPrime. Test Tally.ERP 9 only if a customer
asks (§10).

### 6.8 Link mode (rung 2, draft contract)

**This contract is a draft.** Task 3653 writes the Metorite spec, and that spec
owns the final contract. Build the transport behind one interface, so that a
change here touches one module. Do not build link mode before the spec merges.

**Pairing.** It follows the OAuth 2.0 Device Authorization Grant (RFC 8628),
because the customer types a code on another screen.

1. The bridge sends the company domain, the machine name, its version and the
   kind (`tally`) to Metorite.
2. Metorite answers with a private device code, a short user code, an
   expiry of 10 minutes, and a poll interval.
3. The bridge shows the user code (step 3 of §6.7). It keeps the device code private.
4. A Metorite admin of that company types the user code in **Admin →
   Integrations → Connectors**. The admin sees the machine name, the Tally
   release and the companies, and approves.
5. The bridge polls with the device code. After the approval, it receives a
   connector id and a long link credential. It stores the credential with DPAPI.

**Why the admin types the code.** The organization comes from the admin who
approves, never from the domain. Metorite does not verify a company's domain,
and two companies can claim the same one. So the domain grants nothing. It
lets Metorite warn the admin when the typed domain differs from their
company's domain. Anybody can type `acme.com` on any machine, so Metorite
shows no list of waiting machines to click. The admin must type the code that
the real machine shows. Before the approval, Metorite tells the bridge no
company name, so the pairing call cannot reveal who uses Metorite.

**The link.** It is plain outbound HTTPS with a long poll, not a WebSocket.
Office proxies often block a WebSocket, and a Metorite deploy cuts any held
stream. The Metorite spec makes the final choice.

1. The bridge sends `POST https://<metorite>/connectors/link/poll` with the
   credential in the `Authorization` header. Metorite holds the request for up
   to 25 seconds, and answers with the MCP requests that wait for this
   connector, or with none.
2. For each request, the bridge calls its own MCP layer in the same process,
   the layer that address mode serves at `/mcp`. It then sends the answer with
   `POST https://<metorite>/connectors/link/reply`. Metorite is the MCP client,
   although the bridge starts every connection.
3. The bridge polls again at once. Each poll also carries "Tally up" or "Tally
   down". When a poll fails, the bridge tries again, with a growing wait
   between tries, up to 5 minutes.
4. The same limits bind as in §3: 10 seconds to list the tools, 30 seconds for
   one call, and the tool size budget.
5. Metorite revokes the credential when an admin presses **Disconnect** or
   removes the connector. The bridge then stops and shows "Disconnected by
   your admin" on the setup page.

**What stays the same.** The tools, their hints, their answers, the read-only
rule, the queue of one, the time limits and the logs. In link mode, the bridge
opens no listening port except the setup page.

### 6.9 What the customer's admin decides in Metorite

The program cannot decide these. Metorite's Connectors page asks the admin:

- **Who may use the books.** Every member has `integrations:use:mcp-tally` by
  default (§3.2). The approval screen offers "Only these people" first.
- **The consent.** "Metorite and its AI model provider read your Tally data when
  a member asks." The admin must accept this to approve.
- **Read only.** Rung 2 connects read only. Writes need rung 3, a second
  connector and the Always confirm switch.

---

## 7. Acceptance tests for rung 1

Each test must pass before the handover.

| # | Test | Pass when |
|---|---|---|
| 1 | `curl -i -X POST https://<host>/mcp` with the headers, `Content-Type: application/json`, `Accept: application/json, text/event-stream` and an `initialize` body. Use `-i`. Do not use `-L`. | 200. No 3xx. |
| 2 | The same POST with the Access headers (if any) and no bearer token. Then with a wrong bearer token. Then the read token on `/mcp-write`. | Never 200. A 401 for a missing or wrong token. A 404 for `/mcp-write` in rung 1. |
| 3 | MCP Inspector (`npx @modelcontextprotocol/inspector`) on the public URL. Save the `tools/list` answer to a file. | 9 tools or fewer. Each tool shows `readOnlyHint: true`. The answer is about 6 KB or less. |
| 4 | `tools/list` with TallyPrime closed. | It answers in under 2 seconds. |
| 5 | `status` with TallyPrime closed. | A plain error in under 25 seconds. |
| 6 | **Trial balance** on 31 March of the last financial year. | It equals the Tally screen, group by group, to the paisa. |
| 7 | `ledger_statement` for one busy ledger over one month. | It equals the Tally ledger screen: opening, each row, closing. |
| 8 | `outstanding` with `side` set to `receivable`, on one date. | The total equals the Tally Bills Receivable total on that date. |
| 9 | `ledger_statement` over 120 days. | A plain refusal that names the 92-day limit. |
| 10 | `find_ledgers` with `<`, `&` and a quote in `query`. | An empty result. No Tally error. |
| 11 | A call during a long export. | It waits its turn. The Tally screen stays usable. |
| 12 | Restart the office machine. | The bridge and the tunnel come back with no manual step. |
| 13 | In the Metorite web chat, a member with access asks for the closing balance of one ledger. | The right number, with no confirmation card. |
| 14 | The owner denies `integrations:use:mcp-tally` to one test member. That member asks the same question. | The agent has no Tally tool. |
| 15 | Read the bridge log. | One line for each call. No token in it. |

**More tests for the product (rung 1 unless marked):**

| # | Test | Pass when |
|---|---|---|
| 16 | Install the signed installer on a clean Windows 11 machine with no Python. | No SmartScreen block. The service runs, and the setup page opens. |
| 17 | `grep` the program and its defaults for our company name, our domain and our ledger names. | No match. |
| 18 | From another browser tab, send a request to the setup page with no secret, and then with a wrong `Origin`. | Both refused. |
| 19 | Find the stored tokens on disk. | No token in plain text. Only DPAPI or Credential Manager holds it. |
| 20 | Rung 2: pair with a domain, and let a person who is not an admin of that org try the code. | Refused. Only an admin of that org approves. |
| 21 | Rung 2: an admin of another org types the code. | The approval page warns that the typed domain differs from the org's domain, and asks the admin to confirm again. The connector then belongs to the org of the admin who approved. |
| 22 | Rung 2: pull the network cable for 2 minutes, then put it back. | The link comes back with no manual step, and the chat reads Tally again. |
| 23 | Rung 2: the admin presses Disconnect. | The next chat turn has no Tally tool. The setup page says "Disconnected by your admin". |

---

## 8. Build order

1. **A hello-world through the tunnel, first.** Build a bridge with one tool,
   `status`, that returns fixed text. Publish it through the tunnel. Ask the owner
   to register it as `tally` in Metorite. Ask the web chat to call it. This proves
   the tunnel, the headers, the redirect rule and the Metorite client before any
   Tally work. **If this step fails, stop and report to the owner.**
2. **The Tally reader.** Build the XML client, the queue of one, the parser, and
   the date and sign rules. Prove each envelope on the office Tally.
3. **The read tools,** one at a time. Each tool gets its match test from §7.
4. **The product shell (§6.7).** The installer, the signed build, the setup
   screen in its address-mode form, the settings store, the secret store, the
   Windows service, the restart, the logs and the firewall rule. Run tests 16 to 19.
5. **Handover of rung 1** to the owner (§9). Run tests 13 and 14 together.
6. **Rung 2, link mode (§6.8),** after the Metorite spec of task 3653 merges and
   Metorite builds its side. Run tests 20 to 23 with a second test organization.
7. **Rung 3, writes,** only after the owner says go.

**Keep three modules apart:** the Tally reader, the MCP tools, and the
transport (address mode or link mode). A later connector for another local
system, such as Busy or a local SQL database, then reuses the transport and the
setup screen. Only its reader and its tools are new.

---

## 9. Handover to the Metorite owner

**Send the owner:**

- the URL, for example `https://tally.<our-domain>/mcp`
- the header names
- the token values, through a password manager share. **Never send a token in
  chat, in email or on a task.**
- the tool list, with what each tool returns
- the results of the §7 tests

**The owner then:**

1. Opens **Admin → Integrations → MCP servers → Add**.
2. Sets the name `tally`, the URL and the headers.
3. Sets **Always confirm OFF** and **Read-only key ON**.
4. Presses **Test**.
5. Denies `integrations:use:mcp-tally` to each member who must not see the
   books. Every member has it by default (§3.2).
6. Runs tests 13 and 14 with you.

**For rung 2, a customer's flow replaces this section.** The customer installs
the program and types the domain. Their admin types the code in **Admin →
Integrations → Connectors**, chooses who may use the books, accepts the data
consent and approves. Nobody sends a URL or a token by hand.

---

## 10. Open questions

Answer questions 1 to 6 before step 2 of §8.

1. Which TallyPrime release runs in the office? Release 7.0 or later opens JSON.
2. One company or many? Which companies may Metorite see?
3. Does Cloudflare run the DNS of one of our domains? If no, use Tailscale Funnel.
4. Does the office machine stay on at night and on holidays, with TallyPrime open?
   If no, the chat says "TallyPrime is not running" at those times.
5. Does any other office machine use port 9000 today? The firewall rule needs to know.
6. Does the owner accept that book data goes to Metorite, and to its model
   provider, when a member asks? Rung 1 needs a yes.
7. For rung 3: which voucher types, and who approves a write?

**Questions for the product (answer before step 4 of §8):**

8. **The code-signing certificate.** It costs money each year, so the owner
   buys it. An EV certificate stops the SmartScreen warning at once. A plain
   (OV) certificate builds trust over weeks of downloads.
9. **The download and update address.** Metorite serves the installer and the
   update file. Task 3653 decides where.
10. **The product name** that a customer sees in the installer and in Metorite.
11. **Tally.ERP 9.** Do our first customers still run it? The brief targets
    TallyPrime only.

---

## 11. Later, not now

- **A sync into Metorite.** A copy of masters and vouchers inside Metorite, for
  dashboards and AI Studio apps. That needs a Metorite ingest API and a spec.

---

## 12. References

**Tally**

- XML integration: <https://help.tallysolutions.com/xml-integration/>
- XML interface: <https://help.tallysolutions.com/xml-interface/>
- Integration prerequisites: <https://help.tallysolutions.com/pre-requisites-for-integrations/>

**MCP**

- Streamable HTTP transport: <https://modelcontextprotocol.io/specification/2025-06-18/basic/transports>
- Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- MCP Inspector: <https://github.com/modelcontextprotocol/inspector>

**Tunnels**

- Cloudflare Tunnel: <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/>
- Cloudflare service tokens: <https://developers.cloudflare.com/cloudflare-one/identity/service-tokens/>
- Tailscale Funnel: <https://tailscale.com/kb/1223/funnel>

**Metorite code behind §3** (for the owner, or for you with repo access)

- `apps/services/gateway/gateway/outbound_guard.py`: the address, port and redirect rules
- `apps/services/orchestrator/orchestrator/_maf_mcp.py`: the connect and call times, the tool cap
- `apps/services/gateway/gateway/connections/mcp.py`: the two switches
- `apps/services/gateway/gateway/routes/integrations.py`: the register form
- `project-docs/specs/agent_architecture.md` (WS-8c) and `project-docs/specs/integrations_console.md` (IN-4)
