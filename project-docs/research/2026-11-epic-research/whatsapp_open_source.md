# Open-source study: WhatsApp bots for companies (research, 2026-11-10)

Method: GitHub pages and vendor docs fetched on 2026-11-10. GitHub pages rarely showed commit dates, so
"last activity" is UNVERIFIED unless stated. Items marked [K] come from prior knowledge, not a fetch.

## 0. BLUF

- **Official Cloud API Groups exist but are tiny:** max 8 participants per group, invite-link join only, Official Business Account (OBA) needed,
  no interactive messages, no edit or delete, billed per delivered message. A bot cannot join a company's existing 60-person office group.
- **So "bot in office groups with @mention" has two paths:** (A) official: small API-created groups (<=8); (B) unofficial linked-device
  (Baileys/whatsmeow style) which reads any group but risks bans and breaks Meta terms. Treat B as an opt-in, flagged, per-tenant "Labs" channel at most.
- **Customer support and proactive templates fit the official API cleanly** (1:1, 24h window, templates, webhooks).
- UNVERIFIED: how @mentions appear in Cloud API group webhooks. No source I fetched covered it. Check Meta docs
  (developers.facebook.com/documentation/business-messaging/whatsapp/groups/webhooks/) before design freeze.

## 1. Per-project notes

### Chatwoot - github.com/chatwoot/chatwoot
- License MIT (core) with an `enterprise/` folder under a separate licence (scope not confirmed). ~39.6k stars. Last activity: not shown (7,109 commits on develop).
- Rails + Vue + ActionCable, Postgres, Redis, Sidekiq [K].
- Architecture: Account = tenant. Inboxes per channel; WhatsApp Cloud inbox receives Meta webhooks, sends via Graph API. Conversations have status
  pending/open/resolved. **AgentBot** = external HTTP bot attached to an inbox. Chatwoot POSTs `message_created`, `message_updated`, `widget_triggered`
  (signed with a secret) to the bot URL. Bot replies via Chatwoot API. New conversations start `pending` (bot owns them).
  Handoff = bot sets status `open`. An agent can set it back to `pending` to return to the bot. Captain = built-in AI agent (docs chwt.app/captain-docs).
- COPY: pending/open status as the bot-vs-human ownership flag. Bot as a webhook consumer with a signed secret. Inbox per number. Templates synced from Meta.
- AVOID: Rails monolith coupling. Account scoping done in app code rather than DB RLS. Groups are not a first-class concept.
- Fit: best reference for the support-bot and handoff flows. Strong. No group or @mention model.

### Evolution API - github.com/EvolutionAPI/evolution-api
- Apache-2.0 plus brand-protection conditions (keep logo, notify usage). ~10.1k stars. Activity date not shown (2,629 commits).
- Node 20, TypeScript, Express, Prisma, Postgres/MySQL, Redis.
- Architecture: one "instance" per WhatsApp connection, each with its own token. Two drivers: Baileys (QR, unofficial) and Cloud API (official).
  Events fan out to webhook, RabbitMQ, SQS, NATS, Kafka, WebSocket, Pusher. Built-in integrations: Typebot, Chatwoot, OpenAI (Dify, n8n [K]).
- COPY: one connection abstraction with two drivers behind one interface. Event fan-out to a broker. Per-instance queue config.
- AVOID: instance token as the only isolation. Mixed unofficial/official in one process. Licence brand clauses. Per-instance (not per-tenant) data model.
- Fit: good template for a "channel driver" interface. Do not adopt as the runtime (not row-level multi-tenant).

### Baileys - github.com/WhiskeySockets/Baileys
- MIT. ~11.6k stars, 287 open issues, 48 open PRs. Maintainer Rajeh Taher, sponsor-funded. Docs are work in progress. Breaking change in 7.0.0. Releases section empty on page.
- TypeScript, WebSocket client of the WhatsApp Web multi-device protocol.
- Architecture: you hold an auth state (keys, creds) and persist it yourself. Multi-session means many sockets in one process. Group metadata and
  mentions (`contextInfo.mentionedJid`) are available [K].
