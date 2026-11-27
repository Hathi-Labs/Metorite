# Chat reach: what a member adds, and what the chat can use

**Status: ACTIVE as a map, 2026-11-18. Verified against the code on 2026-11-18
(`origin/main` at `8b8cb2e68`).** Task 3440. It replaces the matrix that task
3440 held on 2026-11-15 (`origin/main` at `320b04375`).

This spec builds nothing. It owns the matrix, the order of the gaps and the
fence. It does not own any fix.

**Owner ask, 2026-11-15.** The words are the owner's, so they sit in a block
that the linter does not read.

```text
"when I add a skill, that skill should also show up in the chat. Any MCP, API, etc. that I add should be accessible via the chat."
```

## 0. The answer in one screen

Most things that a member connects reach the chat today. They reach it only in
the member's own solo chat on the web, and only where the owner turned the
tool packs on. Four kinds of things do not reach the chat at all.

**What reaches the chat:** mail, calendar events, CRM, GitHub and WhatsApp.
Also the Google Drive knowledge source, org MCP servers, Studio apps and
workflows, and published skills. Each has limits, listed below.

**What does not reach the chat:**

1. **API keys and custom APIs** (task 3562). A stored key feeds scripts only.
2. **Plugins** (task 3562). The orchestrator has no reader of a plugin.
3. **Seven key connectors:** Apollo, Google Maps, Instantly, Apify,
   Anymailfinder, Google Sheets and SMTP (task 3562).
4. **A skill that names an MCP tool or an integration action** (TOF-5a, task 3423).
   Such a skill fails to load.

**Three limits cut across the table:**

- **One surface.** Packs, `find_tools` and skills load only in the member's own
  solo chat on the web (task 3494). WhatsApp, a shared room and a delegated run
  load none.
- **One mail rule.** A run that holds a mail pack holds no MCP tool (OA-7b),
  and that rule stays (D-CSY-5). CSY-4 (task 3403) is built dark behind
  `MCP_MAIL_PRECEDENCE_ORGS`. It changes only the precedence of a sticky mail
  pack, when the words of the turn name the server and not mail.
- **One narrow finder.** `find_tools` searches the Studio packs only
  (`PACK_ONLY_OWNERS`). It does not find a CRM, mail or MCP tool (WS-TOF,
  task 3203 for MCP).

The recommended order of work is in §2. The three slices to build first are in
§3. WS-CSY and WS-TOF already own most of the MCP and skill work. This spec
does not plan it again.

## 1. The matrix

**The path** is what puts the thing in a chat turn on `origin/main`.
**Usable** is yes, partial or no. A pack needs `TOOL_PACKS` and
`TOOL_PACKS_ORGS` (empty by default, owner-only to flip). This checkout cannot
read a production flag value, so the table gives the default.

