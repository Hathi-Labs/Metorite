# Meeting-Bot Platform — Replicating Recall.ai In-House

**Status:** plan of record for the notetaker's *joining* layer
**Written:** 2026-07-30
**Scope:** how Metorite gets into a live meeting on Google Meet, Zoom, and
Microsoft Teams without depending on a commercial meeting-bot vendor.
**Not in scope:** the transcribe → diarize → summarize → action-item pipeline
(already built and platform-agnostic — see `note_taker_app.md`), and the live
copilot (`live_meeting_copilot.md`). Everything here feeds those unchanged.

---

## 0. The one-paragraph version

Every vendor in this market — Recall.ai, MeetingBaaS, Nylas, Skribby — solves
the same problem the same way, and it is the way we already chose: **there is no
bot API for Google Meet or Microsoft Teams, so everyone drives a real headless
Chromium against the web client.** Zoom is the exception (it has a native SDK,
which it has now closed to notetakers, and a server-side media firehose that
replaces it). What separates a vendor's bot from ours is not the architecture;
it is (a) a **signed-in bot identity** that platforms admit, (b) **per-participant
media capture via WebRTC interception** instead of a room-audio mixdown, (c) a
**calendar layer** that dispatches bots unattended, and (d) an **honest failure
taxonomy** so a blocked join says why. We have (d) already, are one credential
away from (a), and (b)/(c) are the two real builds ahead.

**Strategic split that governs every decision below:** meetings **we host** and
meetings **we're merely invited to** are different problems. For our own
meetings, first-party APIs are now good (Zoom RTMS is GA; Meet's REST API serves
native transcripts). For other people's meetings, browser automation with a
signed-in identity is the *only* route on Meet, and one of two routes on Teams.

---

## 1. What the market actually does

### 1.1 Join mechanism by platform (this is the whole ballgame)

| Platform | Bot SDK exists? | What every vendor actually does | Our status |
|---|---|---|---|
| **Google Meet** | No | Headless-but-headful Chromium (Playwright/Selenium) under Xvfb, driving the web client | **Built** (`app/meet.py`) |
| **MS Teams** | Graph media platform (Windows/.NET/Azure-bound) | Same browser automation; anonymous web join is allowed by default policy | Not built |
| **Zoom** | Yes — Meeting SDK (Linux/C++) | Native SDK bot **+** web-client fallback **+ increasingly RTMS** | Not built |
| **Webex** | Browser/JS Meetings SDK (official, sanctioned) | Official SDK join with real-time media | Not built |
| **Slack huddles** | **No API at all** | Desktop/system-audio capture only | N/A |

Evidence: MeetingBaaS open-sources its Meet+Teams engine as a Playwright bot
(`Meeting-Baas/meet-teams-bot`); Attendee ships `google_meet_bot_adapter`,
`teams_bot_adapter`, `zoom_bot_adapter` (native SDK), `zoom_web_bot_adapter`
**and** `zoom_rtms_adapter` side by side; Recall's error sub-codes leak the same
trio (`zoom_sdk_app_not_published`, `zoom_web_disallowed`,
`rtms_signature_invalid`). Recall's own engineering blog puts the DIY cost of
reaching their parity at roughly **3–5 engineers for a year** — worth knowing
before we aim at "replicate Recall" rather than "cover our own meetings well".

### 1.2 The 2026 crackdown — the thing that changes the plan

The platforms turned hostile to anonymous bots *this year*:

- **Google Meet (March 2026):** third-party bots are surfaced under a **"With
  potential risks"** section in the admit flow, biased toward denial. Meet also
  ejects unsigned bots from watermarked meetings (Recall exposes
  `google_meet_watermark_kicked`). Anonymous bots hosted by personal accounts
  are refused outright — *this is exactly the wall we hit on 2026-07-29/30.*
- **Microsoft Teams (May–June 2026, MC1251206):** Teams now auto-detects
  external third-party bots, drops them into a **"Suspected threats" lobby
  labelled "Unverified"**, and gives admins a tenant-wide
  `ExternalBotAccessMode = BlockDetectedBots` switch.
