# Personalization — the assistant knows who it talks to

**Status: PROPOSED, 2026-10-23. PZ-1 BUILT 2026-10-23** (migration 294,
the routes of §4.3 in `routes/admin/assistant_profile.py`, and the fix of
§2.1). No flag yet, and nothing reads the profile until PZ-2.

Owner directive of 2026-10-23, recorded in §1. Verified against code on 2026-10-23, at
`origin/main` `e3336f46d`. Board row **WS-63**. The Metorite project
"Personalization & Memory System" holds one task for each slice (§9).

§8 proposes **D-PZ-1** to **D-PZ-9**, each with a default. Every slice in §9
is AGENT-SAFE to build dark, except where §9 says OWNER-GATE.

**What this spec owns.** What the one assistant knows about the member it
talks to, and how it uses that. That is five things:

1. **The profile.** A small, bounded set of facts and preferences of one
   member in one organization. The member can read and change it.
2. **The form of address.** The name that the assistant uses for the member,
   and when and how it greets the member.
3. **The interaction style.** How long, how formal and in which language the
   assistant answers this member.
4. **Names and aliases.** How the assistant resolves "Raj" or "my manager" to
   one person.
5. **Consent and control.** What the assistant may learn, how the member sees
   it, and how the member makes it forget.

**What this spec does not own.**

- **The long tail of recalled memory.** Mem0, Graphiti, the compartments and
  the memory API stay with `memory_architecture.md` (WS-9),
  `docs/multiplayer/memory-clearance.md` (WS-10) and `llm_caching_memory.md`
  (WS-7). This spec adds no vector store and no second recall path.
- **How Metorite writes.** `agent_writing_voice.md` (WS-52) owns the house
  voice and its overlays. The profile's style is a member overlay on top of
  that contract (§5).
- **The member's own voice in a draft.** `email_voice_profiles` and WS-52 S3
  own how a draft sounds when the member signs it. The profile is how the
  assistant talks **to** the member, not **as** the member.
- **The one assistant.** `navigation_shell.md` §7.1 (D99) owns the agent and
  its field. `chat_first_surface.md` (WS-62) owns how a member reaches it.
- **The channels.** `whatsapp_assistant_channel.md` (WS-47) owns the WhatsApp
  link and its run. This spec adds no channel.
- **Custom tools.** The project's tasks #254 and #255 (a custom tool
  registry and a creation flow) are a different feature. They need their own
  spec (D-PZ-9).

---

## 0. The answer in one screen

1. **One profile per member per organization**, in the existing per-member
   row: a new JSON column, `user_settings.assistant_profile`. No new table,
   and no new tenancy shape. It has a hard size cap.
2. **The profile is always present.** The executor adds one bounded block to
   every turn of the member's own runs, on the web and on WhatsApp. The one
   preamble builder does it. It never goes through Mem0, so it works while
   Mem0 is off.
3. **The member teaches it in plain words.** For example: "Call me VJ" or
   "keep it short". The assistant saves at once and offers Undo (D109.5).
   It never learns in silence.
4. **The member sees all of it, and can delete any of it.** Settings has an
   Assistant pane. The chat answers "what do you know about me" from the
   block, with no guess.
5. **It is private.** No admin and no other member reads it. A shared room
   gets no profile block.
6. **It ships dark,** behind `ASSISTANT_PROFILE_ENABLED` on the server and
   `NEXT_PUBLIC_ASSISTANT_PROFILE` on the client.

---

## 1. The owner's ask

Owner, 2026-10-23, in chat. The owner asked for a "personality engine". It
gives the member personal salutations. It understands what the member wants.
It remembers facts about the member.

The owner made the Metorite project "Personalization & Memory System" on
2026-10-18. It has 19 tasks and no descriptions. Its description says: "learn each
user's personality, greetings, name references, short- and long-term memory,
interaction style and reusable custom tools, working across Metorite and
WhatsApp."

---

## 2. What exists today

Measured on 2026-10-23. Paths are from the repository root.