| Kind | Added at | Path to a chat turn today | Flags and defaults | Usable | Gap |
|---|---|---|---|---|---|
| Mailbox (Gmail, Outlook, IMAP) | Email app, `/integrations` Connections | Packs `email` and `email-setup` (`tool_packs.py:1136`). The pack loads by words, by the Email surface or by sticky state | `TOOL_PACKS`, `TOOL_PACKS_ORGS`: empty means off | partial | G1, G2 |
| Google or Outlook calendar | Connections | `my_tasks_list_events` in pack `calendar-day` (CE-15 S1, task 3463) | Same pack flags | partial | G2 |
| Zoho CRM | Connections | Pack `crm-records`. Reads use the `crm` family | Same pack flags | yes | G2 |
| GitHub | Connections | `github_search` and `github_repo_search`, injected at `_tool_injection.py:2417` | None | yes (read only) | none |
| WhatsApp number | Connections | `search_company` source `whatsapp` (`retrieval/sources.py:1202`) and the `whatsapp-assistant` specialist (`routes/agent.py:803`) | None for the source | yes (read, plus the specialist) | none |
| Google Drive knowledge source | Knowledge, Connections | `search_company` source `knowledge` and `read_page` (`sources.py:1203`) | None | yes (read only) | G9 |
| Org MCP server | Connections, MCP | `_inject_mcp_servers` (`_tool_injection.py:4923`) calls `attach_mcp_tools`. No card for a member run (MCP-NC1) | `MAF_MCP_ORGS`: empty by default, `*` in production on 2026-11-15 | partial | G1, G3, G4 |
| Published Studio skill | Studio, Skills | F3: `_turn_with_skills` (`_tool_injection.py:4375`) loads a skill by name (`skill_packs.matches`) or by its description (TOF-1c). TOF-1b brings the packs of its tools | `STUDIO_ORGS`, `STUDIO_CHAT_TOOLS_ORGS` (empty by default), the pack flags | partial | G2, G3, G5 |
| Skill offer ("Save this as a skill?") | Chat | A card after a finished turn (TOF-1d) | `STUDIO_SKILL_OFFER_ORGS`, `STUDIO_ORGS` | yes, dark | none |
| Studio app, its records | Studio | Packs `studio`, `studio-more` are builder tools. They do not run a published app. `search_company` source `studio`. A code-format app with an agent grant gives `app_<slug>_<action>` tools (`skill_families.py:279-291`, `app_tools.py:84-88`). A manifest app gives none | `STUDIO_ORGS`, pack flags, an agent grant | no for running the actions of an app | G2, G7 |
| Studio workflow | Studio, Workflows | Family `workflows`: `list_workflows`, `run_workflow`, `get_workflow_run`. Pack `studio-workflows` publishes and pauses | Pack `studio-workflows` is dark and owner-only | yes (run), partial (publish) | G2 |
| API key, custom API | Integrations, API keys | None. A key feeds scripts only | none | no | G6 |
| Plugin | Integrations, Plugins | None. The orchestrator reads no plugin (`gateway/routes/integrations.py:2395` lists them) | none | no | G6 |
| Key connector: Apollo, Google Maps, Instantly, Apify, Anymailfinder, Google Sheets, SMTP | Connections | None | none | no | G6 |
| Key connector: SerpAPI | Connections | `web_search` may use it | `WEB_RESEARCH_ORGS` and `SERPAPI_API_KEY` | yes | none |
| Outside AI agent | Integrations, AI agents | By design, none. An outside agent calls the platform MCP and acts as the member | `MCP_*` flags of WS-55 | by design | none |
| Built-in skills (families) | Integrations, Built-in skills | The `SKILL_FAMILIES` of `acb_skills` | admin toggles | yes | none |

## 2. The gaps, in the order of member use

The order follows what members most likely add: mail and calendar first, then
Drive, WhatsApp and CRM, then GitHub, MCP servers, published skills and keys.
Size is S (one PR, under a day), M (one slice with a flag) or L (a spec and
several slices). Tier is the review tier of `next-ticket.md`.

