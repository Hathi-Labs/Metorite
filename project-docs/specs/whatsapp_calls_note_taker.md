# WhatsApp Calls → Note Taker — feasibility study & UX design

> ⚠️ **DEFERRED — FUTURE SCOPE (2026-08-10 consolidation, D26).** No work dispatches from
> this document; no board row exists. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


> **Product:** Metorite · **Feature:** extend the AI Note Taker (`/notes`) to WhatsApp voice calls, including group calls
> **Created:** 2026-08-01 · **Updated:** 2026-08-02 · **Status:** 🔬 feasibility study (§0–§11, the design record) · ✅ **Surface C SHIPPED + DEPLOYED** — see §12. Place and answer 1:1 and group WhatsApp calls from `/whatsapp/calls` or a chat's Call button, **speak and listen** through the browser, and every call is recorded server-side. **Transcription is not wired yet** (§12.5) — the recording is produced but nothing consumes it.
> **Siblings:** [`note_taker_app.md`](note_taker_app.md) (the note taker we're extending) · [`meeting_bot_platform_plan.md`](meeting_bot_platform_plan.md) (the bot-joins-a-call pattern) · [`whatsapp_message_manager.md`](whatsapp_message_manager.md) (the WhatsApp vertical we'd hang this off)
> **Touches:** `apps/services/meeting_bot/` · `apps/services/whatsapp_bridge/` (Go + whatsmeow) · `gateway/routes/notes/meeting_bot.py` · `gateway/routes/whatsapp/`

---

## 0. Verdict

**Yes — with one large asterisk, and the asterisk decides the whole design.**

| Question | Answer |
|---|---|
| Can a note taker capture **1:1** WhatsApp calls? | **Yes, officially and supportedly** — but only calls to/from a *business* number on the WhatsApp Business Calling API. |
| Can a note taker capture **group** WhatsApp calls? | **Not through any official Meta API.** Meta's Calling API explicitly does not support group voice or video calls. |
| Can it capture group calls *some other way*? | **Yes, technically** — via an unofficial protocol client (`whatsmeow` + `meowcaller`), the same family of library our `whatsapp_bridge` already runs. Group support in that library is marked **experimental**, and this route **violates WhatsApp's ToS and risks a number ban**. |
| Can we passively record a call we aren't in? | **No.** WhatsApp calls are end-to-end encrypted. The only way to get audio is to **be an endpoint** — a participant, or the device. There is no tap, no server-side copy, no "give me the recording" API. |

**That last row is the whole story.** It means the note taker cannot be a passive observer the way a Zoom cloud recording is. It has to *join the call as a participant* — which is exactly the model our Google Meet bot already uses, and which the rest of our pipeline is already built around. The pipeline (transcribe → diarize → name speakers → summarize → action items → dispatch) is **entirely platform-agnostic and reusable unchanged**. Only the *join and capture* layer is new.

**Recommended path (detail in §6):**

1. **Phase A — official, zero-risk, real value:** note-take **1:1 calls on our Cloud API business number** via the WhatsApp Business Calling API's WebRTC/SIP media leg. Ships without touching any grey area. Covers customer/dealer/vendor calls, which is where the business value actually is for Fracktal.
2. **Phase B — the group-call unlock, opt-in and risk-flagged:** a **"notetaker as a participant" bot** on the whatsmeow bridge — a dedicated, disposable number you *add to the call like a person*. Gated behind the same explicit ToS warning the bridge already carries, and never on the company's primary number.
3. **Phase C — the no-protocol fallback:** device-side capture (record on the phone/desktop, upload). Zero ban risk, zero automation, already 90% built (`/notes` upload path). Worth shipping as the honest escape hatch regardless of A and B.

---

## 1. What WhatsApp actually offers (the four surfaces)

There are exactly four ways audio from a WhatsApp call can legitimately reach a server. They have wildly different capabilities and risk.

### Surface A — WhatsApp Business Calling API (Cloud API) · **official**

Meta shipped voice calling on the WhatsApp Business Platform in 2025. A business phone number on the Cloud API can place and receive **VoIP voice calls** with WhatsApp users, in the same thread as the chat.

What matters for us — **you terminate the media yourself**:

- Call setup is an **SDP offer/answer handshake**. Meta sends your webhook an SDP offer describing the media session; your server returns an SDP answer. The offer is standard WebRTC: **ICE, DTLS-SRTP, OPUS**.
- Alternatively you can run **SIP signalling** with default WebRTC media.
- Because *your* media server is one leg of the call, **you have the raw decoded audio**. That is the note taker's whole requirement.

Constraints, all confirmed:

| Constraint | Detail |
|---|---|
| **No group calls** | The API does not support voice or video calls in groups. One-to-one only. |
| **No video** | Voice only today; video is signposted as future, not available. |
| **Business number only** | Requires the WhatsApp Business Platform (Cloud API) and `whatsapp_business_messaging` permission — not the WhatsApp Business *app*, and never a personal number. |
| **Consent for outbound** | A user must accept an explicit **call permission request** before the business may ring them; the permission expires. User-initiated calls (customer taps call) need no permission. |
| **Region blocks on business-initiated calls** | Blocked in the USA, Canada, Egypt, Vietnam and Nigeria. India — our actual market — is fine. |
| **Cost** | Outbound billed per minute in 6-second increments, only on answer, volume-tiered by country. Inbound (user-initiated) is free. |
| **Meta gives you no recording** | The API itself exposes no recording or transcript. That's not a blocker — *we* are the media endpoint, so we record our own leg. BSPs (Wati, etc.) already sell recording + transcription + summaries built exactly this way, which is proof the pattern is sanctioned. |
| **Availability** | Rolling out through Cloud API and enterprise BSPs; needs enabling on the WABA. |

**Read:** this is a fully supported, documented way to build a WhatsApp note taker — for 1:1 business calls, and only those.

### Surface B — WhatsApp Web calling (browser) · **grey**

As of **July 2026** WhatsApp shipped **calling on WhatsApp Web** — one-to-one *and* **group** voice/video calls in a plain browser tab, no desktop app. Alongside it: **call links with a "require approval to join" waiting room**, **call transfer between devices**, screen sharing, noise suppression.

This is structurally interesting because it makes WhatsApp look, for the first time, like Google Meet: *a group call, in a browser, joinable from a link, with a waiting room*. Our `meeting_bot` worker is already **real Chrome under Playwright with a PulseAudio null sink and ffmpeg** — the exact machinery needed. Pointing it at WhatsApp Web instead of Meet is a small architectural step.

Why it is not the recommended path anyway:

- **Automating a WhatsApp client violates WhatsApp's Terms of Service**, the same reason our `whatsapp_bridge` carries a ban warning. A browser-driven account is an unauthorised client.
- It requires a **real WhatsApp account paired to a phone** in the container, with all the session-fragility that implies.
- Call links require every invitee to have a WhatsApp account, and historically opening a call link on the web app punted you to your phone. Web group calling is *newly* rolled out and still gradual — so the DOM, the flows and the capabilities are a moving target, and DOM automation is the most brittle thing we own (our Meet bot's join flow is already the highest-maintenance code in the repo).
- Compared to Surface C it buys nothing: same ToS exposure, worse reliability, worse audio path.

**Read:** a real option, and the reason group calls are newly *conceivable* at all — but strictly worse than Surface C for the same risk.

### Surface C — whatsmeow + meowcaller (protocol client) · **grey, and the most capable**

We already run **`apps/services/whatsapp_bridge`** — Go, `go.mau.fi/whatsmeow`, QR-paired personal number, holding a live WhatsApp multi-device session. It handles messages today and **ignores calls entirely** (`grep -i call *.go` finds nothing call-related).

whatsmeow itself gives **call signalling only** — `events.CallOffer`, `CallOfferNotice` (with a `Media: audio|video` field, primarily for group calls), `CallTerminate`. A 2023 request to add media/answer support was **closed as not planned**. So whatsmeow alone can tell you a call is happening and who from — useful on its own — but cannot hear it.

The gap is closed by **[`purpshell/meowcaller`](https://github.com/purpshell/meowcaller)** — a pure-Go WhatsApp VoIP library that sits on top of stock upstream whatsmeow (no fork), MIT licensed, ~226 stars / 217 commits:

- Implements WhatsApp's proprietary **MLOW audio codec entirely in Go**, no CGO, no C bindings.
- Places outbound calls, receives inbound calls, answers them.
- **Raw PCM in and out** via a source/sink API — `call.Receive(meowcaller.SinkFunc(func(pcm []float32) { … }))`. That is literally the note taker's input.
- Ships `WAVRecorder` / `MP3File` helpers — recording is a first-class use case.
- **Experimental group calling:** ad-hoc and group-bound group calls, add/ring participants, **reusable call links and approval waiting rooms**.
- Known gaps: Opus fallback for non-MLOW peers is in progress; scheduled-call events unimplemented.

(A second project, `JotaDev66/WaCalls`, does the same on whatsmeow + pion/webrtc with a vendored MLow, reports stable bidirectional 1:1 audio at 16 kHz PCM — but is explicitly **1:1 only, no group calls**. It's corroboration that the approach works, not a better option for us.)

Risk, stated plainly: **this is against WhatsApp's ToS and the number can be banned at any time.** Our bridge README already says exactly this. Group support is *experimental*, i.e. expect breakage.

**Read:** the only path to **group** call note-taking, and it lands on infrastructure we already run in a language we already ship. Also the highest-risk path.

### Surface D — device-side capture · **no protocol involvement**

Record the call on the endpoint you already control — phone recorder, desktop audio capture, a second device in the room — then upload. Our `/notes` upload path runs the identical transcribe → notes → actions pipeline. Zero ToS exposure, zero automation, entirely manual, and legally the most exposed on the *recording-consent* axis (no announcement, nobody knows).

**Read:** the honest fallback. Cheap to surface, worth having.

---

## 2. What is definitively NOT possible

Stating these so nobody re-litigates them later:

1. **No passive/server-side recording.** WhatsApp calls are E2EE. Meta does not hold plaintext and does not offer a recording API on the consumer side. Any design that assumes "WhatsApp gives us the audio" is dead on arrival.
2. **No bot participant in a group call via any official API.** Meta's Calling API is 1:1 by construction. A Cloud API business number cannot be added to a consumer group call.
3. **No retroactive capture.** If the call already happened and nothing was in it, there is nothing to transcribe. (This drives a real UX requirement — §7.6.)
4. **No video note-taking**, on any surface, for business numbers.
5. **No "just add our Cloud API number to the group"** — business numbers and consumer group calls are different worlds.

---

## 3. Feasibility matrix

| Scenario | Surface | Feasible? | Risk | Effort |
|---|---|---|---|---|
| Customer calls our business number, wants notes | **A** (Cloud API) | ✅ Yes | None — supported | M |
| We call a customer from the business number | **A** | ✅ Yes (needs call permission accepted) | None | M |
| Founder's personal 1:1 WhatsApp call | **C** (meowcaller) | ✅ Yes | ToS / ban | M–L |
| **WhatsApp group call** (team standup, dealer group) | **C** | ⚠️ Yes — experimental | ToS / ban + instability | L |
| WhatsApp group call | **B** (Web + Playwright) | ⚠️ Yes in principle | ToS / ban + DOM brittleness | L–XL |
| WhatsApp group call | **A** | ❌ **No** | — | — |
| Any WhatsApp call, no automation | **D** (upload) | ✅ Yes | Consent only | **XS — mostly built** |

---

## 4. What we already have (the reuse story is very good)

This is the strongest argument for doing it: **almost none of the hard part is new.**

| Need | Already exists | Where |
|---|---|---|
| Transcription (AssemblyAI native, Hinglish code-switching, `word_boost` glossary) | ✅ | `packages/acb_stt/` |
| Diarization (native + sherpa-onnx local fallback) | ✅ | `acb_stt/local_diarization.py` |
| Speaker naming (live voiceprints + LLM self-intro inference) | ✅ | `routes/notes/speaker_id.py`, `live_speakers.py` |
| Summarization, templates, action items, grounding | ✅ | `routes/notes/summaries.py`, `templates.py` |
| Action dispatch → tasks / email / documents | ✅ | `routes/notes/dispatch.py` |
| Live streaming ASR + SSE live transcript + live copilot | ✅ | `meeting_bot/app/live.py`, `routes/notes/live*.py` |
| **Speak into the call** (TTS → virtual mic) — needed for the audible consent announcement | ✅ | `meeting_bot/app/main.py` `/bots/{id}/say`, `TTS_CMD` |
| Bot lifecycle: `requested → joining → waiting_room → in_call → processing → done` | ✅ | `routes/notes/meeting_bot.py` |
| Provider abstraction for "something that joins calls and returns audio" | ✅ | `SelfHostedProvider` / `RecallProvider` in `meeting_bot.py:225–296` |
| Bot dispatch UI, active-bot list, stop, diagnostics | ✅ | `notes/components/JoinCallModal.tsx`, `ActiveBots.tsx` |
| A live whatsmeow session on a paired number | ✅ | `apps/services/whatsapp_bridge/` |
| WhatsApp contacts, chats, groups, avatars, send | ✅ | `routes/whatsapp/`, migrations 102–111, 128 |
| WhatsApp voice-note transcription (same STT tier!) | ✅ | `routes/whatsapp/automation/transcription.py` |

**What's genuinely missing:** a media leg for Surface A, a call layer on the bridge for Surface C, and the UX to arm/consent/join. That's it.

**One structural bonus WhatsApp gives us that Meet does not:** every participant is a **phone number we already have a contact record for**. If per-participant audio is separable (§9, open question), diarization stops being clustering-guesswork and becomes an exact join against `wa_contacts` — **named speakers, correct, for free**. That is a materially better transcript than our Meet bot produces today.

**Also worth noting:** `whatsapp_message_manager.md` records a founder decision (v2, 2026-07-23) of "**official WhatsApp Business Platform only; unofficial linked-device routes dropped**" — which the shipped `whatsapp_bridge` subsequently reversed. Phase B re-opens that same question for calls, and should be decided explicitly, not inherited.

---

## 5. The hard part isn't the audio — it's the trigger

For Google Meet the note taker has a link and a calendar. **WhatsApp calls have neither.** They are spontaneous, initiated from a phone, with no URL, no invite, no scheduled start. So the central design problem is:

> **How does the note taker find out a call is happening, in time to be in it?**

Four answers, in order of how good they are:

| Trigger | How it works | Verdict |
|---|---|---|
| **Add it to the call** | The notetaker is a WhatsApp contact that's always online. You tap "add participant" mid-call. It answers in <1s. | ⭐ **Primary.** Zero pre-arming, matches how people already add someone to a call, works for 1:1 (which becomes a 3-party call) *and* groups. |
| **Call permission / auto-answer** (Surface A) | Any call on the business number is *inherently* note-taken, because we're the endpoint. Nothing to trigger. | ⭐ **Primary for Phase A.** The best UX is no UX. |
| **Call link with waiting room** | Create a link from `/notes`; notetaker joins and waits for approval. | ✅ Good for *planned* calls. Requires meowcaller's experimental link support. |
| **Arm ahead of time** ("record the next call in this chat") | `CallOffer` event fires on the bridge → notetaker auto-joins/rings itself in. | ✅ Good for known-imminent calls; useless for surprises. |

**Design consequence:** the note taker must be a **standing, always-available identity** — a contact in your address book named something like *"Metorite Notes"* — not a thing you dispatch per-meeting. That is a genuinely different mental model from `JoinCallModal`'s paste-a-link, and the UX below is built around it.

---

## 6. Recommended plan

### Phase A — Business-number 1:1 note taking (official, ship first)

Add a **WhatsApp calling media leg** as a new `meeting_bot` provider sibling — a small service that terminates the Cloud API SDP offer (WebRTC, OPUS/DTLS-SRTP), records its leg to 16 kHz mono, and hands off to the *existing* ingest path (`_ingest_recording` → `run_transcription`). Reuse `live.py`'s tee for live captions.

Why first: no ToS exposure, no ban risk, ships on the WABA we already have, and for a hardware company the calls that matter commercially (customers, dealers, vendors, service escalations) are exactly the 1:1 business calls this covers. It also proves the media→pipeline seam with a stable, documented counterparty before we bet on an experimental library.

Blocking dependency: **Calling must be enabled on our WABA** (rollout is gated). Verify before committing engineering time.

### Phase B — The participant bot, for groups (opt-in, risk-flagged)

Extend `whatsapp_bridge` (Go, already whatsmeow) with **meowcaller**: answer `CallOffer` automatically when armed, expose the PCM sink, POST audio to the gateway on the existing `X-Bridge-Secret` channel, and drive the same `meeting_bot` lifecycle statuses so `ActiveBots` and the live dock work unchanged.

Gate it exactly like the bridge is gated: **off by default**, its own env flag, its own explicit ToS warning in the UI, and a hard recommendation to pair a **dedicated disposable number**, never the company's primary line.

Start with 1:1 (production-ready in meowcaller), then group (experimental) behind a second flag.

### Phase C — Upload fallback (do this cheaply, immediately)

Surface "record it on your phone and drop the file here" in the WhatsApp UI. It's the existing `/notes` upload path with a WhatsApp-shaped entry point. It costs almost nothing and it's the only thing that works on day zero.

---

## 7. UI/UX design

The rest of this doc answers the second half of the ask: *how does a user actually get a note taker into a WhatsApp call?*

### 7.1 The core metaphor: **the notetaker is a contact, not a button**

Everything follows from §5. In `/notes` today you paste a link and dispatch a bot. For WhatsApp you instead **set up a notetaker identity once**, and thereafter **add it to calls like a person**. It appears in the participant list with a name and an avatar, so nobody is being recorded by an invisible thing.

```
  SET UP ONCE                          THEN, ON EVERY CALL
  ───────────                          ───────────────────
  /notes → Settings → WhatsApp    →    you're on a call
  pair the notetaker number (QR)  →    tap "Add participant"
  name it, give it an avatar      →    pick "Metorite Notes"
  choose auto-join rules          →    it answers, announces itself,
                                       and the live transcript
                                       appears in /notes
```

### 7.2 Setup screen — `/notes` → Settings → **WhatsApp calls**

A new tab in the existing `NotesSettingsModal`, mirroring `BotIdentitySection.tsx` (which already does exactly this shape for the bot's Google account):

- **Which number takes notes.** Two cards, side by side, honest about the trade:
  - **Business number** *(Recommended · Official)* — "Notes on calls to and from your Cloud API number. 1:1 only — WhatsApp doesn't allow group calls on business numbers." Status chip: `Calling enabled on WABA` / `Not enabled — ask Meta`.
  - **Personal notetaker number** *(Group calls · Unofficial)* — QR pairing, reusing the bridge's existing `POST /session` → `{status, qr}` flow and its `<img>` renderer. Carries the same warning the bridge README carries, verbatim in tone: *"This uses an unofficial WhatsApp client. The number can be banned at any time. Use a dedicated number you're willing to lose — never your main line."* Behind an "I understand" checkbox before the QR renders.
- **Identity.** Display name (default *"Metorite Notes"*) + avatar. This is what everyone sees in the participant list — it is the consent surface, so it's mandatory and not editable to something misleading.
- **Auto-join rules** (§7.4).
- **Announcement** (§7.5).

### 7.3 The in-call join — what the user actually does

**Group call (Surface C):** the user taps *Add participant* in WhatsApp's own call UI and picks the notetaker. Nothing in our app. That's the point — **the best interaction is the one WhatsApp already taught them.** Within a second:

1. The notetaker answers.
2. It speaks the announcement (§7.5) into the call via the existing TTS→virtual-mic path.
3. It posts a message into the chat: *"📝 Taking notes on this call — @Vijay started it. Notes land in Metorite when the call ends."*
4. `/notes` opens a live meeting and the **existing `LiveDock`** lights up.

**1:1 call:** identical — adding a third participant converts it to a group call. Worth saying out loud in the UI, because users will assume 1:1 can't be joined.

**Business number (Surface A):** nothing to do at all. The call *is* the note taker. The UI's only job is to say so clearly and let you turn it off per-contact.

### 7.4 Auto-join rules — the "ambient" mode

A rules list in the same settings tab, deliberately narrow:

| Rule | Behaviour |
|---|---|
| **Ask me every time** *(default)* | On `CallOffer`, push a notification / control-plane toast: *"Nikhil is calling the Dealers group — send the notetaker?"* with **Join** / **Not this one**. One tap. |
| **Always take notes** — per chat / per group / per contact | Notetaker rings itself in automatically. Best for a recurring standup group. |
| **Never** | Blocklist. Family groups, personal contacts. Must be as easy to reach as "always". |
| **Just this next call** | Arms once, from the chat header, then reverts. The "we're about to hop on a call" case. |

The rule editor should hang off the WhatsApp app's existing chat list (a **"Take notes on calls"** toggle in each chat's overflow menu), not be buried in `/notes` settings only — that's where the user is when they think of it.

### 7.5 Consent — non-negotiable, and it's UX not legalese

Recording other people is regulated (India: one-party consent; several jurisdictions: all-party). `JoinCallModal` already tells the truth about this today — keep that discipline and go further, because WhatsApp is a *personal* channel and the expectation of privacy is higher.

Three layers, all on by default, none silently disableable:

1. **Visible identity** — the notetaker's name and avatar in the participant list. Can't be hidden.
2. **Audible announcement** on join — TTS into the call: *"Metorite is taking notes on this call for Vijay."* Configurable text, language-aware (Hindi/English), but **cannot be set to silence** without an explicit admin override that is logged.
3. **A message in the chat thread** when recording starts and when notes are ready, so there's a durable record every participant can see.

Plus: an **"I object" affordance.** Anyone can reply `STOP` in the chat and the notetaker leaves the call and discards the recording. This is cheap to build (we already parse inbound messages) and it's the difference between a tool people tolerate and one they resent.

Explicitly rejected: any "record without announcement" mode. Products advertising that exist; we're not one.

### 7.6 The "I forgot" case

The single most common failure will be *the call ended and nobody added the notetaker*. There is no retroactive capture (§2.3). So the UI must fail usefully:

- The WhatsApp chat shows a call-ended event → offer an inline chip: **"Missed taking notes on this call. Turn on auto-notes for this chat?"** — turning a failure into a rule.
- Offer **"Upload a recording"** (Phase C) right there if they happened to record on the phone.
- Never show an empty meeting for a call we didn't capture.

### 7.7 During and after — reuse everything

The live and post-call surfaces need **no new design**: a WhatsApp call becomes a `meeting` row with `platform = 'whatsapp'`, and inherits the entire existing workspace — `LiveDock`, live captions, live copilot, the Summary/Transcript/Actions/Ask tabbed detail, timecode provenance, action-item HITL triage, follow-up drafting.

Three WhatsApp-specific additions:

1. **Speakers are already named.** Participants are phone numbers joined to `wa_contacts` — so the transcript shows *"Nikhil Sharma"*, not *"S2"*, with no rename step. Best-in-class, and only possible on this platform.
2. **Notes go back where the conversation lives.** A **"Send notes to the group"** action posting the summary into the same WhatsApp thread, through the existing send path. On WhatsApp, delivering meeting notes by email is the wrong channel.
3. **A `platform: whatsapp` badge** across the notes library, plus the group/chat name as the meeting title by default.

### 7.8 Surfaces to touch (concrete)

| File / area | Change |
|---|---|
| `notes/components/NotesSettingsModal.tsx` | New **WhatsApp calls** tab (§7.2) |
| `notes/components/BotIdentitySection.tsx` | Precedent to mirror for notetaker identity + pairing |
| `notes/components/JoinCallModal.tsx` | Add a WhatsApp mode — but **not** link-paste; a "how to add the notetaker" explainer + arm-next-call |
| `notes/components/ActiveBots.tsx` | Show WhatsApp calls in the active list (statuses already match) |
| WhatsApp chat list / chat header | Per-chat **"Take notes on calls"** toggle; missed-call chip (§7.6) |
| `routes/notes/meeting_bot.py` | `detect_platform` → `whatsapp`; new provider; drop `SELFHOSTED_PLATFORMS` assumption |
| `apps/services/whatsapp_bridge/` | meowcaller integration, call events, PCM → gateway |
| Migration | `meeting.platform = 'whatsapp'`, notetaker identity + per-chat auto-join rules |

---

## 8. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| **Number ban** (Surfaces B/C) | High | Dedicated disposable number; off by default; explicit consent screen; never the primary line; Phase A carries the business-critical load so a ban degrades rather than breaks. |
| **meowcaller group support is experimental** | High | Ship 1:1 first; group behind its own flag; expect breakage; pin the version. |
| **MLOW codec / protocol churn** | Medium | Upstream tracks it; we pin and update deliberately. Phase A is immune. |
| **Recording-consent law** | High (legal, not technical) | §7.5's three layers on by default; per-jurisdiction defaults; the `STOP` affordance; no silent mode. |
| **WABA calling not yet enabled for us** | Medium | Verify with Meta/BSP *before* Phase A engineering. |
| **Founder decision conflict** (official-only, `whatsapp_message_manager.md` v2) | Medium | Re-decide explicitly for calls; don't inherit silently. |
| **Per-participant audio may not be separable** | Medium | Falls back to existing diarization + speaker-naming. Degrades quality, not function. |

---

## 9. Open questions (verify before building)

1. **Is calling enabled on our WABA?** Hard gate on Phase A. **Run `python3 scripts/check_whatsapp_calling.py`** on a box that has the real credentials — it reads `GET /{phone_number_id}/settings` and reports `calling.status`. A 401/403 means Meta hasn't switched it on for our number (rollout is per-number and not something we can engineer around). *Unverified as of 2026-08-01 — the study was written in an environment with no credentials.*

   **Zero-credential shortcut:** WhatsApp clients render the call icon in a business conversation and on the business chat profile *only* when calling is enabled, and hide it when disabled. So opening a chat with our business number on any phone answers the gate question in seconds. (Tapping it won't connect until the `calls` webhook field and a media endpoint exist — the icon reports provisioning, not readiness.)
2. ~~**Does meowcaller expose per-participant PCM in a group call, or a mixed downlink?**~~ **RESOLVED 2026-08-01 — see §10.** Per-participant PCM exists, keyed by participant JID. Remaining sub-question is only whether we upstream a hook or fork.
3. **Does the Cloud API SDP offer let us pick a codec/sample rate**, or must we transcode OPUS → 16 kHz for `acb_stt`?
4. **Can a Cloud API call be recorded under Meta's policy** as long as the caller is told? (BSPs do it, which suggests yes — confirm in writing.)
5. **How stable is a paired notetaker session under call load?** whatsmeow sessions drop; a bridge that reconnects mid-call is worse than one that never joined.
6. **Group call participant cap** (32 via call links) and whether a notetaker consumes a slot. It does — say so in the UI.
7. **Hindi/Hinglish on WhatsApp calls** — AssemblyAI was chosen partly for this; validate on real call-quality audio (8–16 kHz, lossy), which is materially harder than meeting audio.

---

## 10. Addendum (2026-08-01) — CC *is* the call client, and speakers are attributed not diarized

Two refinements from review that change the recommended shape. They supersede the framing in §7.1 for the personal/group path; §7's consent, rules and post-call design all still stand.

### 10.1 Collapse it: one call surface in CC, two transports underneath

Rather than "a notetaker contact you add to the call", the stronger model is **Metorite takes the call**. It unifies Phases A and B behind one UI:

| You answer on | Transport | Calls covered | Status |
|---|---|---|---|
| **Business number** | Cloud API Business Calling (SDP/WebRTC leg) | 1:1 only | official |
| **Personal number** | whatsmeow + meowcaller | **1:1 and group** | unofficial |

For Surface A this isn't a workaround — **terminating the media leg yourself *is* the documented architecture**, so "take the call in CC" is exactly what Meta expects. For Surface C, `JotaDev66/WaCalls` already ships this shape (a browser call UI over whatsmeow + pion), which is useful corroboration that a CC-hosted dialer is viable.

What it buys: no second number, no extra participant consuming one of the 32 slots, no "add the bot" step, and nothing *experimental* about joining — CC is simply a participant.

What it costs, honestly:
- **Behaviour change.** Calls must be taken in the browser, not on the phone. This is the main adoption risk and should be treated as the primary product question, not an implementation detail.
- **Consent regresses.** With no visible notetaker in the participant list, §7.5's audible announcement and chat-thread message stop being belt-and-braces and become the *only* disclosure. They must be non-optional on this path.
- Ban risk on the personal transport is unchanged.

Keep the "add a notetaker participant" model (§7.3) as the alternative for users who won't move their calls off the phone.

### 10.2 Group calls give attribution, not diarization

**Confirmed in `meowcaller` source.** The structural argument is decisive: E2EE means the server cannot decrypt, therefore cannot mix, therefore mixing happens client-side, therefore the client necessarily receives N separately-decodable streams.

`group_media_receive.go` decodes per participant, keyed by RTP SSRC, and returns PCM bundled with identity:

```go
type decodedParticipantAudio struct {
    ParticipantID string
    UserJID       types.JID   // the participant's phone number
    DeviceJID     types.JID
    SSRC          uint32
    Timestamp     uint32
    PCM           []float32
}
```

`participantReceiveRegistry.DecodeAudio(packet)` resolves the receiver by SSRC, unprotects via `pipe.UnprotectAudio()`, decodes, and returns the above. Mixing happens strictly afterwards in `group_audio_mixer.go` (`Add(participantID, pcm)` → `MixChunk()`).

**So this is not diarization — it is attribution.** No clustering, no embeddings, no `S1`/`S2` labels, no rename step. `UserJID` joins directly to `wa_contacts` and the transcript is correctly named by construction. This is strictly better than our Meet bot and better than the commercial notetakers, and it is the single strongest product argument for building this at all.

**Gap:** the public sink (`SinkFunc(func(pcm []float32))`) exposes only the *mix*; the per-participant types are unexported. Exposing them is a small, well-scoped change at the point `DecodeAudio` already returns — best done as an **upstream PR** (MIT, actively maintained, and `engine_hook_test.go` suggests a hook mechanism to hang it off) rather than a fork we carry.

**Cost decision — take it early.** Transcribing N streams separately is N× the STT bill. Default instead to: **transcribe the mix once**, and use the per-participant streams purely as a **speaker-activity timeline**, labelling each segment by whichever participant had energy at that timestamp. 1× cost, exact attribution, degrades gracefully under crosstalk. Reserve per-stream STT for calls where overlapping speech genuinely matters.

---

## 11. Sources

- [Cloud API Calling — Meta for Developers](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling)
- [Calling — WhatsApp Cloud API docs](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/)
- [SIP Configuration Guide — WhatsApp Business Calling](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/sip)
- [Calling API Pricing — Meta for Developers](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/pricing)
- [How to Integrate the WhatsApp Business Calling API with WebRTC — WebRTC.ventures](https://webrtc.ventures/2025/11/how-to-integrate-the-whatsapp-business-calling-api-with-webrtc-to-enable-customer-voice-calls/)
- [WhatsApp Business Calling API: Pricing, Use Cases & FAQs — respond.io](https://respond.io/whatsapp-business-calling-api)
- [Understanding WhatsApp Calling restrictions and guidelines — Wati](https://support.wati.io/en/articles/12546668-understanding-whatsapp-calling-restrictions-and-guidelines)
- [How to enable WhatsApp call recording, transcription, and summaries in Wati](https://support.wati.io/en/articles/14472037-how-to-enable-whatsapp-call-recording-transcription-and-summaries-in-wati)
- [Introducing Web Calling on WhatsApp, Plus More New Updates — WhatsApp Blog](https://blog.whatsapp.com/introducing-web-calling-on-whatsapp-plus-more-new-updates)
- [WhatsApp now lets you make calls using its web app — TechCrunch](https://techcrunch.com/2026/07/28/whatsapp-now-lets-you-make-calls-using-its-web-app/)
- [WhatsApp Adds Web Calling, Waiting Room, and Call Transfer to Group Calls — gHacks](https://www.ghacks.net/2026/07/29/whatsapp-adds-web-calling-waiting-room-and-call-transfer-features/)
- [WhatsApp Call Links (32 participants) — 9to5Google](https://9to5google.com/2022/10/20/whatsapp-call-links/)
- [purpshell/meowcaller — WhatsApp VoIP Go library for whatsmeow](https://github.com/purpshell/meowcaller)
- [JotaDev66/WaCalls — 1:1 WhatsApp calls via whatsmeow + pion/webrtc](https://github.com/JotaDev66/WaCalls)
- [whatsmeow — Calls support (closed as not planned)](https://github.com/WhiskeySockets/Baileys/issues/40) *(Baileys equivalent request; whatsmeow media support likewise absent upstream)*
- [whatsmeow events package — CallOffer / CallOfferNotice / CallTerminate](https://pkg.go.dev/go.mau.fi/whatsmeow/types/events)

---


## 12. Build log — calling, end to end (2026-08-01 → 08-02)

Surface C is built and deployed. A paired personal number places and answers
1:1 and group calls from `/whatsapp/calls`, you can **speak and listen** through
the browser, and every call is recorded server-side for the note taker.

### 12.1 What shipped

| Layer | Change |
|---|---|
| `whatsapp_bridge/calls.go` | meowcaller on the existing whatsmeow session: place 1:1 / group (by group id or ad-hoc), answer, reject, hangup; phase registry; per-call WAV recording with a frame counter and retention sweep; readiness diagnostics. |
| `whatsapp_bridge/audio_ws.go` | Duplex audio WebSocket — `wsSource` (browser mic → `Player`) and a non-blocking fan-out off the recording sink (peer audio → browser). |
| `whatsapp_bridge/session.go` | `meowcaller.NewClient(..., WithLogger(...))` attached in `newClient`, **before** `Connect`. |
| `whatsapp_bridge/main.go` | `POST /call`, `/call/{hangup,answer,reject}`, `GET /calls`, `/calls/diagnostics`, `/calls/recording`, `GET /call/audio` (WS). |
| `routes/whatsapp/transport/calls.py` | Authenticated proxy, ownership enforcement, recording passthrough, the bridge event seam. |
| `routes/whatsapp/transport/calls_audio.py` | Call-scoped HMAC token + the browser↔bridge audio WebSocket proxy. |
| `routes/whatsapp/core.py` | `provider` on the account model; **`ws_router`** (see §12.3). |
| `whatsapp/calls/page.tsx` + `lib/callAudio.ts` + `public/call-audio-worklets.js` | Dialer, Talk/mute/leave, jitter-buffered playback, readiness panel, recording player. |
| `whatsapp/page.tsx` | **Call** button in every chat header. |

### 12.2 The audio path

```
mic  ──► AudioWorklet ──► WSS ──► gateway ──► WS ──► bridge ──► Player ──► MLow/SRTP ──► peer
spkr ◄── AudioWorklet ◄── WSS ◄── gateway ◄── WS ◄── bridge ◄── callSink ◄── decoded frames
                                                          └──► WAV (note taker)
```

Wire format is **little-endian int16 mono, 16 kHz, one 960-sample (60 ms) frame
per binary message** — meowcaller's native framing, so nothing resamples or
reframes in the middle. The browser's `AudioContext` is opened at 16 kHz so the
resample happens once, in native code.

Load-bearing details, each of which was a bug or nearly one:

- **Uplink starves to silence, never `io.EOF`.** A `Player` that hits EOF stops,
  and a stopped Player is permanent silence for the rest of the call.
- **Not subscribing a Player is fine when nobody is listening.** meowcaller's
  send loop is *"frame-paced from connect, NOT gated on the Player"* — it sends
  silence so the relay learns our SSRC, and **the relay won't bridge the peer's
  media until it sees our stream**. Attaching a browser is therefore optional.
- **Downlink fan-out must not block.** It runs on meowcaller's media path; a
  slow socket drops frames rather than stalling the recorder too.
- **Playback needs a jitter buffer** (~180 ms, re-primes on underrun). Network
  frames don't arrive on the audio clock.
- **Echo cancellation is best-effort.** Browser AEC references what the browser
  renders, and this plays through WebAudio rather than a WebRTC peer connection.
  Headphones are the reliable fix; the UI says so.

### 12.3 Four bugs worth remembering

Each cost a production round-trip, and each is the kind that hides.

1. **A WebSocket cannot live on a feature-gated router.**
   `require_feature_router`'s check takes an HTTP `Request`, which FastAPI never
   populates for a WebSocket — so the dependency raises
   `TypeError: _check() missing 1 required positional argument: 'request'`
   *during the handshake*, before any handler and before any close code. Both
   audio directions failed with nothing logged. **Exempting the path does not
   help** (the dependency still runs to read the path). Hence `core.ws_router`,
   ungated, whose members authenticate themselves.

2. **Un-normalised phone numbers produce a JID WhatsApp silently never acks.**
   The dialer's own placeholder taught `+91 98765 43210`; meowcaller builds the
   offer's JID from that string verbatim. Surfaces only as an offer timeout with
   no error. Targets are now reduced to digits; JIDs pass through untouched,
   which is why dialling from a chat is the most reliable path.

3. **Timeout budgets must shorten at every hop.** The gateway waited 30 s on the
   bridge while the workbench proxy aborts at exactly 30 s, so a slow call
   surfaced as the proxy's generic *"gateway unreachable"* — naming the wrong
   service. Now 12 s (bridge→WhatsApp) < 20 s (gateway→bridge) < 30 s (proxy).

4. **The library is silent unless you ask.** meowcaller resolves to
   `zerolog.Nop()`, so without `WithLogger` every media diagnostic is discarded —
   including the four lines that diagnose a connected-but-silent call. We had
   been debugging against a log we had muted ourselves.

### 12.4 Operating it

Env (all seeded by the deploy):

| Variable | Purpose |
|---|---|
| `WHATSAPP_BRIDGE_SECRET` | Bridge auth **and** the audio-token signing key. |
| `GATEWAY_PUBLIC_URL` | Origin the browser opens the audio socket against — Next cannot proxy a WS upgrade, so audio goes straight to the gateway. |
| `WHATSAPP_BRIDGE_CALL_RECORD_DIR` | WAV output; blank disables recording. |
| `WHATSAPP_BRIDGE_CALL_RETENTION_DAYS` | Sweep age (default 7). Recording runs **~115 MB per call-hour**. |
| `WHATSAPP_BRIDGE_CALL_LOG_LEVEL` | `info` for the media diagnostics; `debug` for the per-packet trace. |

Debugging a call, in order:

1. **Calling readiness** panel in the UI (`GET /whatsapp/calls/diagnostics`) —
   session exists / connected / logged in / calling stack attached.
2. `journalctl -u acb-whatsapp-bridge -f` — what was dialled vs typed, phase
   transitions with elapsed time, `browser audio attached`, and meowcaller's own
   `first RTP sent to relay` / `relay silent after allocate` /
   `first authenticated peer SRTCP received` / `peer SRTCP failed authentication`.
3. **Recent** in the UI — `Ns captured`. Zero seconds on a call that connected
   means signalling worked and media didn't.

### 12.5 Still open

1. **Transcription is not wired.** `/bridge/call-event` logs the recording path;
   nothing feeds it to `acb_stt` or creates a `meeting` row. This is the next
   slice and the reason the feature exists.
2. **Per-participant attribution (§10.2).** We record the mixed sink, so group
   calls need ordinary diarization until the upstream per-participant hook
   lands.
3. **Consent announcement + chat-thread notice** are designed (§7.5), not built.
   With two-way audio in place the announcement is now trivial: a `Player` fed
   by TTS at call start.
4. **Unproven at scale.** One-to-one calling is exercised; group calling is
   experimental upstream and untested here.