| Piece | Where | State |
|---|---|---|
| Mem0 per-member recall | `packages/acb_memory/acb_memory/mem0_client.py` | Built, **dark**. `mem0_enabled` defaults to False (`acb_common/settings.py:1340`). Mem0 rows carry no `organization_id` (H-307). H-336 and H-337 hold it off |
| Memory compartments | `acb_memory/compartments.py:72` `scope_key` | Built. `prefs:<email>` is the only store of preferences, and it lives in Mem0, so it is dark too |
| Memory pre-read for each turn | `apps/services/gateway/gateway/memory_preread.py:95` | Built. Reads nothing while Mem0 is off. A shared room reads no private memory |
| Memory tools | `packages/acb_skills/acb_skills/memory_tools.py` | Built. **Defect, §2.1** |
| Memory API | `apps/services/gateway/gateway/routes/memory.py` | Built: list, search, add, delete one fact |
| The preamble of each turn | `apps/services/orchestrator/orchestrator/executor.py:8449` `_context_preamble_parts` | Live. One builder for both the string and the structured path |
| The member's name | `executor.py:628-714` `_member_name_line` | Live. Reads `app_user.display_name`. It is a line for signing mail, not a form of address |
| The member's number system | `executor.py` `run_number_system`, migration 284 | Live. **The precedent:** one per-member value, one line in every turn |
| My Day greeting | `workbench/control_plane/src/lib/shell/myDay.ts:22` `greetingLine` | Live. The first word of the session name |
| The assistant's persona | `src/lib/shell/assistant.ts:51-58` | Live. A constant in the client: the name "Metorite" and a persona line |
| People directory | `people.preferred_name` (migration 172), `organization_id` (migration 209) | Live. The executor never reads `preferred_name` |
| Per-member settings row | `user_settings`, keyed `(organization_id, user_id)` under FORCE RLS (migration 239) | Live. JSON columns `shell_prefs` (240) and `my_day_prefs` (259) |
| One member on two channels | `whatsapp_member_links` (migration 235) | Live. It maps a WhatsApp number to `(organization_id, member_email)`. The WhatsApp run uses the agent `orchestrator` (`whatsapp_channel/bot_run.py:125`) |

**Not found:** a form-of-address preference, a nickname or alias store, a
consent switch for memory, a setting for the assistant's name.

### 2.1 The defect: "remember this" keeps nothing

`save_memory` (`memory_tools.py:261`) calls `add_memories_background`.
`MemoryClient.add` (`mem0_client.py:246`) returns in silence when Mem0 is
off. The tool then answers "Memory saved." So the assistant tells the member
that it will remember, and it keeps nothing. Mem0 is off in production. PZ-1
fixes this (§9).

---

## 3. The rule that places each fact

`memory_architecture.md` §2 gives the rule. **If the agent did not have the
fact, would it do the wrong thing?** Then the fact goes in a bounded block
that is always present. **Would it only miss a useful detail?** Then the fact
goes in recall (Mem0).

The member's name, form of address, answer style and named aliases are the
first kind. A wrong name in every reply is a wrong act, not a missed detail.
So they go in the profile. Everything else the member says stays in the
conversation, and goes to Mem0 when Mem0 is on.

| The member says | Where it goes |
|---|---|
| "Call me VJ" | Profile, `address.name` |
| "Keep your answers short" | Profile, `style.length` |
| "Reply in Hinglish on WhatsApp" | Profile, `style.notes` |
| "Raj is raj@fracktal.in" | Profile, `aliases` |
| "Remember that I travel every Friday" | Profile, `facts` |
| "The Acme deal closed at 42 lakh" | Not the profile. A company fact goes to the owning app or to Knowledge |
| A detail that the member did not ask to keep | Not the profile. Mem0 when it is on (WS-7) |

The profile is the member compartment of the "durable-explicit" tier of
`memory_architecture.md` §3. That tier has a hard budget and a deterministic
injection (§6.2 there). This spec builds that tier for the member, and only
for the member.

---

## 4. The profile

### 4.1 Where it lives

One nullable JSONB column, `user_settings.assistant_profile`, added by the
next free migration number at build time (R1). 293 is the newest on
2026-10-23. Expand only, nullable, no default (R6).

The row is already keyed `(organization_id, user_id)` under FORCE ROW LEVEL
SECURITY. So the profile is tenant-scoped by construction (R5), and a member
in two organizations has two profiles. This is D15: tenancy is a row.