- COPY: nothing for production. Useful as the reference for what group events look like.
- AVOID: for a paid SaaS, an unofficial client against WhatsApp terms. Account ban risk lands on the customer's number. Auth-state storage is your problem.
- Fit: poor for official-first. Only for an opt-in, disclosed "linked device" channel.

### whatsmeow - github.com/tulir/whatsmeow
- MPL-2.0. ~7.8k stars. Activity date not shown (1,718 commits). Go.
- Multi-device library. Group management and group change events supported. SQL session store (`sqlstore`) [K]. Powers mautrix-whatsapp.
- COPY: SQL-backed device store, event-handler model. Strongest unofficial library for correctness.
- AVOID: same ban/terms risk as Baileys. Go runtime would be a second stack for us.
- Fit: only if we ever ship the unofficial lane, and then as an isolated sidecar per tenant.

### whatsapp-web.js - github.com/pedroslopez/whatsapp-web.js
- Apache-2.0. ~22.9k stars. Activity date not shown (901 commits). Node >=18, Puppeteer driving WhatsApp Web in headless Chrome.
- Mention users and groups supported. Auth strategies LocalAuth / RemoteAuth [K] (docs on wwebjs.dev).
- README: "WhatsApp does not allow bots or unofficial clients ... not totally safe."
- AVOID: one Chrome per session is heavy. Do not use for multi-tenant SaaS.
- Fit: poor.

### WPPConnect (server) - github.com/wppconnect-team/wppconnect-server
- Apache-2.0. ~1.1k stars. 38 open issues. Node 22, Chromium, Swagger at /api-docs.
- Multi-session by URL (`/api/:session/...`), per-session token, secret key for admin. Has a Chatwoot bridge endpoint with bearer session token and
  outbound URL allowlists (`WEBHOOK_ALLOWED_URLS`).
- COPY: outbound webhook URL allowlist (SSRF control). Token per session. Redact tokenised URLs in logs.
- AVOID: same unofficial/browser-based risks.
- Fit: poor as runtime, good as security checklist.

### Typebot - github.com/baptisteArno/typebot.io
- Licence: README says Functional Source License ("Fair Source") but badge says AGPLv3. UNVERIFIED which. Either way, not permissive. ~10.6k stars.
- Next.js, Bun, Nx. 34+ blocks (bubbles, inputs, logic). WhatsApp via its own integration [K]; README did not mention it.
- COPY: visual flow with typed blocks (text, buttons, input, condition, webhook call). Good for admin-built scripted flows.
- AVOID: licence. Do not embed. Flow engines are weak for open-ended LLM tool use.
- Fit: idea source for a guided "set up a support flow" builder only.

### Botpress - github.com/botpress/botpress
- MIT, ~15.0k stars. Repo now centres on Botpress Cloud. On-prem v12 is in a separate repo. Plugins "coming soon".
- TypeScript, pnpm, Turborepo. Integrations defined by `integration.definition.ts` (typed actions, events, channels, configuration schema). `bp deploy` to a
  workspace, private by default.
- COPY: **typed integration definition** (actions, events, config schema, secrets) as the unit of a bot "skill". Private-by-default, publish later.
- AVOID: cloud-first lock-in. A public version cannot be updated again.
- Fit: good model for our skill/tool manifest.

### n8n - github.com/n8n-io/n8n
- Sustainable Use Licence (fair-code), ~210k stars. WhatsApp Business Cloud node and WhatsApp Trigger node exist [K]. Queue mode = main + Redis + workers [K].
- COPY: Trigger node verifies Meta's webhook handshake and signature. Templates as a node operation.
- AVOID: licence limits offering it as a service. Per-workflow credentials are not row-level tenants.
- Fit: do not embed. Customers can still wire n8n through our webhooks/MCP.

### Rasa - github.com/RasaHQ/rasa
- Apache-2.0, ~21.4k stars. Open Source is in **maintenance mode**. Direction is CALM (LLM understands, code enforces business logic). WhatsApp not in the README channel list.
- COPY: CALM principle: LLM picks the flow, deterministic code runs the steps. Matches "approvals by member".
- AVOID: adopting the framework. Python NLU stack is heavy.
- Fit: idea only.