- **Zoom (March 2, 2026):** the Meeting SDK docs now state it is **"reserved for
  human use cases and does not support bots or AI notetakers."** OBF-token
  enforcement means an SDK app joining an external meeting needs a token tied to
  **an OAuth-authorized user actively present in the meeting** — when they leave,
  the bot dies. Zoom's web client runs **always-on invisible reCAPTCHA v3**
  explicitly to keep automated tools out.

Driving force: *In re Otter.AI Privacy Litigation* (N.D. Cal.) over recording
people who never consented. The direction of travel is unambiguous — **bots
that are anonymous, unannounced, or impersonating get blocked; bots with a real
identity that announce themselves get in.** Our design must lean into the second.

### 1.3 Where the industry is heading: bot-less capture

Recall now sells a **Desktop Recording SDK**; Circleback and tl;dv ship
bot-free desktop modes; Granola never had a bot; Zoom built **RTMS** so no bot
joins at all. Three converging escapes from bot fatigue. We should treat
"a participant bot" as one of *three* capture strategies, not the only one.

### 1.4 Convergent lifecycle (validates ours)

Every vendor independently arrived at:

```
scheduled → launching → joining → waiting_room → in_call_not_recording
   → [recording permission gate] → in_call_recording → call_ended
   → processing → done          ‖  fatal(sub_code) at any point
```

Ours (`joining → waiting_room → in_call → processing → done | failed |
not_admitted`) is the same shape. Recall's ~80 **sub-codes** are the reference
implementation of honesty, and include failures we haven't modelled: *only bots
detected in the call*, *meeting locked/full*, *registration required*, and
their own *`failed_to_launch_in_time`*. Standard safety timers everywhere:
waiting-room (600 s), no-one-joined, everyone-left, and **silence detection**.

### 1.5 Cost of the vendors, for the build-vs-buy line

| Provider | Per bot-hour | Notes |
|---|---|---|
| Skribby | $0.35 + STT | cheapest, no calendar layer |
| Recall.ai | $0.50 + $0.15 STT + storage | best-in-class, full calendar + real-time |
| MeetingBaaS | ~$0.63–0.69 equiv. | **requires $99–299/mo subscription** |
| Nylas | $0.70 all-in | no real-time stream, no signed-in bots |
| **Us (self-hosted)** | **~$0** marginal | VPS already paid for; our cost is engineering |

At even 100 meeting-hours/month, vendor spend is $35–70/mo — *small*. The reason
to self-host is not cost, it is **data control and the copilot**: our bot has to
be an agent that consults business systems mid-call, and no vendor exposes that.

---

## 2. What we already have (verified, 2026-07-30)

Grounding so this plan doesn't rebuild working code:

| Capability | Where | State |
|---|---|---|
| Meet join flow (canonical URL, name, mic/cam off, ask-to-join, admission wait) | `app/meet.py` | Built; **real-join verify still owed** |
| Anti-automation-fingerprint launch flags | `meet.py:chrome_launch_args()` | Built — matches the industry set |
| Persistent signed-in Chrome profile + Google sign-in endpoints | `MEET_PROFILE_DIR`, `app/google_login.py` | Built, live, **awaiting a bot account** |
| Room-audio capture (PulseAudio null sink → ffmpeg) | `meet.py:_start_ffmpeg` | Built + verified with a real tone |
| Live streaming ASR + per-utterance speaker embeddings | `app/live.py`, `endpointing.py` | Built + verified against a stub ASR |
| Speak into the call (TTS → virtual mic) | `meet.py:_say_consumer`, `live.py:speak` | Built; needs a voice installed |
| Failure diagnostics (controls list, body text, screenshot, `state.json`) | `meet.py:_snapshot`, `main.py` | Built — **better than most OSS** |
| Provider abstraction (`selfhosted` ‖ `recall`) | `routes/notes/meeting_bot.py` | Built — Recall is a config flip away |
| Honest refusal of Zoom/Teams links | `SELFHOSTED_PLATFORMS` | Built |
| VNC debug loop into the live browser | `MEET_VNC=1`, entrypoint | Built + verified |
| Action-item dispatch (task/email/doc) + mid-call agent consults | `routes/notes/dispatch.py`, `copilot_context.py` | Built (migration 129) |

**Two honest gaps in what we have:** capture is a **room mixdown**, so speaker
attribution is inferred from voice embeddings rather than known per-participant
streams; and there is **no calendar layer**, so every join is manual.

---

## 3. The techniques worth stealing (with licence reality)