**Why not a new table.** The owner named `user_settings` "one row of
per-member preference" (2026-09-22). Two JSON columns already follow this
shape. A third JSON column adds no seam. A new table would be a second home
for per-member preference.

### 4.2 The shape

```json
{
  "v": 1,
  "learning": true,
  "address": { "name": "VJ", "formality": "casual", "greeting": "brief" },
  "style":   { "length": "short", "language": "en-IN", "notes": ["Hinglish is fine on WhatsApp"] },
  "aliases": [ { "id": "a1", "alias": "Raj", "email": "raj@fracktal.in" } ],
  "facts":   [ { "id": "f1", "text": "Travels every Friday", "kind": "routine",
                 "source": "chat", "at": "2026-10-23T11:00:00Z" } ]
}
```

| Key | Values | Cap |
|---|---|---|
| `learning` | `true` or `false`. Default `true` | — |
| `address.name` | Free text | 40 characters |
| `address.formality` | `casual`, `neutral`, `formal` | — |
| `address.greeting` | `none`, `brief`, `warm` | — |
| `style.length` | `short`, `default`, `detailed` | — |
| `style.language` | A BCP 47 tag | 16 characters |
| `style.notes` | A list of free text | 5 items, 200 characters each |
| `aliases` | `{id, alias, email}`. `email` must be a member or a person of the org | 50 items, alias 60 characters |
| `facts` | `{id, text, kind, source, at}`. `kind` is `about`, `routine`, `preference`, `work` or `relationship`. `source` is `chat`, `whatsapp` or `settings` | 40 items, 300 characters each |

**The budget.** The block that the executor renders is at most 6,000
characters, about 1,500 tokens. The caps above keep it under that. A write
that would break a cap is a 422 with the cap in the message.

**No sample text.** A fact holds what the member said to keep, in at most 300
characters. It never holds a mail body, a message body or a file. This is the
same rule as WS-52 S3 for the voice profile.

### 4.3 The API

The member comes from the session, and the tenant comes from the bound
session. Neither comes from the body (R5, R11). The shape follows
`routes/shell/prefs.py`: a strict write, a lenient read, and one key per
write merged with `jsonb_set`.

| Route | Does |
|---|---|
| `GET /auth/me/assistant-profile` | The profile, or the empty profile |
| `PUT /auth/me/assistant-profile` | Body `{key, value}`. `key` is `learning`, `address` or `style` |
| `POST /auth/me/assistant-profile/facts` | Add one fact. Returns its `id` |
| `PATCH /auth/me/assistant-profile/facts/{id}` | Change the text or the kind |
| `DELETE /auth/me/assistant-profile/facts/{id}` | Forget one fact |
| `POST`, `PATCH`, `DELETE` `.../aliases[/{id}]` | The same for aliases |
| `DELETE /auth/me/assistant-profile` | Forget everything. The column becomes NULL |

A member with no `user_settings` row gets one on the first write, with the
calendar seed, as `prefs.py` does.

---

## 5. How the assistant uses it

### 5.1 One block, on every channel

`_context_preamble_parts` gains one block, `_member_profile_block`, placed
after the member name line. Nothing above it moves. One builder serves both
the string path and the structured path, so they cannot drift.

The block reaches a run only when all four hold:

1. `ASSISTANT_PROFILE_ENABLED` is on.
2. The agent is `orchestrator`. The WhatsApp run uses that agent too.
3. The run has a verified member (`_verified_run_member`), from the run
   boundary and never from the payload.
4. The run is not in a shared room. The run boundary carries the same
   `shared` bit that `memory_preread` reads.

The read follows `_run_member_display_name`: one read, a cache per
`(org, member)` with a 60-second life, and no exception escapes. The block is
byte-stable between writes, so it sits well in the cached prefix
(`llm_caching_memory.md`).

The block reads like this:

```text
## About the member (their own profile — they can see and change it)
Address them as "VJ". Tone: casual. Greeting: brief, only in the first reply of a chat.
Answer length: short. Language: en-IN. Notes: Hinglish is fine on WhatsApp.
People they name: "Raj" = raj@fracktal.in.
What they asked you to remember:
- [f1] Travels every Friday (routine, 2026-10-23)
If they ask what you know about them, answer from this block only.
```