### mautrix-whatsapp - github.com/mautrix/whatsapp
- AGPL-3.0. ~1.9k stars. Latest release v26.10 dated 16 Oct (year inferred 2026). Go, on whatsmeow, bridgev2 framework [K].
- Architecture: puppeting bridge. A user logs in once with their own device. Each WhatsApp chat/group becomes a "portal" (one Matrix room), each WhatsApp
  contact a "ghost" user. Mapping: login(user) -> portal(chat) -> ghost(participant). Relay mode lets a bot account speak for non-logged-in users [K].
  Backfill, mute, block, polls bridged.
- COPY: **portal model**: one durable row per external chat, keyed (login, chat_jid); ghosts per participant; per-chat settings on the portal row. This is how one user
  sits in many groups: the group is the unit, not the user.
- AVOID: AGPL (copyleft over network use). Do not link or port code.
- Fit: data-model reference for `wa_group` and `wa_participant`.

### Slack Bolt and the Slack app model - docs.slack.dev
- Bolt: MIT [K]. Events API facts fetched: POST per event, ack 2xx in 3 s then process from a queue, retries 3 times (immediate, 1 min, 5 min),
  `event_id` globally unique (dedup key), `team_id` + `api_app_id` on every event, 30,000 deliveries/workspace/app/hour, auto-disable if >95% fail in 60 min.
  Scopes granted per install via OAuth. `app_mention` fires only when the bot is @mentioned. Slash commands and interactivity are separate request types [K].
- COPY: install record per workspace (token, scopes, team id). Ack-then-queue. Dedup on event id. Mention-only trigger by default. Channel membership = consent (bot must be invited).
  Scope list shown to the admin at install.
- Fit: the reference contract for the group @mention bot.

### Microsoft Bot Framework / Teams - [K, not fetched]
- Activity protocol: one JSON `Activity` shape for all channels. Adapter validates the JWT per tenant. Teams bots get `mention` entities and receive channel messages only when
  @mentioned unless RSC permission is granted. Conversation references are stored so the bot can message proactively. Admin consent per tenant; manifest lists scopes.
- COPY: **conversation reference** (stored address) for proactive messages. One normalised activity type across channels. Manifest with declared scopes.
- AVOID: Azure coupling.

### OpenClaw - github.com/openclaw/openclaw
- MIT. ~394k stars, 4.8k open issues. Personal assistant on your own machine; channels incl. WhatsApp, Slack, Discord, Telegram.
- Gateway = control plane for sessions, tools, events, channels. Unknown senders in DMs get a **pairing code** that the owner approves
  (`openclaw pairing approve <channel> <code>`). Inbound messages are untrusted. Skills/plugins via SDK and a registry (ClawHub). Sandboxing is configurable.
  WhatsApp library and group mention logic: UNVERIFIED (page truncated; likely Baileys [K]).
- COPY: pairing/approval of unknown senders. "Trusted gateway, untrusted execution, policy enforced by code". Treat inbound text as hostile.
- AVOID: single-user, host-level tool execution. Not multi-tenant.

### wa_llm - github.com/ilanbenb/wa_llm
- MIT (licence file looks odd). 174 stars, 318 commits. Python 3.13, FastAPI, Postgres + pgvector, Anthropic + Voyage, Docker. Uses a WhatsApp Web client over HTTP (QR link).
- Group bot: responds "when mentioned", stores history, knowledge base, group summaries. **Groups are enabled by an operator setting `group.managed = true`.**
  Users opt in/out of being tagged by DMing `opt-in`, `opt-out`, `status`.
- COPY: managed-group allowlist flag. Per-person opt-in/out of being tagged. Group history table for context.
- AVOID: SQL-only admin; unofficial client.

