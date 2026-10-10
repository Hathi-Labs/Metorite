# WhatsApp assistant channel — a member chats with Metorite from their own WhatsApp

**Status:** WAC-3 built, dark (2026-10-10), after WAC-1 and WAC-2 on
2026-10-09. WAC-4 is next. Board row
**WS-47**. WAC-1 to WAC-5 are AGENT-SAFE, and they run on Meta's free test
number. WAC-0 (the number) is the owner's, and it gates production only. HANDOFF
**H-251** carries it.

**WAC-1 as built (2026-10-09).** The table is `whatsapp_member_links`. The
routes are in `gateway/routes/whatsapp_channel/`, and the section is
`WhatsAppLinkSection` on My Profile. The flags have one reader,
`whatsapp_channel/flags.py`. Fences: `tests/unit/test_wac_link_code.py`,
`tests/unit/test_wac_link_table.py` (R8) and
`workbench/control_plane/src/app/people/lib/whatsappLink.test.ts`.

**WAC-2 as built (2026-10-09).** `receive_webhook` sends the group of
`WHATSAPP_ASSISTANT_PHONE_NUMBER_ID` to `whatsapp_channel/inbound.py`, and
the group never reaches the WS-20 path. The bot replies with
`WHATSAPP_ASSISTANT_ACCESS_TOKEN`, after the 200. `flags.py` reads both. A
redelivery record keeps the reply of each link message by `wamid`, so a
repeat gets the same reply and counts no second failure. It is in the process
and single-process.

**The WAC-3 record replaces it for a linked sender only.** A link message from an
unlinked phone has no org, so a tenant table cannot hold it. The in-process
record stays for that case.

The migration `whatsapp_member_link_lookups` adds the two SECURITY DEFINER reads
of §5.3: `whatsapp_member_links_for_phone` and
`whatsapp_member_link_for_code`. The code function also returns an active
row of the SAME phone, for the redelivery rule. A new code for a phone that
the member already linked in that org revokes the old row, so the unique
index holds. Fences: `tests/unit/test_wac_bot_inbound.py` and
`tests/unit/test_wac_bot_link_r8.py` (R8).

**WAC-3 as built (2026-10-10).** The run is `whatsapp_channel/bot_run.py`.
The migration `whatsapp_bot_messages` adds the table and `chat_session.channel`. Before
the 200, `inbound.py` binds the org of the phone's current link. It writes the
member's turn into the WhatsApp thread, and then a `received` row by `wamid`.
The turn has an id made from the `wamid`, so the sweep reads the text back from
the thread.

After the 200, the background task starts the run as a task of its own, so a
slow run never holds the response.

The run checks the switch, the link, the member, `feature:chat` and
`resolve_identity`, in that order. Then it calls the executor's `run_agent` in
the process. `acb_skills.ask_tools.refuse_cards` makes every card tool deny at
once. `chat_fold.persist_channel_reply` writes the reply row. Fences:
`tests/unit/test_wac_bot_run.py` and `tests/unit/test_wac_bot_run_r8.py` (R8).

*(Review round 1, 2026-10-10.)* One thread runs one message at a time, oldest
first, so a second text runs right after the first. A failure after the claim
gives the row back to the sweep, and the last try sends the general text once.
A thread that gets a participant on the web is a shared room. The bot leaves
it, and the next text opens a new thread. A redelivery writes nothing, and a
bot write keeps the agent that the member chose.

*(Review round 2, 2026-10-10.)* Each message gets one reply at most, and at
least one unless the channel fails. A row becomes `sending` in the database
just before its first part goes out, and nothing runs a `sending` row again. A
crash can lose the tail of a long reply, and it never sends a part twice. A
thread is a room only when its visibility is not `private` or it has a
participant who is not its member. The member's own `owner` row is no room.