The id in brackets lets the assistant forget or change one fact by id.

### 5.2 The form of address

The name comes from the first of these that has a value:

1. `assistant_profile.address.name`
2. `people.preferred_name` for the member's email in the org
3. The first word of `app_user.display_name`
4. No name. The assistant uses no name, and never a placeholder

`_member_name_line` keeps the full display name for signing mail. The form
of address is a different thing, so the two lines stay separate. My Day's
`greetingLine` reads the same order through `GET /auth/me/assistant-profile`.

**The greeting.** `none` means the assistant opens with the answer. `brief`
means one short greeting in the first reply of a chat, in the member's time
of day. `warm` adds one personal line, from a fact, at most once a day. The
assistant never greets in the middle of a chat. The default is `brief`.

### 5.3 The interaction style

The style is a member overlay of the WS-52 contract. The rules of
`acb_llm.voice` stay in force. Where the member's style differs, the member
wins, as the WS-52 email overlay already says for the member's own voice.

`length` maps to the WS-52 brevity rule (§11.8 there): `short` halves the
target, `detailed` lifts it. `formality` sets the register. `language` sets
the language of the answer, and the chat still follows a member who writes
in another language. On WhatsApp, the channel limits of WS-47 win over
`detailed`.

The persona of the assistant does not change. It is still "Metorite". This
spec personalizes what the assistant knows and how it talks, not who it is.

### 5.4 Names and aliases

A tool `resolve_person(name)` returns one person, or a short list to ask
about. It looks in this order:

1. The member's own `aliases`, exact and case-blind.
2. `people` in the org: `preferred_name`, then `display_name`, then the local
   part of the email.
3. `app_user.display_name` in the org.

**One match acts. Two or more ask.** The assistant never picks between two
people by guess. After the member picks, the assistant offers to save the
alias. The tool reads through the tenant session, so it sees one org only.

### 5.5 Learning from the chat

Four tools on the orchestrator, behind the flag:

| Tool | Does |
|---|---|
| `profile_remember(text, kind)` | Add a fact |
| `profile_forget(id)` | Forget a fact |
| `profile_set(key, value)` | Set `address` or `style` |
| `profile_alias(alias, email)` | Add or change an alias |

Each is a reversible write on the member's own data. So it acts at once and
shows an Undo, by D109.5. With `learning` off, each tool refuses and says so.

**When the assistant writes.** Only when the member states a fact or a
preference about themselves, or says yes to a proposal from the assistant.
It never saves what the member says about another person as a fact about
that person. It never saves from a forwarded mail, a file or a tool result.

`save_memory` stays for Mem0. The orchestrator prompt says which tool to use:
a fact the member asks to keep goes to the profile, and nothing else goes
there.

---

## 6. Identity across channels

**There is no new identity work.** Each channel already resolves the member to
one pair, `(organization_id, member_email)`, at its boundary. The profile is
keyed by that pair, so every channel reads the same profile.

| Channel | How the pair is known |
|---|---|
| Web chat | The session. The tenant is bound at `get_db()` |
| WhatsApp | `whatsapp_member_links`, after the member links the number (WS-47) |
| MCP for outside agents | The OAuth grant (WS-55). It gets no profile block, because it runs no orchestrator turn |
| Email as a chat channel | Not a channel. Not in scope |

A number that links to two organizations already picks one org for each
conversation (WS-47). That org's profile applies.

---

## 7. Consent and control

1. **Explicit by default.** The assistant writes the profile only when the
   member asks, or says yes. It never extracts a fact from a chat in silence.
2. **The member sees everything.** Settings → Assistant shows every key and
   every fact, with its source and date. Each fact has Edit and Delete.
3. **One switch.** "Let the assistant remember what I tell it." Off stops
   every write. The assistant still reads the profile. The member can delete
   it.
4. **Forget everything** clears the column, at once. No soft delete. The
   profile is the member's own words, and the member asked to remove them.
5. **Private.** No admin route reads another member's profile. No export
   includes it. A shared room gets no block.
6. **Leaving the org.** When a member's access ends, their `user_settings`
   row and so their profile goes with it. PZ-9 verifies this and adds the
   delete if it is missing.