### OpenCaptain - github.com/CAKAI-DEV/OpenCaptain
- MIT, 0 stars (new/small), 202 commits. Bun, Hono, Postgres + Drizzle + pgvector, BullMQ + Redis, JWT. Webhook `/api/v1/webhooks/whatsapp` with verify and access token (Cloud API style).
- PM agent over WhatsApp/Telegram. Tasks via chat, AI auto-detects tasks; "AI suggests, humans confirm"; role chain Creator > Squad Lead > Op Lead > PM; Linear sync.
- COPY: suggest-then-confirm for task creation. Closest concept to Metorite.
- AVOID: low maturity; multi-tenancy not stated.

### papai - github.com/wkich/papai
- Business Source License 1.1. 9 stars. Telegram/Mattermost/Discord, not WhatsApp.
- Tools gated by capability and context; plugin tools named `plugin_<id>__<tool>`; per-group `/config` opened by a group admin in DM; authorised members list; reply-to-bot counts as a mention.
- COPY: **per-group config by an admin in a private UI**, authorised-member list, "reply to the bot = mention", namespaced plugin tools.

### Others seen
- Todoist-meeting-manager-agent (kaymen99): meeting notes to tasks, not a live bot. Idea: extract actions from meeting text.
- Wassenger, Kapso, Unipile, Gupshup: commercial. The Kapso blog is the best neutral summary of the Groups API state.
  Gupshup has a Groups API (Beta) doc page (seen in search, not read).

## 2. How they handle the asked topics

| Topic | What the sources do |
|---|---|
| @mention in groups | Slack: `app_mention`. Teams: `mention` entity. Baileys/wwebjs: `mentionedJid` list [K]. wa_llm: "when mentioned". papai: reply-to-bot counts. Cloud API: UNVERIFIED. |
| Per-group config / allowlist | wa_llm `managed` flag. papai admin `/config` + member list. Slack: bot must be invited. OpenClaw: pairing approval. |
| Opt-in / consent | wa_llm tag opt-in/out. OpenClaw pairing. Cloud API: customer opt-in for templates is Meta policy; group join is by invite link (consent by design). |
| Dedup | Slack `event_id`. Meta messages carry `wamid` [K]. Store UNIQUE (tenant, provider, event_id) first, then enqueue. |
| Human handoff | Chatwoot pending/open flag. |
| Rate limiting | Slack 30k/h/workspace/app, auto-disable at >95% fail. Meta: per-number throughput + messaging tiers [K]. |
| Template management | Chatwoot syncs Meta templates. Groups need their own templates (no analytics for group templates). |
| Skills / tools | Botpress typed integration definition. papai capability-gated, namespaced tools. OpenClaw plugins/skills with sandbox. |

## 3. Comparison table

| Project | Licence | WhatsApp path | Multi-tenant | Queue | Bot model | Fit |
|---|---|---|---|---|---|---|
| Chatwoot | MIT + enterprise dir | Cloud API | Account | Sidekiq [K] | AgentBot webhook | High (support, handoff) |
| Evolution API | Apache-2.0 + brand terms | Baileys or Cloud | Instance | RabbitMQ/SQS/NATS/Kafka | Webhook + integrations | Medium (driver idea) |
| Baileys | MIT | Unofficial | No (library) | none | none | Low |
| whatsmeow | MPL-2.0 | Unofficial | No | none | none | Low |
| whatsapp-web.js | Apache-2.0 | Unofficial (Chrome) | No | none | none | Low |
| WPPConnect | Apache-2.0 | Unofficial (Chrome) | Sessions | none | Chatwoot bridge | Low |
| Typebot | FSL or AGPL (unclear) | Cloud API [K] | Workspace | none | Flow blocks | Idea only |
| Botpress | MIT | via integration | Workspace | n/a | Typed integration | Medium (manifest) |
| n8n | Fair-code | Cloud API nodes | No | Redis workers [K] | Workflow | Do not embed |
| Rasa | Apache-2.0 | none native | No | n/a | CALM flows | Idea only |
| mautrix-whatsapp | AGPL-3.0 | Unofficial | Per-user login | n/a | Portals/ghosts | Data model idea |
| Slack Bolt/Events | MIT [K] | n/a | Per-workspace install | ack then queue | app_mention, scopes | High (contract) |
| Bot Framework/Teams | MIT [K] | n/a | Per-tenant | n/a | Activity + conv. reference | Medium |
| OpenClaw | MIT | unverified | Single user | n/a | Skills + pairing | Medium (security) |
| wa_llm | MIT | Unofficial | No | none | Mention bot, managed groups | Medium |
| OpenCaptain | MIT | Cloud-style webhook | Unclear | BullMQ | Suggest + confirm | Medium |