**Amended 2026-10-09 (owner, in chat: "can you see what we can start working on
and building").** Four changes, recorded in §11:

1. **No App Review gates the bot.** The bot number belongs to Hathi Labs, which
   owns the Metorite Meta app. Standard access covers a business's own assets.
   App Review (submitted 2026-10-08) matters only for the WS-20 inbox, where a
   customer connects their own number. Most of §6.2 is done already.
2. **The run uses the main Chat assistant**, not the Projects assistant (§5.5).
3. **One phone can link to every org its person belongs to**, with an org
   switcher. This replaces the one-org rule of D-WAC-2 (§5.2, §5.3).
4. **Native WhatsApp UI** (lists, link buttons, Flows) becomes two new slices,
   WAC-10 and WAC-11 (§5.11, §7).

**Verified against code on 2026-10-06** at `origin/main` `fb97075fe`. **§4.1, §5.4,
§5.9 and the WAC-2 row checked again on 2026-10-09** at `c9ea6c22b` (WAC-2 audit).
**Created:** 2026-10-06, by owner directive in chat. **Owner:** vjvarada.

**Single owner.** This spec owns WhatsApp as a way to TALK to Metorite.
`whatsapp_message_manager.md` (WS-20) owns the WhatsApp INBOX app: a member's
own business number, triage and drafts. The two share the transport code and
nothing else. `whatsapp_calls_note_taker.md` owns calls.

**Supersedes nothing.** It realises ADR-007 (`system_architecture.md`) and the
WhatsApp half of CH-4 (`competitive_hardening_2026-07.md`).

---

## 0. The answer in one paragraph

A member of a customer org chats with Metorite from **their own personal
WhatsApp**, the way an Instinct user texts Instinct. **Metorite owns one bot
number for all orgs.** The customer creates no number and configures nothing. A
member links their phone **once**, with one tap in Metorite and one message
sent. After that, every message from that phone runs the Metorite assistant **as
that member, inside that member's org**, and the reply comes back on WhatsApp.

The bot answers questions about Metorite work only, because Meta bans
general-purpose AI assistants on the WhatsApp Business Platform. About 80
percent of the plumbing exists. Three parts do not exist yet. They are the
phone link, the hand-off from the webhook into an agent run, and an org binding
that does not go through the email.

---

## 1. Scope and non-goals

### 1.1 In scope

1. One Metorite-owned WhatsApp number, on the official Meta Cloud API.
2. Linking a member's phone to that member and one org, without typing a phone
   number (§5.2).
3. An inbound text or voice note runs the assistant as the member, and the reply
   goes back as WhatsApp text.
4. A write that changes data asks for confirmation with WhatsApp reply buttons.
5. Metorite-initiated messages (a link invitation, a daily brief, a nudge)
   through Meta-approved templates, only to a member who opted in.
6. An org admin can turn the channel off for the org. A member can unlink.

### 1.2 Non-goals

- **No open-domain assistant.** The bot does not answer "write me a poem". This
  is Meta policy (§2.3), not a product preference.
- **No unofficial transport.** The whatsmeow bridge in
  `apps/services/whatsapp_bridge/` breaks WhatsApp's terms. A ban would remove
  the channel for every customer at once. This channel never uses it.
- **No number per customer org** in this workstream. It is an upgrade path
  (§10, Q2).
- **No iMessage, SMS or Telegram.** iMessage needs a third-party relay (Linq or
  Sendblue) and Apple's approval. WhatsApp is the channel that matters in India.
- **No voice calls to the bot.** The Business Calling API exists, and
  `whatsapp_calls_note_taker.md` already uses it. Calls are a later workstream.
- **No change to the WS-20 inbox app.** A message to the bot number never lands
  in anybody's WhatsApp inbox app.

---

## 2. Research record (2026-10-06)

### 2.1 What Instinct is

- **A consumer personal assistant**, from Spear Street Technology (San
  Francisco), founder Noah Shinn. It is invite-only, US-operated and 18+. It
  raised a Series B at a $2.5B valuation in August 2026.
- **The interface is a conversation.** A user texts it on iMessage or WhatsApp,
  or calls it. It also has a Mac app and a web workspace at `app.instinct.com`,
  where the user manages connections and settings.
- **A persistent cloud computer does the work.** It has a browser and stored
  credentials for the user's connected accounts (Gmail, Calendar, Drive). It
  keeps working between messages, so a job can take hours or days.
- **It messages first.** It "texts you first when something needs doing".
- **It publishes no architecture.** One review says: "Public architecture is
  thin. The company does not publish a runtime diagram." No source names its
  WhatsApp or iMessage provider.
- **Group chats.** A person with no account can join a group chat with the
  agent. That is an onboarding channel.

**The lesson for Metorite.** Instinct built no chat app of its own. The first
message IS the onboarding. That is the part worth copying.

### 2.2 How the category does the transport

- **Poke** (The Interaction Company) runs on iMessage, SMS and Telegram through
  **Linq**, a texting-API company. Poke has limited WhatsApp support because of
  Meta's ban (§2.3).
- **WhatsApp has one reliable route:** the Meta Cloud API, directly or through
  a Business Solution Provider (Twilio, Gupshup, Interakt and others). A funded
  company does not build on an unofficial client, because WhatsApp bans the
  number.
- **Inference.** Instinct's WhatsApp channel is almost certainly the Meta Cloud
  API on a business number that Instinct owns. This is an inference, not a
  published fact.

### 2.3 The constraint: Meta's 2026 policy

- **Since 15 January 2026, the WhatsApp Business Solution Terms ban "AI
  Providers" whose AI is the core product.** New accounts were bound from 15
  October 2025. ChatGPT, Copilot and Perplexity left WhatsApp because of it.
- **Allowed:** a bot that is "ancillary to a legitimate business service", for
  example bookings, order tracking, notifications and customer service.
- **Our reading.** A member who asks their own company's Metorite about tasks,
  projects and the calendar uses a business service. That reading holds only
  while the bot refuses open-domain questions. ⚠️ Meta has not ruled on our
  case. Meta enforces at its own discretion.
- **The antitrust case.** Italy's AGCM ordered interim measures in December
  2025. On 8 June 2026 it referred the case to the European Commission, which
  investigates across the EEA. Do not plan around the outcome.

### 2.4 What the channel costs

- **The number:** a prepaid SIM, a few hundred rupees. It must receive one OTP.
- **The Cloud API:** Meta charges nothing to connect or host.
- **Messages (per-message pricing since 1 July 2025):** a free-form reply
  inside the 24-hour customer-service window is free. A template that Metorite
  sends outside that window is charged by category (utility, marketing,
  authentication) and by the recipient's country. Read the live rate card at
  build time. Do not copy rates into this spec.

### 2.5 Sources

- Instinct: [instinct.com](https://instinct.com/) ·
  [Vellum breakdown](https://www.vellum.ai/blog/official-instinct-breakdown) ·
  [eesel review](https://www.eesel.ai/blog/instinct-ai-review) ·
  [rywalker research](https://rywalker.com/research/instinct) ·
  [TechBuzz, group chats](https://techbuzz.ai/articles/instinct-launches-ai-agent-group-chats-for-collaborative-tasks)
- Poke and Linq: [TechBuzz, Linq raise](https://www.techbuzz.ai/articles/linq-raises-20m-to-embed-ai-assistants-in-imessage-and-sms) ·
  [Linq texting API guide](https://linqapp.com/blog/texting-api)
- Meta policy: [respond.io explainer](https://respond.io/blog/whatsapp-general-purpose-chatbots-ban) ·
  [TechCrunch, Italy](https://techcrunch.com/2025/12/24/italy-tells-meta-to-suspend-its-policy-that-bans-rival-ai-chatbots-from-whatsapp/) ·
  [GBAF, referral to the EU](https://www.globalbankingandfinance.com/italy-regulator-drops-investigation-metas-whatsapp-ai-bot/)

---

## 3. The owner's questions, and the answers (2026-10-06)

These three questions shaped the design. They are recorded so nobody asks them
again.

**Q-A. "How does Instinct use WhatsApp, and can we do the same?"** Yes. See
§2. The difference is scope: Instinct is a general assistant, and Metorite must
stay a business service.

**Q-B. "I do not want my customer to create a WhatsApp number for the bot. The
phone number should be the customer's, and the bot chats with it."** That is
the design. Two numbers exist, and the customer owns neither one as a setup
task:

| Number | Who owns it | Who sets it up |
|---|---|---|
| The bot's number ("Metorite" on WhatsApp) | Metorite | The owner, once, through Meta (§6) |
| The member's number | The member's own personal WhatsApp | Nobody. It already exists |

The bot must have a number, because WhatsApp cannot deliver a message without a
sender. Instinct has one too. A user saves it as a contact.

**Q-C. "Do I need to buy a separate phone number?"** Yes. Buy one, keep it for
the life of the product, and keep it off every WhatsApp app. One number serves
every customer. Development does not wait for it, because Meta gives every new
app a free test number (§6.3).

---

## 4. Measured state (2026-10-06)

### 4.1 What exists and is reused

| Piece | Where | What it gives us |
|---|---|---|
| Meta webhook | `apps/services/gateway/gateway/routes/whatsapp/transport/webhook.py` (GET line 62, POST line 122) | Verify handshake, `X-Hub-Signature-256` HMAC check against `WHATSAPP_APP_SECRET`, and a parse to a sync result |
| Webhook parser | `apps/services/whatsapp_ingestion/whatsapp_ingestion/providers/webhook.py` | `parse_webhook(payload)`. It is total and never raises |
| Send seam | `whatsapp_ingestion/providers/base.py` (`BaseWhatsAppProvider` line 122, `send_text` line 133, `send_template` line 147) and `providers/cloud_api.py` | Free text and templates over the Cloud API |
| 24-hour window rule | `gateway/routes/whatsapp/transport/send.py` (route at line 62) | The template-or-text decision |
| Agent run | `gateway/routes/agent.py`: `POST /agent/run` (line 3065), `/agent/run/stream` (line 2192), `/agent/run/async` (line 3131). WAC-3 calls the executor's `run_agent` in the process instead, because these routes take the org from the email | A run as a named member, through `session_user` |
| Service identity | `packages/acb_auth/acb_auth/deps.py` `get_current_user` (line 410) | `Bearer GATEWAY_INTERNAL_TOKEN` + `X-User-Email` gives a member context |
| Projects, Tasks and Calendar tools | `apps/skills/skill-projects/skill_projects/` | Every tool calls the gateway as the member, through a per-run ContextVar |
| Chat history | `chat_session`, `chat_message` (`infra/postgres/02_chat_history.sql`), with tenant RLS | The store of the web chat. It has no channel marker, and a server-started run writes no `chat_message` rows (measured 2026-10-10). WAC-3 adds both (§5.5) |
| Speech to text | `packages/acb_stt/` | Voice notes |
| Approval of an outward act | Action Broker, as `gateway/routes/whatsapp/automation/outbound.py` uses it | The pattern for a confirm step |
| Calls | `whatsapp_calls_note_taker.md` Surface C | The later calls workstream |

### 4.2 The gaps

1. **No link from a phone to a member.** `people.phone`
   (`infra/postgres/172_people_profile.sql` line 97) is private, self-written,
   unverified and not unique. It must not identify anybody. No other member
   table holds a phone.
2. **The webhook knows only per-org numbers.** `_resolve_account` (line 93) looks
   up `phone_number_id` in `wa_accounts`, through the SECURITY DEFINER function of
   migration 230. Every row there belongs to one org and
   one owning member (`infra/postgres/102_whatsapp.sql` line 24). The bot
   number belongs to Metorite, not to an org. An unknown number is logged as
   `whatsapp.webhook.unknown_number` and dropped today.
3. **Nothing sends an inbound message into an agent run.** The post-sync hooks
   (`whatsapp_ingestion/post_sync.py`) classify and triage. They never start a
   chat turn.
4. 🔴 **The email route cannot carry a member of two orgs.**
   `acb_auth/access.py` `resolve_identity` (line 1247) has two branches.
   With the identity cutover on, it returns `(None, None)` for a person with
   more than one active membership, on purpose (P2a). With the cutover off, it
   reads one `app_user` row and binds the org of that row. In both cases,
   `X-User-Email` alone cannot pick the org that the member linked. The phone
   link already names the org. So the run must bind THAT org, and never
   re-derive it from the email (§5.5).
5. **A chat confirmation is a web card.** The Projects assistant asks for
   confirmation with a generative-UI card (`generative_ui_2.md`). WhatsApp has
   no cards. It has reply buttons (up to three) and list messages.

---

## 5. The design

### 5.1 The bot number is platform configuration, not tenant data

The bot number is not a row in `wa_accounts`. It is platform configuration on
the box:

| Variable | Holds |
|---|---|
| `WHATSAPP_ASSISTANT_PHONE_NUMBER_ID` | Meta's id for the bot number. The webhook matches it FIRST |
| `WHATSAPP_ASSISTANT_DISPLAY_NUMBER` | The bot's phone number, digits only with the country code and no "+" (for example `919800000000`). The `wa.me` link needs it, because the phone number id is not a phone number *(added 2026-10-09)* |
| `WHATSAPP_ASSISTANT_WABA_ID` | The WhatsApp Business Account id, for templates |
| `WHATSAPP_ASSISTANT_ACCESS_TOKEN` | The System User's permanent token |
| `WHATSAPP_APP_SECRET` | Exists already. The webhook HMAC key |
| `WHATSAPP_VERIFY_TOKEN` | Exists already. The GET handshake token |
| `WHATSAPP_ASSISTANT_ENABLED` | The kill switch, default OFF |
| `WHATSAPP_ASSISTANT_ORGS` | The list of org ids that may link, default empty |

**Why not a `wa_accounts` row.** That table is tenant data, owned by one member
of one org, under RLS. Putting a cross-tenant number in it would give one org a
row that routes every other org's traffic. (D-WAC-1, §10.)

### 5.2 Linking a phone, with nothing typed

**Flow A, member-initiated (the default).**

1. The member opens **My Profile** (`/people/me`) and selects **"Chat on
   WhatsApp"**. The section adds no nav entry *(host page named 2026-10-09)*.
2. Metorite issues a single-use code that expires in 15 minutes. It stores only
   a hash of the code, with the member and the org.
3. Metorite shows a `https://wa.me/<bot number>?text=Link%20me%3A%20<code>`
   link and a QR code of the same link for a desktop user.
4. The member taps it, and WhatsApp opens with the message ready. They send it.
5. The webhook sees the bot number and finds a link code in the text. It checks
   the hash and the expiry. Then it writes the link: the sender's WhatsApp id,
   the member and the org.
6. The bot replies: "Linked to <org name> as <member name>. Ask me about your
   tasks, projects or calendar."

The message proves the member holds the phone. The signed-in session proves who
the member is. No SMS OTP is needed.

**Flow B, Metorite-initiated.** The member, or an admin, enters the number once,
and the member ticks "Message me on WhatsApp" (the opt-in Meta requires).
Metorite sends an approved template: "Hi, this is Metorite. Reply YES to
connect." A YES within 24 hours writes the link. No link exists before the reply,
so a wrong number links nobody.

**One phone, one person, and every org of that person (D-WAC-2, amended
2026-10-09).** A phone links to one person. That person can link the phone in
each org where they are a member, one link per org. Exactly one of those links
is the **current** link, and every run uses it. The member changes it with the
org switcher (§5.11), and the last choice stays.

**A second person can never link the same phone.** The phone can already have an
active link to one member email. Then the bot refuses a link code from a
different member email, and tells the sender why.

**The routes (WAC-1).** A router of its own, never the `/whatsapp` router, because
`require_feature_router("whatsapp")` gates that router for the inbox app. `GET
/me/whatsapp-link` returns the channel state and the member's links. `POST
/me/whatsapp-link/code` issues a code. Both need a signed-in member with
`feature:chat`, because the run of WAC-3 is a Chat run. Both refuse when
`WHATSAPP_ASSISTANT_ENABLED` is off or the bound org is not in
`WHATSAPP_ASSISTANT_ORGS`. The org comes from the bound tenant, never from the
request.

**A new code replaces the old one.** Issuing a code revokes the member's earlier
`pending` row in the same org, so one member has at most one live code per org.

**The QR code renders in the browser** with the `qrcode` npm package, from the
same `wa.me` link. The server sends no image.

### 5.3 The link table

A new table, name chosen at build time (proposed `whatsapp_member_links`).
Columns: `id`, `organization_id`, `member_email`, `wa_id` (the sender id Meta
sends, digits only), `status` (`pending`, `active`, `revoked`), `code_hash`,
`code_expires_at`, `opted_in_at`, `linked_at`, `last_inbound_at`, `revoked_at`.

- **R5.** The table is tenant-scoped, with RLS, and it passes
  `tests/unit/test_tenant_coverage.py`.
- **The cross-tenant reads** *(corrected 2026-10-09)*. The webhook knows no
  org when a message arrives. The table has FORCE RLS (R5), so each read is a
  SECURITY DEFINER function granted to `acb_app` only, in the shape of
  migration 230. It is not a new connection site (R5b). WAC-2 adds two
  functions:
  - **by `wa_id`**, for a message: the active links of that phone
  - **by `code_hash`**, for a redemption: the one `pending` row of that code,
    because the sender of a code does not say which org issued it
  *(Was: "the same mechanism as `resolve_identity`". That was wrong:
  `resolve_identity` reads tables that are exempt from RLS.)*
- **The code hash is SHA-256 of the code.** A code has 10 characters from a
  32-character alphabet, which is 50 bits. It expires in 15 minutes and works
  once, so a copy of the database cannot be used to redeem one later.
- **Uniqueness** *(amended 2026-10-09)*. A partial unique index on
  `(wa_id, organization_id)` where `status = 'active'`, and a second partial
  unique index on `wa_id` where `is_current` is true. A column `is_current`
  (boolean, default false) joins the column list.
- **The cross-tenant read returns every active link of the phone**, each as
  `(organization_id, member_email, is_current)`. The org switcher needs the
  list. The run uses only the current row.
- **One person per phone.** The code redemption refuses a link when the phone
  already has an active link to a different `member_email`. This check runs in
  the same narrow read, so it needs no wider access.
- **R6.** The migration only adds. The number is taken at build time (R1).

### 5.4 The inbound path

```
Meta ──POST /whatsapp/webhook──▶ verify HMAC ──▶ parse
        │
        ├─ phone_number_id ≠ bot number ──▶ today's path, unchanged (WS-20)
        │
        └─ phone_number_id = bot number
              ├─ status update only ──▶ log the status, ack 200 (no store before WAC-3)
              ├─ text holds a link code ──▶ §5.2 redemption, reply, ack 200
              ├─ wa_id has no active link ──▶ fixed reply with NO org data, ack 200
              └─ wa_id is linked ──▶ record the message by wamid (idempotent)
                                     ──▶ ack 200 at once
                                     ──▶ run the assistant in the background (§5.5)
                                     ──▶ send the reply (§5.6)
```

- **Ack fast.** Meta retries a slow webhook, and a retry is a duplicate. The
  route returns 200 before the agent runs.
- **Idempotent by `wamid`.** Meta can deliver one message twice. A second
  delivery of the same `wamid` starts no second run.
- **The reply to an unknown sender carries no org data.** It says how to link,
  and nothing else. It never names an org, a member or a task.
- **Durability.** A crash between the ack and the run must not lose the message
  in silence. The recorded message carries a state, and a sweep re-runs a
  message that stayed `received` too long. Use the BO-20 durable queue (WS-4)
  if it has landed by build time. Do not build a second queue.

**The bot message record, as WAC-3 builds it** *(set 2026-10-10 from the WAC-3
audit)*. BO-20 has not landed (WS-4 row). So WAC-3 adds the table
`whatsapp_bot_messages`, tenant-scoped with FORCE RLS. Its columns are `id`,
`organization_id`, `member_email`, `wa_id`, `wamid` (unique), `direction` (`in`
or `out`), `state`, `chat_session_id`, `tries`, `error_code`, `received_at`
and `updated_at`. The states are `received`, `running`, `replied`, `refused`,
`failed` and `expired`.

- **It holds no message text.** The text lives only in the chat thread (§5.9).
- **One `wamid`, one run.** A redelivery inserts nothing (`ON CONFLICT DO
  NOTHING`). Only the insert that wrote the row starts a run.
- **The sweep.** Every minute, a sweep binds each org of
  `WHATSAPP_ASSISTANT_ORGS` in turn, so it needs no new SECURITY DEFINER
  function. It runs a row again when the row stayed `received` or `running` for
  more than 5 minutes, for at most 3 tries. A row older than 24 hours becomes
  `expired` and gets no reply, because the reply window has closed.

**Link redemption, as WAC-2 builds it** *(set 2026-10-09 from the WAC-2 audit)*:

- **Status updates.** WAC-2 acks a status update and writes a log line with the
  status and no message text. Nothing stores it. WAC-3 records the bot's own
  messages, and a delivery state can attach to that record then.
- **The fixed replies.** The bot sends exactly these texts. Only the success
  reply holds org data, and it goes only to the phone that just proved it holds
  the code.

  | Case | Reply |
  |---|---|
  | Unknown phone, no code | "Hi, this is Metorite. To chat with your workspace, open My Profile in Metorite and select Chat on WhatsApp." |
  | Wrong, used or expired code (one text for all three) | "That link code did not work. Open My Profile in Metorite and get a new link." |
  | The phone belongs to another person | "This phone is already linked to another Metorite account. Unlink it there first." |
  | Too many failed codes | The same text as a wrong code |
  | Success | "Linked to <org name> as <member name>. Ask me about your tasks, projects or calendar." |

  One text covers wrong, used and expired codes, so a reply never tells a
  guesser which case it hit.
- **A redelivered link message.** Meta can deliver the same message twice. When
  the code's row is already `active` with this sender's `wa_id`, the bot sends
  the success reply again and changes nothing. It never answers a redelivery
  with the failure reply.
- **The per-sender limit.** At most 5 failed redemptions for each `wa_id` in 15
  minutes. After that, the bot sends the failure reply and runs no lookup. The
  counter lives in the gateway process, so a restart resets it. The gateway
  runs one process (`deploy/hostinger/acb-gateway.service`). A move to several
  workers makes this limit advisory, and that move must replace it. A 50-bit
  code that expires in 15 minutes leaves little to guess, and every sender is a
  real WhatsApp phone behind Meta's HMAC.
- **The per-member issue limit.** At most 10 codes for each member in each org
  in one hour. The route counts them from `whatsapp_member_links.created_at`,
  under the advisory lock that it already takes. Beyond it, the route
  answers 429 with a plain sentence, and writes nothing.
- **The first link of a phone is current.** Redemption sets `is_current` only
  when the phone has no current link. A later org of the same person links
  with `is_current` false, and the org switcher (WAC-10) moves it.
- **The bot path fails closed.** With `WHATSAPP_APP_SECRET` unset, the bot path
  does nothing, in dev too. The WS-20 path keeps its own rule.

### 5.5 The run

- **The agent** *(amended 2026-10-09)*. The run uses the main Chat assistant,
  the default agent of `/chat`, with its specialist agents. The owner wants the
  bot to be an entry point to all of Metorite, not to Projects only. The
  channel adds no agent of its own. Tools that need a card on the web follow
  §5.6.
- **The identity.** The run is the linked member. ⚠️ **The org comes from the
  link row and is bound explicitly.** It is never re-derived from the email,
  because §4.2 gap 4 makes that fail for a member of two orgs. It is never read
  from the message text. (D-WAC-3.)
- **The tools find the org again** *(measured 2026-10-10)*. Each tool calls the
  gateway with `X-User-Email` (`skill_projects/client.py`). The gateway binds
  the org from `resolve_identity` (`acb_auth/deps.py`). So binding the org on
  the run does not reach the tools.
- **So the run checks the identity first.** Before each run, WAC-3 checks that
  `resolve_identity(member_email)` returns the org of the current link. When it
  does not, the run does not start. The bot sends this fixed text: "Metorite
  cannot open this workspace from WhatsApp yet. Use the web app." A run never
  answers from an org that the link does not name.
- **What this costs today.** Nothing yet: `app_user` is unique on the email
  across all orgs (migration 162), so every link that exists resolves to its
  own org. A full answer for a member of two orgs needs a per-request org claim
  on the tool loopback. WS-35 slice B owns that, and WAC-3 does not build it.
- **Still a member, and still allowed.** Before each run, check four things. The
  member is active in the link's org (a bound read of `app_user.status`). The
  member holds `feature:chat` there. The kill switch is on. The org allowlist
  still holds the org. Refuse a second live run on the same thread. WAC-3 refuses the
  run of a removed member. WAC-8 revokes the link.
- **The scope rule.** The run carries a channel instruction: answer about this
  member's Metorite work only, and refuse other topics in one line. Keep replies
  short, and write plain text, because WhatsApp shows plain text and a little
  markup.
- **The thread** *(set 2026-10-10)*. The migration of WAC-3 also adds
  `chat_session.channel text` (nullable, no default, expand only). WAC-3 writes
  `whatsapp` there. The thread is the member's latest `whatsapp` session in the
  link's org. A message more than 24 hours after the last one starts a new
  session. WAC-3 writes the member's turn and the reply through `routes/chat.py`
  `_upsert_session` and `_upsert_messages`, with the link's org. A badge in the
  web sidebar is not part of WAC-3.
- **Metering.** The run goes through the Router like every other chat run, and
  it spends the org's AI credits by the same rules. WhatsApp adds no second
  meter.

### 5.6 The reply

- A reply inside the 24-hour window is free-form text through `send_text`.
- Split a long reply at paragraph breaks. WhatsApp's limit is 4096 characters.
- **WAC-3 is reads only** *(set 2026-10-10)*. A server-started run pushes a card
  that nobody sees, and the tool would wait up to an hour. So in a WhatsApp run,
  a tool that asks for a card gets a deny at once. It writes nothing, and the
  run does not wait. The reply says to make the change in the web app. Capture
  moves to WAC-4. *(Accepted 2026-10-10, WAC-3 review.)* The member's own
  memory tools (`remember`, `save_memory` and `save_episode`) ask for no card,
  so they still write the member's own memory from WhatsApp.
- **A rate limit per member is due before WAC-9** *(WAC-3 review)*. WAC-3 runs
  every text of a linked member, and only the org's AI credits cap the cost.
  WAC-9 must not switch on an org before a limit on bot runs per member exists.
- **A failed run gets one fixed reply**, chosen by `classify_run_error`. For the
  code `credits` the reply is: "Your organization is out of AI credits. An admin
  can add credits in Settings, Billing." Every other code gets: "Metorite could
  not answer just now. Try again in a few minutes."
- **A write asks first** (WAC-4). A tool that needs a card on the web asks here
  with reply buttons ("Confirm", "Cancel"). The button payload carries a signed,
  single-use, short-lived token for that one pending act. A tap from any other
  phone, or after expiry, does nothing.
- **A destructive act never runs from WhatsApp.** A delete, a move or any
  guarded act (Projects S3 class) replies with a link to confirm it in the web
  app. A stolen phone must not be able to destroy data. (D-WAC-4.)
- Each reply can end with a deep link to the item in Metorite.

### 5.7 Voice notes

Meta sends an audio message as a media id. Download it with the bot's token,
transcribe it through `acb_stt`, and treat the text as the member's message.
Echo the transcript in the reply ("Heard: …") so the member can catch a wrong
transcription. Delete the audio file after transcription.

### 5.8 Messages that Metorite starts

- **Only to a phone with an active link and `opted_in_at` set.**
- **Only through approved templates.** Each template is a utility template with
  a reply button, so the member's tap opens a free 24-hour window.
- **First templates:** the link invitation (flow B), and the daily brief (the
  "My day" report of `projects_reports.md`). The third is a nudge on a task that
  waits on the member.
- **The member stops them** by sending "STOP", or from Settings. "START"
  resumes.

### 5.9 Security and tenancy

| Risk | Control |
|---|---|
| A SIM swap gives the phone to someone else | Destructive acts go to the web app (D-WAC-4). An admin can revoke a link. A link expires after 90 days with no inbound message (the period is Q5) |
| A forged webhook | HMAC on every POST. A missing `WHATSAPP_APP_SECRET` must fail CLOSED for the bot path, in dev too. Today's route answers 403 with no secret outside dev, and accepts in dev |
| A message arrives from an unknown phone | §5.4. No org data in the reply |
| A link code is guessed | 8 characters or more, single use, 15-minute expiry, a hash at rest, and a rate limit per sender |
| A member leaves the org | The membership check in §5.5 revokes the link |
| The org turns the channel off | An org setting stops new links and stops every run for that org |
| Org data passes through Meta | State it in the trust record (WS-37) and the DPDP notice. Store no message text beyond the chat thread |

### 5.11 Native WhatsApp UI *(added 2026-10-09)*

WhatsApp offers native elements. The bot uses them where they help, and plain
text everywhere else.

| Element | Limit | Use |
|---|---|---|
| Reply buttons | 3 buttons | Confirm and Cancel (§5.6). Quick acts on one task: Done, Tomorrow, Snooze |
| List message | 10 rows in sections | Today's tasks, where a row opens the task. A project picker. **The org switcher** |
| Link button (`cta_url`) | 1 button | "Open in Metorite" for any item |
| Typing indicator | Shows up to 25 seconds | Sent when a run starts, so the member knows the bot works |
| WhatsApp Flows | Multi-screen forms | "New task" with a project dropdown, an assignee and a calendar picker. A Flow fetches the member's projects from a Flow endpoint (WAC-11) |
| Document and image | Files | A report as a PDF. A chart as an image |

**The org switcher.** "Switch org" (typed, or a list row) sends a list message
of the member's linked orgs. A tap sets that link as current. The reply names
the new org. A member with one org never sees the switcher.

**The tap is untrusted input.** A list row id or a button id from WhatsApp is
data the phone sent. The webhook checks it against the sender's own links and
pending acts before it acts. A row id never carries an org id that the sender
has no link to.

**Flows need an endpoint key.** A Flow endpoint encrypts its payload with an RSA
key pair. The private key is platform configuration on the box, like §5.1. The
owner puts it there (WAC-11).

### 5.10 Shell manifest (R9)

This is a channel, not an app, so R9 does not bind it. It adds one entry in
member settings ("Chat on WhatsApp") and one org setting for admins. It adds no
pane, no palette, no bell and no ⌘K listener.

---

## 6. Owner setup — the Meta side (WAC-0, OWNER-GATE)

**Why it is the owner's.** It creates the company's identity at a third party.
It adds a payment method. It puts a long-lived secret on the box (`CLAUDE.md`
§3a rule 3). HANDOFF **H-251** carries it.

### 6.1 Get the number

1. Buy a new prepaid SIM, registered to the company. A landline or a virtual
   number also works, if it can receive a voice-call OTP.
2. Make sure the number is on NO WhatsApp app. If it is, delete that WhatsApp
   account first. Do not use a personal number.
3. Keep the SIM recharged. An Indian carrier recycles an inactive number, and a
   recycled number can cost us control of the bot.

### 6.2 Set up Meta

1. Create a **Meta Business Portfolio** at business.facebook.com for the legal
   entity.
2. **Verify the business:** incorporation or GST documents, and the
   `metorite.com` domain. Before verification, Meta limits how many chats the
   business can start each day.
3. At developers.facebook.com, create an app of type **Business** and add the
   **WhatsApp** product. Meta creates a WhatsApp Business Account and a free test
   number.
4. In WhatsApp Manager, add the real number and verify it by OTP. Set the
   display name to **"Metorite"**. Meta reviews the name against the website.
   Turn on the two-step PIN.
5. In Business Settings, create a **System User**. Give it a permanent token
   with `whatsapp_business_messaging` and `whatsapp_business_management`.
6. Set the webhook. The callback URL is
   **`https://api.metorite.com/whatsapp/webhook`**. Caddy already sends
   `api.metorite.com` to the gateway. Set the verify token, and subscribe to the
   **`messages`** field.
7. Add a **payment method** in WhatsApp Manager.
8. Submit the templates of §5.8 for approval.
9. Publish the app (Live mode). Meta requires a privacy-policy URL.

### 6.3 Develop before 6.1 and 6.2 finish

The test number from step 3 can message up to five recipient numbers that the
owner verifies in the app dashboard. That is enough to build and demo WAC-1 to
WAC-5. Put the test number's id and a temporary token in a local `.env` only.

### 6.4 What reaches the box

Five values: the phone number id, the display number, the WABA id, the app
secret and the access token (§5.1). The owner puts them in the box `.env`. They never go into chat, a
commit or a log line.

---

## 7. Tickets

Every ticket ships dark behind `WHATSAPP_ASSISTANT_ENABLED` (default OFF) and
`WHATSAPP_ASSISTANT_ORGS` (default empty). Each one is one PR.

| Id | What | Gate | Done when |
|---|---|---|---|
| **WAC-0** | The Meta setup of §6 | **OWNER-GATE** | The five values of §5.1 are on the box, and `GET /whatsapp/webhook` answers Meta's handshake |
| **WAC-1** ✅ built 2026-10-09 | The link table (§5.3), its migration, the code-issue route, and "Chat on WhatsApp" in member settings | AGENT-SAFE | A signed-in member gets a `wa.me` link and a QR. The code is stored as a hash and expires. The migration applies on a real database (R8). `test_tenant_coverage.py` passes |
| **WAC-2** ✅ built 2026-10-09, dark | The webhook branch for the bot number (§5.4): code redemption, the unknown-sender reply, status updates | AGENT-SAFE | A link message from the test phone writes an `active` row and gets the confirmation reply. A message from an unlinked phone gets the fixed reply, and the reply holds no org data. A wrong, used or expired code links nothing. The WS-20 path is unchanged for every other `phone_number_id`. *(Added 2026-10-09, WAC-1 review and WAC-2 audit.)* `POST /me/whatsapp-link/code` answers 429 after 10 codes for one member in one org in an hour. The sixth failed code from one `wa_id` in 15 minutes gets the failure reply with no lookup. A redelivered link message re-sends the success reply. With `WHATSAPP_APP_SECRET` unset, the bot path does nothing, in dev too. A second member email on a linked phone links nothing |
| **WAC-3** ✅ built 2026-10-10, dark | A linked text runs the main Chat assistant and the reply comes back (§5.4 bot message record, §5.5, §5.6). Read tools only: a card is denied at once (capture moves to WAC-4) | AGENT-SAFE | *(Made testable 2026-10-10.)* A1: a linked sender's message writes one `received` row, the route answers 200 before the run, and `run_agent` gets agent `orchestrator`, the link row's org, the link's email as the session user, and the scope rule in `system_context`. A2: one payload delivered twice, or twice at once, starts one run. A3: `resolve_identity` returning another org or none starts no run and sends the fixed refusal. A4: a member not active in the link's org gets no run and no reply. A5 (R8): a `chat_session` with channel `whatsapp` and its user and assistant rows exist in the link's org, `GET /chat/sessions` lists them for that member, and another org sees none. A6: a card tool in a WhatsApp run returns at once and writes nothing. A7: a `credits` failure sends the credits text, any other failure sends the general text, and the row ends `failed`. A8: the sweep runs a stale `received` row once, a row older than 24 hours ends `expired` with no send, and the tries stop at 3. A9: flag off or org off the allowlist starts no run. A10: the migration applies on a real database and `test_tenant_coverage.py` passes. Fences: `tests/unit/test_wac_bot_run.py`, `tests/unit/test_wac_bot_run_r8.py`. The live phone check stays the manual step of §8 |
| **WAC-4** | Confirmation by reply buttons for class B writes, and the web-app link for guarded acts (§5.6) | AGENT-SAFE | "Add a task: call the vendor tomorrow" asks with buttons, and Confirm writes exactly one task. A tap from another phone or after expiry writes nothing. A delete request answers with a web link and changes nothing |
| **WAC-5** | Voice notes (§5.7) | AGENT-SAFE | A voice note runs as its transcript, the reply echoes the transcript, and no audio file remains after the run |
| **WAC-6** | Flow B, the Metorite-initiated link (§5.2) | AGENT-SAFE to build, **OWNER-GATE** to send to a real person | With a test template, YES links the phone and no reply links nothing |
| **WAC-7** | Proactive messages: the daily brief and the nudge, STOP and START (§5.8) | AGENT-SAFE to build, **OWNER-GATE** to switch on | A brief goes only to an opted-in, linked phone. STOP ends every Metorite-initiated message to that phone |
| **WAC-8** | Org controls: the org setting, admin revoke, revoke on membership removal, link expiry | AGENT-SAFE | An org with the channel off gets no run and no new link. A removed member's link is `revoked` before their next message |
| **WAC-9** | Production switch-on for an org | **OWNER-GATE** | The owner names the org. The flag holds it, and a smoke message on production gets an answer. Report the box and the SHA (§3a rule 2) |
| **WAC-10** | Native UI, part 1 (§5.11): list messages, the link button, the typing indicator, the org switcher | AGENT-SAFE | "What is due today?" returns a list message, and a tap on a row returns that task. A member of two orgs switches orgs with the list, and the next answer comes from the new org. A row id for an org the sender has no link to changes nothing |
| **WAC-11** | Native UI, part 2: the "New task" Flow and its endpoint (§5.11) | AGENT-SAFE to build, **OWNER-GATE** for the endpoint key on the box | The Flow opens from a button, lists the member's own projects, and its submit writes exactly one task in the current org. A Flow token from another phone writes nothing |

**Order:** WAC-1 → WAC-2 → WAC-3 → WAC-4 → WAC-10 → WAC-8 → WAC-5 → WAC-11 →
WAC-6 → WAC-7.
WAC-8 comes before any production use. WAC-9 can follow WAC-8.

---

## 8. Verification commands

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"   # R8: without it the DB tests SKIP
uv run pytest tests/unit/test_wac_*.py -q   # not test_whatsapp_assistant_*, which is WS-20's
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_index_completeness.py tests/unit/test_handoff_queue.py -q
uv run pytest tests/unit/test_whatsapp_webhook*.py -q                 # the WS-20 path stays green
node .claude/hooks/ste-lint.mjs project-docs/specs/whatsapp_assistant_channel.md
cd workbench/control_plane && npx tsc --noEmit && npx vitest run   # the My Profile section
```

The end-to-end check uses Meta's test number and the owner's phone. It is a
manual step, and its evidence is the reply on the phone plus the run log line.

---

## 9. Fences (R7)

| Rule | Fence (written by the slice named) |
|---|---|
| The run's org comes from the link row, never from the email or the message, and a run whose email resolves to another org does not start | A test with `resolve_identity` patched to another org (WAC-3, A3) |
| A card tool in a WhatsApp run never waits and never writes | A test that runs a class B tool in a WhatsApp run (WAC-3, A6) |
| An unknown sender gets no org data | A test that asserts the reply text is the fixed string (WAC-2) |
| One `wamid` starts one run | A test that delivers one payload twice (WAC-3) |
| A link code is single-use and expires | Tests for reuse and expiry (WAC-1, WAC-2) |
| The bot path fails closed with no app secret | A test with `WHATSAPP_APP_SECRET` unset (WAC-2) |
| A guarded act never runs from WhatsApp | A test that asks for a delete and asserts no write (WAC-4) |
| The bot refuses open-domain questions | **Advisory.** The instruction text carries the rule, and an eval case checks it. No unit test can prove a model's refusal |

---

## 10. Proposed decisions and open questions

### 10.1 Proposed decisions (the owner accepts them, or changes them)

- **D-WAC-1.** The bot number is platform configuration, not a `wa_accounts`
  row (§5.1).
- **D-WAC-2** *(amended 2026-10-09, §11)*. One phone belongs to one person. It
  can link to each org of that person, and one link is current. *Was:* one phone
  has at most one active link, to one member in one org
  (§5.2).
- **D-WAC-3.** The run binds the org from the link row (§5.5).
- **D-WAC-4.** A destructive or guarded act never runs from WhatsApp. It
  replies with a web-app link (§5.6).
- **D-WAC-5.** The bot answers about Metorite work only (§2.3).

### 10.2 Open questions for the owner, each with a default

| Q | Question | Default if unanswered |
|---|---|---|
| Q1 | Pricing: is the channel in the ₹500 seat, or a paid add-on? Who pays Meta's template charges? | In the seat. AI use spends credits as usual. Metorite absorbs template charges during the pilot |
| Q2 | One shared number, or one number per org? | One shared number. A per-org number is a later upgrade for a customer who wants its own brand |
| Q3 | Display name: "Metorite" or "Metorite Assistant"? | "Metorite" |
| Q4 | Which org goes first? | Fracktal (customer zero, D36) |
| Q5 | How long does a link live with no inbound message? | 90 days |
| Q6 | Flow B: may an admin enter a member's number, or only the member? | Only the member |

---

## 11. Amendments of 2026-10-09

The owner asked to start the build while they get the bot number. Four
changes follow from the research of that day. The owner may still reverse any
of them.

| # | Change | Why |
|---|---|---|
| A1 | No App Review gates the bot | The number belongs to the app's own business. Standard access covers it |
| A2 | The run uses the main Chat assistant | The owner wants one entry point to all of Metorite |
| A3 | One phone links to every org of its person, with a current link and an org switcher | A person can be a member of two orgs. One shared bot number serves every org |
| A4 | Native UI slices WAC-10 and WAC-11 | The owner asked for UI elements inside the chat. §5.11 lists what WhatsApp allows |

**Interpretation recorded.** "A bot that can chat with all the customers of all
the organizations" is read as: the members of every Metorite org. The end
customers of an org (for example, a café's clients) are a different product,
and this spec does not cover them. The owner has not confirmed this reading.