| Source | Licence | Verdict |
|---|---|---|
| **Vexa** (`Vexa-ai/vexa`) | **Apache-2.0** | ✅ Safe to lift code with attribution |
| **joinly** (`joinly-ai/joinly`) | **MIT** | ✅ Safe to lift code |
| **screenapp/meeting-bot** | **MIT** | ✅ Safe to lift code |
| **Attendee** (`attendee-labs/attendee`) | **Elastic License 2.0** | ⚠️ **Not open source.** Internal use OK; forbids offering the functionality as a hosted/managed service. **Read for technique, reimplement independently — do not copy code.** |
| **meetingbot/meetingbot** | LGPL-3.0 | ⚠️ Copyleft — avoid |
| **recallai/google-meet-meeting-bot** | **No licence** | ⛔ Read only; copying is legally unusable |

Attendee has the best techniques *and* the most restrictive licence. Since
Metorite is our internal tool (not a SaaS we resell), ELv2's use grant is
satisfiable *[⚠️ 2026-08-09: this compliance argument rests on the retired D10
premise — under WS-29 Metorite IS resold. Re-evaluate the ELv2 use-grant
(Attendee is ELv2, not OSS) before the first external tenant uses meeting-bot
features; flag carried in work_plan.md WS-19.]* — but the safe engineering posture is: **treat Attendee as a
research paper.** The techniques below are architectural facts about Chrome and
Meet, not Attendee's expression of them.

### 3.1 ★ The big one: intercept `RTCPeerConnection`, don't capture the room

Instead of recording what the speakers play (our PulseAudio mixdown), wrap the
browser's own WebRTC stack from inside the page:

```js
// injected before Meet's own script runs
const Orig = window.RTCPeerConnection;
window.RTCPeerConnection = function (...args) {
  const pc = Reflect.construct(Orig, args);
  pc.addEventListener('track', onTrack);        // every participant's stream
  const origDC = pc.createDataChannel.bind(pc);
  pc.createDataChannel = (label, o) => tap(label, origDC(label, o));
  return pc;
};
```

What this unlocks, none of which the mixdown can give us:

1. **Per-participant audio**, via `MediaStreamTrackProcessor` (WebCodecs) per
   track → *diarization becomes free*. You never separate a mix, because it was
   never mixed. This is the single highest-leverage change available to us.
2. **Meet's own data channels**, which carry protobuf: a `collections` channel
   with participant names + device IDs, and a `captions` channel with
   **speaker-device-tagged caption text**. That is speaker-labelled
   transcription with **zero STT cost**.
3. **Speech activity from `getContributingSources()`** (CSRC + `audioLevel`,
   polled ~250 ms with hysteresis) — cheap, robust who-is-speaking-now.
4. **Survives UI redesigns.** The DOM changes constantly; the WebRTC API does
   not. Everything except the join click stops depending on selectors.

Transport from page → worker: one **localhost WebSocket with 4-byte type tags**
(`1 JSON / 2 VIDEO / 3 AUDIO / 5 PER_PARTICIPANT_AUDIO`), so media and control
events stay mutually ordered on one channel.

Brittleness to accept: the protobuf schemas are private and *will* change. Use
them opportunistically (a free bonus) and keep our existing ASR path as the
guaranteed floor.

### 3.2 Chrome flags — ours already match

`--disable-blink-features=AutomationControlled`, `excludeSwitches:
["enable-automation"]`, `--use-fake-ui-for-media-stream`,
`--autoplay-policy=no-user-gesture-required`, headful under Xvfb (never
`--headless`). Two additions worth taking: `--use-fake-device-for-media-stream`
(satisfies the media gate with no hardware) and a **non-round window size**
(Attendee uses 1930×1090 to dodge the exact-1080p fingerprint).

### 3.3 Identity: log in once, reuse the session forever

Universal practice, and the documented anti-flag technique: **never script a
password login repeatedly.** Sign in once (by hand if needed), persist the
profile / `storageState`, reuse. Recall goes further and runs its own SAML IdP
for a dedicated Workspace so no Google password page is ever automated. Repeated
fresh logins are what summon 2FA challenges and CAPTCHAs. **Our persistent
`MEET_PROFILE_DIR` is already this pattern.** Recall runs ~30–50 concurrent
meetings per account and round-robins a pool; at our scale one account is fine.