## 4. Reference architecture for Metorite

### 4.1 Components
1. **Edge webhook** (one public endpoint per provider): verify Meta signature (X-Hub-Signature-256), resolve tenant from `phone_number_id` or WABA id, insert into `wa_inbound_event`
   with UNIQUE (provider, event_id) for dedup, return 200 fast, enqueue.
2. **Queue + workers** (existing Redis through the prefix wrapper): per-tenant concurrency cap and per-number outbound throttle.
3. **Channel driver interface**: `receive()`, `send_text()`, `send_template()`, `create_group()`, `invite_link()`. Driver 1 = Cloud API (build now). Driver 2 = linked-device sidecar (parked, flag off, owner-gated).
4. **Router/dispatcher**: normalises to one `Activity` (tenant, channel, conversation, sender, text, mentions[], reply_to). Picks the bot by binding.
5. **Bot runtime**: bot = admin config (persona, allowed tools, knowledge scope, mode) + skills. All model calls go through the existing model router. No provider keys.
6. **Skill/tool manifest** (Botpress style): name, JSON schema, scope, `needs_approval` flag. Tools write to the existing task store through the same service layer as chat.
7. **Approval service**: write tools produce a draft card. A member approves in Metorite (or by reply in 1:1). Maps to "approvals by member".
8. **Handoff inbox**: pending/open flag per conversation (Chatwoot). Human reply goes out through the same driver.
9. **Template service**: sync templates from Meta, store status/category, per-tenant approval of use, send with variable binding.
10. **Scheduler**: reminders and meeting polls as jobs, always ending in a template or an in-window message.

### 4.2 Data model sketch (every row carries organization_id, FORCE RLS)
- `wa_number`(id, org, waba_id, phone_number_id, display_name, oba_status, quality_rating, token_ref, driver)
- `wa_bot`(id, org, name, kind[support|group|scheduler], persona, model_tier, tools[], knowledge_scope, enabled, created_by)
- `wa_binding`(id, org, bot_id, number_id, scope[number|group], group_id NULL, trigger[mention|always|keyword])
- `wa_group`(id, org, number_id, group_jid, invite_link, title, managed bool, status, member_count) - portal row (mautrix)
- `wa_participant`(id, org, group_id, wa_id, member_email NULL, role, tag_opt_in bool, joined_at) - ghost row; link to a Metorite member only after verification
- `wa_contact`(id, org, wa_id, name, opt_in_source, opt_in_at, opt_out_at, last_inbound_at) - 24h window = last_inbound_at
- `wa_conversation`(id, org, contact_id|group_id, bot_id, status[pending|open|resolved], assignee_email)
- `wa_message`(id, org, conversation_id, direction, provider_msg_id UNIQUE per org, type, body, template_id, status, mentions jsonb, created_at)
- `wa_inbound_event`(org, provider, event_id, received_at, processed_at) UNIQUE(provider, event_id)
- `wa_template`(id, org, name, language, category, status, components jsonb, meta_id)
- `wa_action_draft`(id, org, bot_id, conversation_id, tool, args jsonb, proposed_by_wa_id, approver_email, state, task_id)
- `wa_audit`(org, actor, act, subject, at) for consent, handoff, tool calls

### 4.3 Flows
**Inbound customer message (1:1, official).** Meta webhook -> verify signature -> map phone_number_id to org -> dedup insert -> enqueue -> worker loads
conversation (status `pending` = bot) -> update `last_inbound_at` -> bot runs with scoped knowledge -> reply in window -> if low confidence, the user asks
for a person, or a policy word hits: status `open`, notify assignees, bot goes silent.