7. **The profile is never sent outside.** It goes to the model through the
   Router, as every turn does. No tool sends it to a third party.

---

## 8. Decisions proposed

Each has a default. The owner accepts or changes them. Until then, the
slices build the default, dark.

| # | Question | Default |
|---|---|---|
| **D-PZ-1** | Where does the profile live? | A JSONB column on `user_settings`, not Mem0 and not a new table (§4.1) |
| **D-PZ-2** | May the assistant learn in silence? | No. It writes only on the member's words or yes, then offers Undo (§5.5) |
| **D-PZ-3** | Who may read a profile? | The member only. No admin read, no shared room (§7) |
| **D-PZ-4** | One profile across orgs? | No. One per org, because tenancy is a row (D15) |
| **D-PZ-5** | The order of the form of address | Profile, then People's preferred name, then the first word of the display name (§5.2) |
| **D-PZ-6** | The default greeting | `brief`, in the first reply of a chat only |
| **D-PZ-7** | The default of the learning switch | On, because nothing is learned without the member's words |
| **D-PZ-8** | Does the member rename the assistant? | No. It stays "Metorite" (§5.3) |
| **D-PZ-9** | Custom tools (#254, #255) | Out of this spec. They need their own spec, and they wait |

---

## 9. The slices

Each slice is one PR. Each lands dark. The risk tier follows
`next-ticket.md` step 1b.

| Slice | Task | What | Done when | Gate · tier |
|---|---|---|---|---|
| **PZ-0** | #243, #244, #245, #246 | This spec, the INDEX row and the board row | The spec merges | AGENT-SAFE · LOW |
| **PZ-1** | #247 | The migration, the API of §4.3, and the fix of §2.1 | The tests of §10 pass against a real database. `save_memory` with Mem0 off answers that memory is off | AGENT-SAFE · **HIGH** (migration, tenancy) |
| **PZ-2** | #256 | `_member_profile_block` in the preamble, behind the flag, with the four conditions of §5.1 | A unit test shows the block for a solo run, and no block for a shared room, for a run with no member, and with the flag off. A test shows that no block above it moves | AGENT-SAFE · MEDIUM |
| **PZ-3** | #251 | The style keys in the block, and the WS-52 overlay rule | The eval of PZ-10 shows `short` answers shorter than `default` on the same prompts | AGENT-SAFE · MEDIUM |
| **PZ-4** | #252 | The form of address of §5.2, the greeting rule, and My Day | A unit test covers each step of the order. My Day shows the profile name | AGENT-SAFE · MEDIUM |
| **PZ-5** | #253 | `resolve_person` and the alias write | A real-database test: one match acts, two ask, and another org's person is never returned | AGENT-SAFE · **HIGH** (a read across people) |
| **PZ-6** | #249 | The four tools of §5.5, the Undo card, and the orchestrator prompt line | A test per tool. With `learning` off each tool refuses. Undo restores the prior value | AGENT-SAFE · MEDIUM |
| **PZ-7** | #257 | Settings → Assistant pane on the web | The member sees, edits and deletes each key and fact. The visual review passes in light mode and at compact density | AGENT-SAFE · LOW |
| **PZ-8** | #258 | WhatsApp: the tools on the WhatsApp run, and the channel limits over `style` | A WhatsApp run test shows the block and a `profile_remember` call | AGENT-SAFE · MEDIUM |
| **PZ-9** | #259 | Forget one, forget all, the switch, and the purge when access ends | A real-database test for each. A removed member leaves no profile | AGENT-SAFE · **HIGH** (a member write) |
| **PZ-10** | #260 | The eval set and the log lines | 20 fixed prompts score address, length, alias and no-leak. A log line per turn says the block's size, never its text | AGENT-SAFE · LOW |
| **PZ-11** | #261 | Web and WhatsApp give the same answer from the same profile. Staging test | Verified on staging. Turning the flags on in production is an owner act | **OWNER-GATE** for the production flip |

**Tasks that this spec does not use.** #248 (short-term session memory) is
built: the chat history window and the Redis memory block. #250 (semantic
recall) is Mem0, and WS-7 owns its turn-on (H-336, H-337, H-307). #254 and
#255 wait on D-PZ-9.