| Id | Gap | Evidence | Owner task | Size | Tier | Owner decision | Recommended next slice |
|---|---|---|---|---|---|---|---|
| G1 | A turn that holds a mail pack holds no MCP tool (OA-7b, kept by D-CSY-5). A sticky mail pack stays for 8 turns, so a later turn about the server alone also loses it | `_holds_untrusted_pack` returns early in `_inject_mcp_servers` (`_tool_injection.py:4854`, `:4923`) | CSY-4, task 3403 (built dark, 2026-11-21, owner-only flag). CSY-1 task 3400 gives the brief | M | HIGH | Yes, D-CSY-2 (default yes) | CSY-4, then CSY-1 (§3.1) |
| G2 | Packs, `find_tools` and skills load only in a solo web chat. WhatsApp, a shared room and a delegated run get none | `_start_pack_turn` returns None (`executor.py:2055`) | Task 3494 | M | HIGH | No for the dark build. The flip needs the owner's word | §3.2 |
| G3 | A skill cannot name an MCP tool or an integration action. It fails to load with `action_unserved` | `skill_packs.action_tool` returns None for any other name (`skill_packs.py:223`) | TOF-5a, task 3423 (3493 records it) | M | HIGH | No (D224 rung 1 covers it) | §3.3 |
| G4 | The chat does not know what a server holds, and `find_tools` does not list MCP tools | No `CONNECTED_SYSTEMS_ORGS` setting in `settings.py`. The finder owners are `PACK_ONLY_OWNERS` (`tool_packs.py:2985`) | CSY-1 (3400), CSY-5 (3405), CSY-6 (3406), TS-9 (3203) | M each | STANDARD, CSY-6 HIGH | D-CSY-1 and D-CSY-3 (default yes) | Already planned. Do not re-plan |
| G5 | A skill reaches only the member who owns it, or the org by a visibility that no route sets | `list_loadable` loads an `org` skill, and no route sets `visibility` (`agent_sandbox.md` §0b) | TOF-4a, task 3421 | M | STANDARD | No | Already planned |
| G6 | API keys, custom APIs, plugins and seven key connectors have no chat tool | The fences name them as `gap` | Task 3562 | L | HIGH (egress, secrets) | Yes: which keys, read or write, spend | A spec first (§3.4) |
| G7 | No chat path runs the actions of a published (manifest) app as the member. The loader skips manifest apps, grants name agents, and the tool runs as an agent | `app_tools.py:84-88`, `:87`, `:153`, and `ai_studio.md:790` | F3 and WS-STU. Task 3762 holds the trace | M | HIGH | Yes. Do app actions reach the chat as catalog actions of a skill (F3)? | A design first. A member-identity tool path needs the HIGH review |
| G8 | `find_tools` searches the Studio packs only. A CRM, mail or calendar tool must load with its pack | `_find_candidates` (`_tool_injection.py:4751`) | WS-TOF (a3) | M | HIGH | No | Already planned |
| G9 | Google Drive is read only. The chat cannot create or edit a Drive file | `sources.py:1203` has a read source and no write tool | None filed | M | HIGH | Yes (a write to a third party) | File a task when members ask |
| G10 | Calendar writes. The chat reads synced events and plans the day. It does not create an event on Google or Outlook | CE-15 reads events only | WS-CAL | M | HIGH | Yes (a write to a third party) | WS-CAL owns it |

**Already filed:** TOF-5a (task 3423) builds G3, and task 3493 only records it. Task 3494 is G2. Task 3562 is G6.

Task 3463 is closed by CE-15 S1 and S2. The chat now reads synced events
through `my_tasks_list_events`. Task 3440 holds this matrix.

**Built since 2026-11-15, and now off the gap list:**

- TOF-1b: a skill brings the packs of its tools.
- TOF-1c: a skill loads from the member's words.
- TOF-1d: the skill offer reads its flag for each org.
- CE-15: the chat reads calendar events.
- CSY-7: the admin edits the server description.
- CSY-10: the running step keeps the server label.
- MCP-NC1: a member's MCP call asks no card.
- The CHAT-REACH fences.
- CSY-1, CSY-5 and CSY-6: the brief, the reasons line and the server's own
  instructions, dark behind `CONNECTED_SYSTEMS_ORGS`.
- CSY-4: a turn that names a server evicts a sticky mail pack, dark behind
  `MCP_MAIL_PRECEDENCE_ORGS`. Only the owner flips it, after task 3604.

## 3. The top three slices

**The CSY rule binds each slice.** A member never learns of a server that the
member cannot use. No label, hint or count of a refused or ungranted server may
reach the model or the screen. The test is `is_authorized("mcp-<name>")`.

### 3.1 G1: an MCP server reaches a turn that follows a mail turn

CSY-4 (task 3403) is specified in `chat_connected_systems.md` §3.4, and it is
built dark (2026-11-21). This spec adds only the reach evidence.

- **Change:** `choose` in `acb_skills/tool_packs.py` gets one more reason. A
  sticky email pack leaves when the words name a server. A pack that the words
  name stays.
- **Flag:** `MCP_MAIL_PRECEDENCE_ORGS` (dark, empty by default, owner-only).
- **Fence:** `tests/unit/test_pack_choose_server_words.py`. It proves
  that no run holds a mail pack and an MCP tool together.