### 3.4 Distinguish denial from timeout, and retry the knock

Typed outcomes (`WaitingRoomTimeout` vs `RequestToJoinDenied`) rather than one
"failed", plus a retry loop on the anonymous-join path — screenapp retries the
Meet guest request **10 times** by default because it fails intermittently.
Collapsing these is the most common design mistake, and we currently collapse
some of them.

### 3.5 Gate recording on permission, not on page load

Two-phase: the page signals readiness, the worker enables frame-sending only
once admission/permission is actually granted. Prevents recording the waiting
room, and makes "we never record before consent" structurally true.

### 3.6 Auto-leave condition set

Ours has max-duration and alone-timeout. Missing: **silence detection**,
**caption-failure**, **kick detection** (participant list says we were removed),
and the subtlety that lone-participant exit should only **arm after other
participants have been seen** (else the bot leaves before anyone arrives).

### 3.7 joinly's MCP tool surface (MIT — liftable)

`join / leave / speak / chat / mute / unmute / transcript` as tools, plus a
**subscribable live-transcript resource**. This is the cleanest published
abstraction for making a meeting agent-drivable, and it maps directly onto our
copilot's needs. Their diarization, by contrast, does not exist — don't copy it.

---

## 4. The plan

Six phases, ordered by *value per unit of risk*. Phases 1–2 are small and
unblock everything; 3–4 are the real capability jumps; 5–6 are optional reach.

### Phase 1 — Finish the identity story (blocked on one credential)

The decisive unverified path. Nothing else matters until a bot actually gets in.

**First, clear up what "a Google Account" means here, because the phrasing
misleads.** It is a **login, not a mailbox.** Google lets you sign up with an
address you already own instead of creating an `@gmail.com` one, and when you do
it provisions an *identity* — a username and password — and deliberately does
**not** provision Gmail. No MX record moves, no mail is routed to Google, no
second inbox appears; Outlook remains the only mailbox for that address. It is
the same act as signing up for Zoom or Figma with a work email.

Its only job is to give the bot's Chrome something to sign in *with*, because
Meet will only treat a signed-in participant as admittable. The address (rather
than any username) matters for exactly one reason: Meet's auto-admit matches the
**invited** address against the **signed-in** account's address, so they have to
be the same string.

Real consequences, and they are the whole list: Google will send occasional
security mail to the address, which lands in Outlook (file it with a rule), and
the address must keep receiving mail indefinitely because account recovery
depends on it — so don't retire the catch-all later.

**The identity does NOT have to be a Gmail address.** Three options, and the
middle one is recommended:

| Option | Cost | Gets us | Cost of choosing it |
|---|---|---|---|
| Plain Gmail (`ccnotetaker@gmail.com`) | Free | A working signed-in bot | Looks like a stranger on client calls; invites land in a mailbox nobody reads |
| **★ Google Account on our own domain** (`notetaker@fracktal.in`) | Free | Same capability, our own branding, and invites arrive in a mailbox we already control in Microsoft 365 | None material — Google's signup accepts an existing address ("Use your existing email"), verification goes to that mailbox |
| Google **Workspace** on the domain, MX left with Microsoft | ~$7–14/user/mo | Makes us a *Workspace host*: link-guests can ask to join, native transcripts + Gemini notes retrievable via the Meet REST API, and no anonymous-guest wall on meetings we host. This is the tier Recall.ai runs its own bot accounts on. | Recurring cost, DNS TXT verification, and Workspace admin to maintain. Revisit if Meet becomes core. |

Workspace's "**Skip Google MX setup**" is the load-bearing detail for option 3:
the domain activates for Meet/Calendar/Drive while mail keeps flowing to
Microsoft 365, so adopting it would not touch email at all.

Option 2 is recommended because it is free, looks right, and the bot's mailbox
becomes the foundation of the invite-driven opt-in in Phase 4 *and* the future
"bot finds a slot and schedules it" feature — a Gmail would strand those invites
somewhere nobody watches. A catch-all on the domain means no new mailbox is
even needed.