---

## 10. Verification commands

Run from the repository root. R8 needs a database first:
`bash scripts/dev_db.sh`, then `eval "$(bash scripts/dev_db.sh --export)"`.

```bash
# PZ-1
uv run pytest tests/unit/test_assistant_profile.py tests/unit/test_assistant_profile_r8.py -q
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_memory_tools_dark.py -q
# PZ-2 to PZ-6
uv run pytest tests/unit/test_executor_profile_block.py tests/unit/test_profile_tools.py -q
uv run pytest tests/unit/test_resolve_person_r8.py -q
# PZ-7
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/settings
# PZ-10
uv run python -m evals.personalization --offline
```

**Fences (R7).** `test_tenant_coverage.py` holds the column under the row's
RLS. `test_executor_profile_block.py` holds the four conditions of §5.1 and
the order of the preamble. `test_resolve_person_r8.py` holds the one-org read.
`test_memory_tools_dark.py` holds the honest answer of §2.1.

---

## 11. Open questions

1. **Should a fact expire?** A routine such as "travels every Friday" can go
   stale. The default is no expiry, and the pane shows the date so the member
   can judge.
2. **Should the assistant propose a fact after it sees a pattern?** For
   example, the member moves deep work to Friday three weeks in a row. That
   is learning from behaviour, not from words. It waits until the eval of
   PZ-10 exists.
3. **Should Mem0 promote into the profile?** `memory_architecture.md` §6.4
   promotes a fact that is used three times. That waits on WS-7.

---

## 12. Phase 2 — the learned voice, and a setup coach (PROPOSED, 2026-10-24)

**Status: PROPOSED, 2026-10-24. Nothing is built.** Owner directive of
2026-10-24, in chat. It extends §1 to §11, and it changes no rule of them.
The slices start after PZ-11. Each one is dark until the owner says.

### 12.1 The owner's ask

The owner asked for four things:

1. **Learn how the member writes,** from the mail the member already sent. The
   email app has a voice profile, and it must serve the whole app, not only
   email.
2. **Learn from more than one channel.** Mail, WhatsApp and the chat each show
   how the member talks to other people. They also show how the member wants
   to be spoken to.
3. **Ask the member, at the right moment.** When a part of the setup is
   missing, the assistant asks one question in the chat. The member can set
   their voice, their rules and their preferences as they use the app.
4. **Keep this true as the app grows.** Each new app must read the member's
   preferences through one seam, and it must name what it needs set up.

### 12.2 Two kinds of preference

The profile of §4 holds how the assistant talks **to** the member. Phase 2
adds how the member talks **to other people**. They are two things, and they
stay two things.

| Kind | Example | Home | Reader |
|---|---|---|---|
| **Inbound style** | "Keep it short", "call me VJ" | `assistant_profile` (§4) | The chat, on every channel |
| **Outbound voice** | "Signs off 'Cheers, VJ'", "formal with customers, casual with the team" | The voice profile of WS-52 S3 | Each drafter: email, WhatsApp, a message that the chat writes |

### 12.3 One voice for the member, built from each source

**No new store.** `email_voice_profiles` stays the store of the outbound
voice, one row for each mailbox (WS-52 S3). Phase 2 adds a **member voice**:
the merge of the member's mailbox profiles, read through one function in
`acb_llm.voice`. Every drafter reads that function. No drafter reads a
mailbox row directly.

**The sources, in order of trust:**

1. **What the member said.** Profile facts and style notes (§4). They always
   win.
2. **Sent mail.** The existing build job reads up to 150 sent mails, keeps
   derived features only, and stores no sample (WS-52 S3).
3. **The member's own WhatsApp messages and chat turns.** Length, language,
   formality and the greeting the member uses. Derived features only. A
   message from another person is never a source.

**The audience rule.** The voice may differ by audience: a customer, a
colleague, a supplier. Phase 2 derives this from sent mail by the domain of
the recipient. It stores a rule such as "formal with `@customer.com`", never a
mail.

### 12.4 Learned facts are proposals, never writes