**Group @mention.** Official: small API-created group (<=8) joined by invite link; webhook group message -> check `wa_group.managed` and binding trigger=mention
-> detect mention of the business number (field shape UNVERIFIED) or reply-to-bot -> resolve sender wa_id to member via `wa_participant` (unverified senders get read-only answers, no writes) ->
LLM picks tool -> read tools answer directly; write tools (create task, post update) make a `wa_action_draft` and reply "Draft: X. Reply 1 to confirm", or approve in Metorite ->
on approval write task, reply with link. Dedup on wamid. Group admin config lives in Metorite UI, never in the group.
Large existing office group: not reachable officially. Offer (a) a small Metorite group per team, or (b) the parked linked-device lane with explicit consent, risk notice, and owner gate.

**Scheduled meeting coordination.** Organiser asks in chat or group -> tool `propose_meeting(slots, attendees)` -> draft approval -> poll sent to each attendee 1:1 (template if outside window, or group message) ->
replies collected as availability rows -> scheduler job at the deadline picks the slot -> calendar write through the Calendar lens -> confirmation message and reminder jobs (template).

**Human handoff.** Trigger (user ask, low confidence, sentiment, tool failure) -> status `open`, bot muted for that conversation, summary posted as an internal note ->
agent replies from Metorite inbox through the driver -> "return to bot" sets `pending`. Timeout re-queue if no agent answers within SLA.

**Outbound proactive message with template.** Needs `wa_contact.opt_in_at` and no `opt_out_at` -> if now - last_inbound_at < 24h, free text allowed; else pick an approved `wa_template`
-> bind variables -> rate-limit per number and per tier -> send -> status webhooks update `wa_message` -> failures pause the campaign and alert the admin. STOP keyword sets opt-out.

### 4.4 Guardrails (tie to repo rules)
- R5: tenant from number/WABA mapping only, never from payload text. Redis keys via wrapper. One DB seam.
- R6: new tables expand-only. Ship dark behind a flag (name to choose, e.g. WA_BOTS_ORGS).
- Money and third parties: no real sends outside dev tenants without owner OK (CLAUDE.md 3a rule 3).
- Treat all inbound text as untrusted (OpenClaw). Validate tool args by schema. Outbound webhook URLs on an allowlist (WPPConnect).
- Cost: each group message is billed per delivered recipient. Put a credit meter on it.

## 5. Unverified list
- Mention delivery in Cloud API group webhooks (payload shape).
- Whether the Cloud API can read an existing group the business did not create (docs and a community thread say API-created groups only).
- Last commit dates of all repos except mautrix-whatsapp (v26.10, 16 Oct, year inferred).
- Typebot real licence (FSL vs AGPL). Chatwoot enterprise-folder licence terms.
- OpenClaw WhatsApp library. Teams, Bolt, n8n node details, Rasa channels, Baileys/wwebjs internals: prior knowledge.
- Meta pricing numbers and tier limits not fetched.

## Sources
- https://github.com/chatwoot/chatwoot , https://github.com/EvolutionAPI/evolution-api , https://github.com/WhiskeySockets/Baileys , https://github.com/tulir/whatsmeow
- https://github.com/pedroslopez/whatsapp-web.js , https://github.com/wppconnect-team/wppconnect-server , https://github.com/baptisteArno/typebot.io , https://github.com/botpress/botpress
- https://github.com/n8n-io/n8n , https://github.com/RasaHQ/rasa , https://github.com/mautrix/whatsapp , https://github.com/openclaw/openclaw
- https://github.com/ilanbenb/wa_llm , https://github.com/CAKAI-DEV/OpenCaptain , https://github.com/wkich/papai
- https://docs.slack.dev/apis/events-api/ , https://www.chatwoot.com/hc/user-guide/articles/1677497472-how-to-use-agent-bots
- https://developers.facebook.com/docs/whatsapp/cloud-api/groups , https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/faq/
- https://kapso.com/blog/whatsapp-groups-api-state-2026 , https://developers.facebook.com/community/threads/1084199161066371/