1. Register the chosen address as a Google Account — i.e. create a Google
   *login* on it (see the note above; this does not create a mailbox, move mail,
   or affect Outlook).
   **2-step verification is not a blocker** — it only rules out the *scripted*
   `POST /google-login`. If Google demands a phone, 2SV, or "verify it's you",
   use `POST /google-login/interactive` and complete it by hand over VNC once;
   the persistent profile keeps the session afterwards. (Interactive needs
   `MEET_VNC=1`, which defaults to `0` — set it in `/opt/acb/app/.env` and
   restart the container, then tunnel to 6080.)
2. `POST /google-login` → verify `signed_in: true` persists across a container
   restart (the volume is `acb-meeting-bot-profile`).
3. **Verify a real join end to end**, then set the display name to something
   unambiguous ("Metorite Notetaker").
4. Put the bot's address on a calendar invite and confirm the **waiting-room
   bypass** — the only fully unattended admission path that exists on Meet.
5. Document the failure→remedy table from what actually happens.

**Exit criteria:** a real Meet call recorded, transcribed, summarized, and
action-items dispatched, with no human clicking Admit.

### Phase 2 — Lifecycle honesty and safety timers (small, self-contained)

Bring our status model up to the industry reference *before* adding platforms,
because every new platform multiplies the failure modes.

- Add sub-codes to `meeting_bot.error`: `not_admitted_denied` vs
  `not_admitted_timeout`, `meeting_not_started`, `meeting_locked`,
  `requires_sign_in`, `sign_in_captcha`, `landing_page`, `watermark_kicked`,
  `only_bots_present`, `launch_timeout`. Surface them as one-line explanations
  plus a remedy in the Notes UI (the plumbing for this already exists).
- Add auto-leave conditions from §3.6, including **silence detection** and
  **kick detection**, and arm lone-participant exit only after seeing others.
- Retry the knock on transient Meet redirects (bounded, ~5 attempts).
- Gate recording on permission per §3.5.

### Phase 3 — WebRTC interception (the capability jump)

Rewrite capture per §3.1, keeping the current pipeline as the fallback.

- Inject the payload on `page.add_init_script` so it beats Meet's own bundle.
- Per-participant audio → the existing live/ASR path, but now **each stream is
  already attributed**, so voice-embedding diarization becomes a *fallback for
  merged streams* rather than the primary mechanism.
- Opportunistically decode the `captions` channel: free speaker-tagged
  transcript, no ASR spend. Treat schema drift as expected; fall back silently.
- Keep the PulseAudio mixdown as the archival recording (it is verified and
  cheap) — do not delete a working path to adopt a better one.

**Why this matters beyond quality:** per-participant streams make "who said
they'd do what" reliable, which is what makes auto-dispatched action items
trustworthy. It's the difference between a recorder and a notetaker.

### Phase 4 — Calendar-driven auto-join (the UX jump)

Mechanically identical at every vendor, and we already hold the pieces (the
calendar app, and a `join_at`-shaped bot dispatch):

- Watch the connected calendar for events carrying a meeting URL (native
  conference field → location → description, in that order).
- Dispatch a bot at **T-0** (Recall uses T-2 min; Fireflies joins exactly at
  start). Knock, then give up after 10–15 min.
- The standard control set, defaulting **narrow**: `all with a link` /
  `only meetings I organize` / `internal only` / `external only` /
  `only when the bot is invited`, plus a **per-meeting toggle** on the upcoming
  list and an **accepted-RSVP-only** filter.
- Re-sync on event time/URL changes; cancel the bot if the event is cancelled.
- Dedupe so a double-dispatch can't put two bots in one room.

This is also where the user's idea lands: **give the bot a real organizational
mailbox.** Once it has an address, inviting it *is* the opt-in — the same UX
Fireflies sells as `fred@fireflies.ai`. Later, that mailbox can accept
scheduling requests and find slots (a natural extension of our email + calendar
apps, and deliberately out of scope here).

### Phase 5 — Second platform: Teams before Zoom

Teams is strictly easier and less legally fraught than Zoom right now.