- **Keep:** OA-7b, the mark of a chat that read mail, and the refusal for a
  server that the grant refuses.

### 3.2 G2: packs and skills in a WhatsApp turn and a shared room

Task 3494. The WhatsApp path is a batch run (D115). It shows no cards, so it
cannot ask a held write.

- **Change:** `_start_pack_turn` in `orchestrator/executor.py:2055` serves a
  read-only subset of packs when `batch_thread` is true. The subset holds the
  `crm` reads and the `calendar-day` reads. It holds no write tool and no mail
  pack. A shared room stays out until the owner decides.
- **Flag:** `CHAT_PACKS_BATCH_ORGS` (new, dark, empty by default). It acts only
  where `packs_on` allows the pack.
- **Fence:** `tests/unit/test_tool_packs_batch.py` (new). It proves three facts. A batch
  run holds no write tool and no mail pack. The org comes from the run
  binding. The flag off leaves the run byte-identical.
- **Decision for the owner:** may a WhatsApp turn read CRM and calendar data?

### 3.3 G3: a skill that names an MCP tool

TOF-5a, task 3423, builds this slice. This lane does not. The catalog of actions has no row for an MCP tool.

- **Change:** `skill_packs.action_tool` maps `mcp:<server>.<tool>` to the tool
  name, only when the server is granted and its tool is approved (STU-B1b).
  The catalog serves a row only for a server that the member may use.
- **Flag:** it reuses `STUDIO_CHAT_TOOLS_ORGS` and `MAF_MCP_ORGS`. It adds no
  setting.
- **Fence:** `tests/unit/test_skill_packs.py` (extended), and a new case that a
  refused server gives no row and no `action_unserved` hint that names it.
- **Keep:** `_beside_mcp` (`tool_packs.py:3709`). A skill that needs a mail
  pack and an MCP tool still cannot hold both.

### 3.4 G6, after the three: keys, custom APIs and plugins

A spec first, because this gap needs owner decisions: which keys, read or
write, and which spend needs a card. The first slice is the read of one key
connector with a read-only pack, for example Google Sheets. Task 3562 owns it.
`project-docs/specs/api_connections.md` (PROPOSED) holds the design. When its
API-4 slice lands, the gap rows for keys, custom APIs and the key connectors
become a new path, `api`, in both reach fences. SMTP stays a gap, because it
is not an HTTP API.

## 4. The fence

Two tests pin the reach. Both read the real lists, so a new item fails the
test until a decision is written.

| Test | What it pins | What it does not pin |
|---|---|---|
| `tests/unit/test_connector_chat_reach.py` | Each of the 16 rows of `connector_list.json` has a path (`mcp`, `pack`, `retrieval`, `specialist`, `tool` or `gap`). A `gap` names its task. A `pack` names packs that exist | That the tool works. The flag state. That a pack loads in a given turn |
| `tests/unit/test_integration_kinds_chat_reach.py` | Each view of the Integrations screen and each connection kind has a decision. The WhatsApp specialist and the Drive source exist | The Studio kinds (skill, app, workflow). The reach of an outside agent beyond the word `by_design` |

**Two stale lines to repair when the next slice lands.** Both tests name task
3440 for a connector gap, and task 3562 now owns the key connectors. A slice
that closes G6 changes those `gap` entries in the same PR.

## 5. What this spec does not own

| Topic | Owner |
|---|---|
| The MCP brief, the mail precedence and the server instructions | `chat_connected_systems.md` (WS-CSY) |
| Tools on the fly, the finder and the failure store | `agent_sandbox.md` §0b (WS-TOF, D224, D225) and `one_assistant_context.md` §4.2.9 |
| Skill loading, the skill offer and TOF-1a to TOF-1d | `ai_studio.md` section 14 and its rows |
| The tool pack registry and the caps | `one_assistant_context.md` §4.2 |
| The Integrations console and its views | `integrations_console.md` |
| Retrieval sources and the find ladder | `find_engine.md` and `knowledge_base.md` |
| Calendar events and writes | WS-CAL |