D-PZ-2 stands. Nothing the app derives goes into the profile without the
member's yes. A derived item becomes a **proposal**: "You sign most mail
'Cheers, VJ'. Should I use that?" The member accepts, edits or dismisses it.
An accepted proposal writes through the PZ-6 tools, with Undo.

This answers §11 question 2. A pattern from behaviour is a proposal, and
never a fact.

### 12.5 The setup coach

**What is set up.** One function, `setup_status(org, member)`, returns each
setup item and its state: done, missing or stale. The first items:

| Item | Done when |
|---|---|
| Form of address | `address.name` is set, or the member said "no preference" |
| Answer style | `style.length` is set |
| Writing voice | At least one mailbox has a voice profile, built with consent |
| Email rules | The member has one rule or more, or said "not now" |
| Names for people | At least one alias, after the member has named a person twice |

**How it asks.** The coach uses only the seams that exist:

- **In the chat:** one question at a time, at a moment that fits. Example:
  after the chat drafts the third mail, it asks "Want me to learn your
  writing voice from your sent mail?" The nudge of WS-62 (D-CF-7) carries a
  question when no chat is open.
- **On My Day:** one card that lists the open setup items (WS-58).
- **On WhatsApp:** only through Reach (WS-59, D103), and only when the owner
  turns that on.

**Limits.** At most one setup question a day. A dismissed item waits 14 days.
"Don't ask again" stops that item for good. The member sees every item in
Settings → Assistant.

### 12.6 The preference contract for every app

**Each app joins through its shell manifest** (R9, D89). The manifest gains
two optional fields:

- `preferences`: the profile and voice keys that the app reads.
- `setup`: the setup items that the app adds to the coach, each with its own
  "done when".

**The rule.** An app reads member preferences only through
`acb_common.assistant_profile` and `acb_llm.voice`. A second store of member
preference is a defect. A later fence test fails on an app that reads
`user_settings.assistant_profile` or `email_voice_profiles` outside those two
modules.

This is how the system stays true as the app grows. A new app declares what
it reads and what it needs set up. The coach then asks for it, and the
profile serves it.

### 12.7 The slices

| Slice | What | Done when | Gate · tier |
|---|---|---|---|
| **PZ-12** | The member voice in `acb_llm.voice`: the merge of mailbox profiles. Every drafter reads it | A test shows that the email drafter, the WhatsApp drafter and a chat-written message read one function | **OWNER-GATE:** the consent wording (WS-52 S3) · HIGH |
| **PZ-13** | The audience rule from sent mail, by recipient domain | A test with fixed mail derives "formal with X" and stores no body | AGENT-SAFE · HIGH (reads mail) |
| **PZ-14** | Voice features from the member's own WhatsApp messages and chat turns | A test shows that a message from another person never counts | **OWNER-GATE:** the sources · HIGH |
| **PZ-15** | Proposals: a derived item asks, and an accepted one writes with Undo | A test shows that no derived item writes without a yes | AGENT-SAFE · MEDIUM |
| **PZ-16** | `setup_status` and the items of §12.5 | A test for each item's "done when" | AGENT-SAFE · LOW |
| **PZ-17** | The coach in the chat and on My Day, with its limits | A test for one question a day, the 14-day wait and "don't ask again" | AGENT-SAFE · MEDIUM |
| **PZ-18** | `preferences` and `setup` in the shell manifest, and the fence of §12.6 | The fence fails on a direct read outside the two modules | AGENT-SAFE · LOW |
| **PZ-19** | The coach on WhatsApp through Reach | Reach sends a setup question only with its switch on | **OWNER-GATE:** WhatsApp sends · MEDIUM |

### 12.8 Decisions the owner owes

| # | Question | Default |
|---|---|---|
| **D-PZ-10** | The consent wording before the app reads sent mail | The WS-52 S3 notice: what it reads, what it keeps, how to delete it |
| **D-PZ-11** | May the WhatsApp drafter read the email voice? | Yes, through the member voice of §12.3 |
| **D-PZ-12** | May WhatsApp messages and chat turns feed the voice? | Yes, the member's own words only, and derived features only |
| **D-PZ-13** | How often may the coach ask? | One question a day, a 14-day wait after a dismiss |
| **D-PZ-14** | May the coach ask on WhatsApp? | Not until the owner turns it on, through Reach |