- **Anonymous web join is still allowed by default policy** ("Join on the web
  instead"), so our existing browser worker generalizes: a `teams_join` module
  alongside `meet.py`, sharing the launch flags, snapshot diagnostics, capture
  pipeline, and lifecycle. Expect the lobby, expect the **"Unverified"** label,
  and expect CAPTCHA occasionally.
- The sanctioned alternative if automation proves unreliable: **Azure
  Communication Services** guest join — programmatic, ~free for Teams external
  users, admitted like any guest. Limitation: **mixed audio only**, no
  per-participant streams. Good enough for transcription; worse for diarization.
- Avoid the Graph app-hosted media bot: raw RTP audio, but C#/.NET on a Windows
  Azure VM with a public IP and **admin consent in every tenant whose meetings
  it joins.** Wrong shape for us entirely.

### Phase 6 — Zoom, honestly scoped

Zoom is the one platform where the DIY bot path is now *officially closed*, so
split by whose meeting it is:

- **Meetings we host → Zoom RTMS.** GA since June 2025, clientless (no bot
  joins at all), streams **per-participant audio, video, transcripts, and chat
  over WebSockets** to our backend. Requires a paid Zoom plan with RTMS enabled
  and Developer Pack credits (~$1/credit). This is the *best* media quality
  available on any platform and needs no browser.
- **Meetings we're invited to → don't pretend.** The Meeting SDK forbids
  notetakers; OBF tokens require an authorized participant present; the web
  client is CAPTCHA-walled by design. Options, in order: (a) keep refusing the
  link honestly and offer the upload path — *what we do today, and it's correct*;
  (b) flip `NOTES_BOT_PROVIDER=recall` for Zoom only, letting a vendor absorb
  the SDK-approval burden while Meet stays in-house; (c) device-local capture
  (Phase 7).
- Recommendation: **do (a) now, keep (b) as the documented escape hatch.**
  Recall's provider is already wired; a per-platform provider selector is a
  small change to `resolve_bot_provider()`.

### Phase 7 (optional) — Device-local capture, the no-bot fallback

Granola's model: capture **system audio + microphone on the user's own machine**.
Platform-agnostic by construction — Meet, Zoom, Teams, Slack huddles, even a
phone call. No waiting room, no admission, no anti-bot arms race. Costs:
attribution degrades to "me / them" (recoverable via our voice-embedding
gallery, which already exists), it only works when the user actually attends,
and **the consent burden shifts entirely to the user** since nothing signals
recording to the room. Worth building precisely for the meetings we can never
get a bot into — which, after Phases 5–6, means Zoom calls hosted by others.

---

## 4a. Live findings, 2026-07-30 — and why we are NOT buying a residential proxy

Tested against a real meeting with the host present in the call. Recorded here
because it cost an afternoon and the conclusion is counter-intuitive.

**What Meet does.** Its green room states: *"System info will be sent to confirm
you're not a bot."* Our bot loads the page, fills its name, mutes, clicks "Ask to
join" — and is declined **~3 seconds later**, without the host ever being shown
an Admit prompt. The decline is programmatic, not a human saying no.

**Ruled out by experiment, so don't re-litigate:**

| Hypothesis | Test | Result |
|---|---|---|
| Our join automation is broken | probe reached the green room in the same container | ✗ our code is fine |
| The persistent profile is poisoned | loaded with `/profile` | ✗ same green room |
| Nobody was in the call to admit it | host confirmed present, retried | ✗ still declined in 3 s |
| Missing media devices are the tell | added `--use-fake-device-for-media-stream` (7 fake devices) | ✗ declined identically |
| Chromium's missing H.264 is the tell | installed **real Google Chrome 151** | ✗ declined *sooner* |

Chromium can't play H.264 while real Chrome can, which looked like the smoking
gun — it wasn't. A control run proved Chromium still reached the green room at
that moment, so neither the meeting nor a blocked IP explained it; Google simply
fingerprints real-Chrome-under-automation more precisely.

**Still not isolated:** IP reputation vs automation artifacts. Both remain
plausible and a proxy would test only the first.

### Rejected: residential proxies

The obvious next move is to give the bot a residential IP. We are not doing it,
for four reasons in descending order of importance:

1. **It doesn't remove the check — the invite does.** A signed-in bot whose
   address is on the calendar invite is **auto-admitted with no knock at all**,
   so there is no join request for Meet to evaluate. A proxy tries to *pass* the
   bot check; an invite means the check never runs. Categorically better.
2. **WebRTC media barely survives a proxy.** Forcing media through one needs
   Chrome's `disable_non_proxied_udp`, which requires SOCKS5 **with UDP
   ASSOCIATE** — rare in residential-proxy products — or it falls back to TCP,
   reintroducing retransmission and congestion control to a real-time stream.
   Degraded audio means degraded transcripts, which is the whole product.
3. **Cost scales with minutes, not requests.** Residential proxies bill per GB
   and a meeting is continuous media. This is the opposite of the traffic shape
   they're priced for.
4. **It's an arms race against Google, and losing it fails silently** — mid-call,
   in front of clients.

**The honest version of "residential IP", if we ever truly need one:** run a
worker on a machine on the office connection (a mini PC), reached over a
Tailscale/Cloudflare tunnel. A real ISP address, no proxy, no ToS games. It still
doesn't remove the knock, so it ranks below the invite either way.

### How the market actually solves this

Not one serious vendor's primary strategy is "make an anonymous bot look human":

- **Fireflies** — the bot has its own address (`fred@fireflies.ai`); inviting it
  *is* the opt-in. Signed-in bots.
- **Otter** — joins as a named participant ("<name>'s OtterPilot") off the
  connected calendar.
- **Recall.ai** (the infrastructure under many notetakers) — a dedicated, paid
  Google Workspace tenant, pools of standard accounts round-robined ~30–50
  concurrent meetings each, authenticated via **SAML SSO where Recall is the
  IdP** so no Google password page is ever scripted. They recommend residential
  proxies only in their *build-it-yourself* blog post, as a supporting tactic —
  it is not how their product works.
- **Skribby** — signed-in accounts on Meet/Teams, ZAK tokens on Zoom.
- **Nylas** — has *no* signed-in bot mechanism, and consequently documents that
  org-restricted meetings need a human to approve the bot. That is the price of
  skipping the account, paid in their own docs.
- **Granola / Circleback / tl;dv desktop** — sidestep entirely with device-local
  capture (our Phase 7).

The pattern is unanimous: **a real account identity is the mechanism.** Proxies
are scale plumbing for people running thousands of concurrent bots.

---

## 4b. Multi-tenancy: one notetaker identity per organization

The single-tenant design has a global bot identity and a single browser profile.
*(re-scope under WS-29: bot identity becomes per-org at MT-1+; this section
describes the current internal deployment)*
The moment two organizations (or two users in different orgs) use the notetaker,
that global is the thing that breaks — and the *email is the least of it*.

### What actually breaks, worst first

1. **A signed-in Chrome profile is exclusive.** Chrome takes a lock on its
   user-data dir, so two concurrent meetings **cannot share one profile**. This
   is a hard mutual exclusion, not a performance concern. (Guarded now: the
   worker 409s a second dispatch instead of racing the lock — before this, the
   second job would have failed deep in the launch or corrupted the profile that
   makes unattended joining possible.)
2. **Capacity.** Each bot is a real Chrome in a live WebRTC call: **1.5–3 vCPU
   and 3–6 GB** by the providers' own published per-pod figures. The current VPS
   has **2 vCPU** (RAM is fine at ~8 GB), so it seats **one** concurrent
   meeting. No software change alters that; only more hosts do.
3. **Identity commingling.** One Google account shared across orgs means Org A's
   notetaker account sits in Org B's calls, and one browser profile holds Org A's
   Google session while serving Org B's meeting. This is the same defect class
   the repo already had to fix for agents (`agent_blob_instance` +
   `quarantine_commingled_agent_data`) — worth reusing that thinking rather than
   rediscovering it.
4. **Attribution.** Two orgs dispatching the same display name to different
   client calls is confusing at best and a disclosure at worst.

Notably **not** broken: the notes data itself. `meeting` and `copilot_config` are
already keyed by `owner_email`, and org-level access control landed in migration
130 — so transcripts and notes don't leak today. The gap is the *joining* layer.

### The design

Mirror the email app, which already solved per-account isolation:

- **`notetaker_identity` table keyed by `organization_id`** — bot email, display
  name, sign-in state, profile handle. One row per org, exactly like
  `email_account` per mailbox. The identity endpoints become org-scoped rather
  than global, and the EXECUTIVE guard becomes "executive **of that org**".
- **Profile per identity, not per worker.** `/profile` becomes
  `/profiles/{organization_id}`, passed at dispatch. One volume, many profiles.
- **One container per meeting** (the industry norm — Recall and Attendee both
  run a pod per bot). The worker stays single-meeting; concurrency comes from
  instances, and the gateway picks a free one.
- **A queue with honest UX.** When every worker is busy, say so and hold the
  request — "your notetaker will join when the current call ends" beats a
  failure. Recall models this as login groups with round-robin and a hard
  `login_not_available` error when the pool is exhausted; we need the same
  vocabulary long before we need their scale.
- **Per-org bot naming** ("<Org> Notetaker") so attribution is unambiguous.

### Sequencing note

None of this is needed for one organization, and building it now would be
speculative. *(dated 2026-08-09: needed at the first external tenant — sequence
with WS-29)* But two things should happen *before* a second org is onboarded, or
they become data-migration problems instead of design choices: the identity must
be **a row keyed by org from the start** (not an env var), and the profile path
must be **derived from that row** (not a constant). Both are cheap now and
expensive later.

---

## 5. Non-negotiable design rules

Distilled from how the market gets blocked. These are constraints, not
preferences.

1. **Real, invitable identity.** A dedicated signed-in account whose address
   users add to invites. Never the owner's personal account.
2. **One login, persisted forever.** Never script repeated password logins.
3. **Honest naming.** "Metorite Notetaker" — never a human's name, never
   something that reads as a real person. Deceptive names get bots blocked.
4. **Announce recording; honour opt-out.** Post an entry message on join
   (Fireflies won't join if someone opts out; Nylas posts to chat). A bot in the
   participant list is *not* legal consent anywhere — two-party-consent states
   and GDPR both require informed agreement. Never attempt to suppress Zoom's
   consent prompt (it's impossible, and trying is a policy violation).
5. **Knock politely, once. Treat denial as final.** No re-knock loops. Bounded
   retries only for transient redirects, never for an explicit refusal.
6. **Default-narrow auto-join.** Opt in per meeting or per class; never
   "everything with a link" by default.
7. **Never claim success you didn't verify.** The recurring failure mode in this
   project has been reporting shipped work against unverified paths — a deploy
   that reported success on a stale image, and a "fixed" join that Google was
   refusing on policy grounds. Every phase above ends in a live check.
8. **One meeting per container.** A bot is stateful, long-lived, and ~1.5–3
   vCPU / 3–6 GB RAM. Never share a browser between meetings.
9. **Keep the vendor escape hatch warm.** `NOTES_BOT_PROVIDER=recall` must stay
   working. When a platform's automation breaks mid-week, a config flip is a
   better answer than an outage.

---

## 6. Recommended order (and what I'd skip)

**Do in order:** Phase 1 (blocked on one credential) → Phase 2 (small, makes
everything debuggable) → Phase 4 (calendar auto-join — biggest UX gain per line
of code, and where the bot's mailbox pays off) → Phase 3 (WebRTC interception —
biggest quality gain, largest build) → Phase 5 (Teams).

**Deliberately deprioritize:** Phase 6's Zoom bot (officially closed; refuse
honestly and offer Recall for Zoom only) and the Graph media bot (wrong stack).
Phase 7 is the right answer for Zoom-hosted-by-others when we care enough.

**What we are not trying to be:** Recall.ai. They are ~3–5 engineer-years into
per-bot CPU optimization, 80-code failure taxonomies, and account pools for
concurrency we will never need. Our win condition is different and narrower:
*our own meetings, captured reliably, with an agent in the room that can reach
our business systems.* No vendor sells that, which is the actual reason to build.

---

## 7. Open questions

1. Do the Meet protobuf channel schemas still match published decoders? (Most
   brittle dependency in Phase 3 — verify before committing to it.)
2. Does the bot account want a real mailbox now (invite-driven opt-in, future
   scheduling agent) or later? Affects Phase 4's shape.
3. Is a paid Zoom plan on the table? If yes, RTMS makes our own Zoom meetings
   the *highest*-quality capture we'd have anywhere.
4. Google Workspace for the org? It would make Meet dramatically easier
   (host-side "anyone with the link can ask to join", native transcripts via the
   Meet REST API, Gemini smart-notes retrievable by API). **Not blocked by
   Microsoft 365** as first assumed — Workspace's "Skip Google MX setup" lets the
   domain activate for Meet/Calendar while mail keeps flowing to Outlook. So it
   is purely a cost decision (~$7–14/user/mo), not a migration.
