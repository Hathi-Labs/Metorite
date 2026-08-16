# Metorite Meeting Bot (self-hosted)

A **fully self-hosted** meeting-joining worker — a headless-Chrome (Playwright)
participant that joins a meeting link, records the call audio, and hands it back
to Metorite. **No third-party cloud, no per-hour API.** The only cost is
the machine this runs on.

It exists so the AI Note Taker's "Join call" feature (spec §3.13) can be driven
entirely in-house: the gateway's `selfhosted` bot provider talks to this worker
over a small HTTP contract, and the recording flows into the normal
transcribe → diarize → speaker-name → summary pipeline like any other recording.

## Why it's a separate service

Each bot **is a real headless Chrome joining a live WebRTC call** — roughly
**1–3 GB RAM + up to 2 CPU cores per concurrent meeting**, and this MVP runs
**one meeting per instance** (scale out by running more instances). That does
**not** fit Metorite's small default VPS, so this worker is deliberately
standalone: run it on the upsized VPS or a dedicated box.

## HTTP contract

| Method | Path | Body / Result |
|---|---|---|
| POST | `/bots` | `{meeting_url, bot_name, live_callback?}` → `{id, status}` |
| GET | `/bots/{id}` | → `{id, status, download_url\|null, error\|null}` |
| POST | `/bots/{id}/leave` | leave now → `202` |
| POST | `/bots/{id}/say` | `{text}` → speak into the call → `202` |
| GET | `/bots/{id}/recording` | audio bytes (when `status == "done"`) |
| GET | `/health` | `{ok: true, active: N}` |

Statuses: `joining → waiting_room → in_call → processing → done` (or `failed` /
`not_admitted`). Optional `Authorization: Bearer <MEETING_BOT_TOKEN>`.

`POST /bots` returns **409 when the worker is already in a meeting**. That is
normal, not an error: a bot is a whole Chrome, and a signed-in profile can only
be held by one Chrome at a time. Scale with more worker instances, never by
raising `MEET_MAX_CONCURRENT` while sharing a profile.

`GET /bots/{id}` also carries **`end_reason`** — `ended`, `kicked`,
`noone_joined`, `everyone_left`, `asked` or `max_duration`. It exists because a
short or empty recording has several unrelated causes, and "captured no audio"
alone sends people to debug a working audio stack when in fact nobody joined.

## Live streaming + speaking (optional, for real-time agents)

Beyond batch record-then-transcribe, the worker can **stream** the transcript
live and **speak** back into the call — the foundation for agents that act
mid-meeting:

- **Live transcript:** while in-call the worker tees the audio to a streaming
  ASR and POSTs each segment to the `live_callback` URL the gateway passes at
  join (built from `NOTES_LIVE_CALLBACK_BASE`). The gateway fans those out to
  live captions (`GET /notes/meetings/{id}/live`) and to agents. Two ways to
  get an ASR, checked in this order:

  1. **`LIVE_ASR_URL`** — your own streaming ASR WebSocket (WhisperLive-style).
     Free per minute if you run one; wins when set.
  2. **`LIVE_TOKEN_URL`** (the normal path, wired by the deploy) — the worker
     asks the gateway for a short-lived token for whichever provider is keyed
     in **Settings → Models**, and streams to AssemblyAI directly. The master
     key never enters this container, and switching providers in Settings
     applies to the bot with no redeploy.

  With neither, the bot records and the batch pipeline transcribes after the
  call — but there are **no live captions**, which looks like success until the
  meeting ends. Only completed turns are forwarded; partials would stutter the
  same sentence down the console a word at a time.
- **Consistent live speakers (pause-chunked spine):** set `EMBED_CMD` to attach a
  per-utterance speaker **embedding** to each segment. The worker runs a local
  pause endpointer (VAD on natural pauses, not fixed windows — `endpointing.py`),
  computes an embedding per utterance, and tags the overlapping ASR segment. The
  gateway's voiceprint gallery (`live_speakers.py`) then keeps speaker ids stable
  across chunks and binds names from self-intros — so an agent knows *who* is
  speaking live. With no `EMBED_CMD`, segments are text-only (unchanged).
- **Speak into the call:** `POST /bots/{id}/say {text}` renders text via `TTS_CMD`
  (a shell template with `{text}`/`{out}`, e.g. a piper invocation producing a
  WAV) and plays it into the bot's **virtual microphone** so participants hear
  it. With no `TTS_CMD` the request is logged, not spoken.

Batch recording is unaffected by either — both are additive and gated.

## Deployment (the normal path)

**It deploys itself.** `infra/docker-compose.yml` carries a `meeting-bot`
service under the **`meetingbot` profile** (deliberately *not* `core` — each
in-call bot is a real Chrome, so it must never start just because someone
brought the stack up), and the deploy workflow builds and starts it, generates
`MEETING_BOT_TOKEN` once, and points the gateway at it:

```
MEETING_BOT_ENABLED=1                                  # opt out with 0
MEETING_BOT_URL=http://127.0.0.1:8095                  # gateway → worker
NOTES_BOT_PROVIDER=selfhosted
NOTES_LIVE_CALLBACK_BASE=http://host.docker.internal:8080   # worker → gateway
```

The worker publishes on host **8095** because the gateway itself owns 8080, and
binds to loopback only — nothing off-box can dispatch a bot. A build or start
failure skips the bot and never fails the deploy.

To turn it off: set `MEETING_BOT_ENABLED=0` in `/opt/acb/app/.env` and redeploy
(the container is removed; join-by-link just becomes unavailable).

## Run it standalone (dev)

```bash
cd apps/services/meeting_bot
MEETING_BOT_TOKEN=$(openssl rand -hex 24) docker compose up -d --build
curl localhost:8080/health
```

Then point the gateway at it (on the Metorite host `.env`):

```
NOTES_BOT_PROVIDER=selfhosted
MEETING_BOT_URL=http://<worker-host>:8080
MEETING_BOT_TOKEN=<same secret as above>
```

## Environment

| Var | Default | Meaning |
|---|---|---|
| `MEETING_BOT_TOKEN` | _(none)_ | Bearer secret the gateway must send. Set it. |
| `MEETING_BOT_DATA` | `/data` | Where recordings are written. |
| `MEET_JOIN_TIMEOUT` | `150` | Seconds to wait in the waiting room before giving up. |
| `MEET_MAX_DURATION` | `14400` | Hard cap (s) on a single recording (4 h). |
| `MEET_ALONE_TIMEOUT` | `120` | Leave this long after **everyone else left** a call it was really in. |
| `MEET_NOONE_JOINED_TIMEOUT` | `300` | Leave this long after joining a room **nobody ever came to**. Deliberately separate from the above: a meeting that never started should be abandoned quickly, while a call that emptied out may still be reconnecting. |
| `MEET_MAX_CONCURRENT` | `1` | Meetings this worker will take at once. Each needs its own Chrome (~1.5–3 vCPU, 3–6 GB), and a shared signed-in profile can only serve one. Scale with instances. |
| `LIVE_ASR_URL` | _(none)_ | Self-hosted streaming-ASR WebSocket. Takes priority over `LIVE_TOKEN_URL`. |
| `LIVE_TOKEN_URL` | _(set by deploy)_ | Gateway endpoint that mints streaming credentials from the key in Settings → Models. Unset **and** no `LIVE_ASR_URL` → no live captions. |
| `LIVE_CALLBACK_TOKEN` | `$MEETING_BOT_TOKEN` | Bearer for the worker→gateway live callback. |
| `TTS_CMD` | _(none)_ | Shell template (`{text}`,`{out}`) that renders speech to WAV. Unset → can't speak. |
| `EMBED_CMD` | _(none)_ | Shell template (`{in}` PCM s16le 16k mono → `{out}` JSON float array) that emits a per-utterance speaker embedding. Unset → text-only segments. |
| `LIVE_VAD_RMS` | `300` | Energy-VAD threshold (RMS over s16le) for the pause endpointer. Tune per room/mic. |
| `LIVE_SEGMENT_MAX_WAIT` | `2.5` | Max seconds a recognised segment waits for its utterance to close (and so for its embedding) before being forwarded untagged. Bounds live latency. |
| `CHROME_EXECUTABLE` | _(none)_ | Path to a Chrome/Chromium binary. Needed when the browser on the box wasn't installed by *this* Playwright version — otherwise the launch fails with "Executable doesn't exist at …/chromium-&lt;rev&gt;". |
| `MEET_PROFILE_DIR` | `/profile` (by deploy) | Persistent Chrome profile. Set → the bot keeps whatever Google account was signed in via `/google-login`, so Meet stops auto-declining it as anonymous. Unset → anonymous, and joins need a human to admit it. |
| `MEET_GOOGLE_EMAIL` / `MEET_GOOGLE_PASSWORD` | _(none)_ | Optional credentials for `POST /google-login`. Use a dedicated bot account **without 2FA**. |
| `MEET_VNC` | `0` | `1` → live VNC view of the bot's browser on 6080 (noVNC) / 5900. Loopback only; use an SSH tunnel. |
| `MEET_LOGIN_WINDOW` | `600` | Seconds an interactive (VNC) sign-in session stays open. |

## The anonymous-guest wall (read this before debugging a join)

**Google auto-declines anonymous participants, and being in the call yourself
does not help.** The green room states it outright: *"System info will be sent to
confirm you're not a bot."* An automated browser fails that check. The bot fills
its name, mutes, clicks "Ask to join", and is refused **~3 seconds later** — the
host is never shown an Admit prompt, so there is nothing to click.

Established by experiment on 2026-07-30 against a live meeting **with the host
present**. Five hypotheses were tested and killed; don't re-run them:

| Hypothesis | Test | Result |
|---|---|---|
| Our join automation is broken | a probe reached the green room in the same container | ✗ the code is fine |
| The persistent profile is poisoned | loaded the meeting with `/profile` | ✗ same green room |
| Nobody was in the call to admit it | host confirmed present, retried | ✗ still refused in 3 s |
| Missing mic/camera is the tell | added `--use-fake-device-for-media-stream` | ✗ refused identically |
| Chromium's missing H.264 is the tell | installed **real Google Chrome 151** | ✗ refused *sooner* |

Not isolated: IP reputation vs automation artifacts. A datacenter IP is a
plausible contributor, but see the plan for why a residential proxy is the wrong
answer (it tries to *pass* the check; an invite means the check never runs).

What actually works:

1. **Sign the bot in and put its address on the calendar invite.** Meet then
   auto-admits it with **no knock at all**, so there is no request to decline.
   The only unattended path, and what every commercial meeting bot does.
2. **The host turns on Quick access** (in the call: Host controls → Quick
   access), which removes admission for everyone. No bot account needed, but it
   is per-host and applies to human guests too. **Untested here.**
3. **Record with the platform's own recorder and upload the file** — the whole
   transcribe → notes → action-item pipeline is identical for an upload.

Superseded advice, for anyone who read this file earlier: "join the call first,
then click Admit" **does not work**, and neither does the Workspace-only
"Anyone with the link can ask to join" setting on a personal-account meeting.

### Signing the bot into a Google account

**A Google Account here is a login, not a mailbox.** Signing up with an address
you already own (Google's "use your existing email" path) creates an identity and
deliberately does *not* create Gmail: no MX change, no mail routed to Google, no
second inbox. If that address is on Microsoft 365, it stays on Microsoft 365. The
account exists purely so the bot's Chrome has something to sign in with, since
Meet only treats signed-in participants as admittable. Use the **same** address
you will put on calendar invites — auto-admit matches one against the other.

Use a **dedicated account**, never your own: repeated automated logins on a
personal account invite a security flag. 2-step verification is **not** a
blocker; it only rules out the scripted endpoint, and
`POST /google-login/interactive` exists to finish it by hand over VNC once.
`MEET_PROFILE_DIR` (default `/profile`, its own volume) holds the profile, so
signing in is a one-time act that survives redeploys.

```bash
# Scripted (a plain password account, no 2FA)
curl -s -X POST -H "Authorization: Bearer $MEETING_BOT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"email":"notetaker@yourdomain.com","password":"…"}' \
  localhost:8095/google-login | jq

# Who is signed in right now?
curl -s -H "Authorization: Bearer $MEETING_BOT_TOKEN" \
  localhost:8095/google-login | jq
```

Credentials can also live in `MEET_GOOGLE_EMAIL` / `MEET_GOOGLE_PASSWORD` and
the body omitted. Every step screenshots to `/data/google-login-*.png`, so a
failure names the wall it hit: `blocked` ("this browser or app may not be
secure" — scripted login can never pass, use the interactive path) versus
`needs_human` (2FA / "verify it's you" — the password worked, finish it by
hand).

For anything needing a human (2FA, passkey, consent), open the browser and do
it yourself over VNC:

```bash
curl -s -X POST -H "Authorization: Bearer $MEETING_BOT_TOKEN" \
  localhost:8095/google-login/interactive | jq   # holds the browser open 10 min
```

## Debugging on the VPS

Three affordances, in increasing order of directness:

**1. Watch the browser live (VNC).** The container runs Chrome headful under
Xvfb; with `MEET_VNC=1` it also runs x11vnc + noVNC, so you can watch a join as
it happens and click things yourself. Loopback-only — it is an unauthenticated
view of a browser holding a Google session, so reach it through a tunnel:

```bash
# on the VPS: set MEET_VNC=1 in /opt/acb/app/.env, then
docker restart acb-meeting-bot

# from your machine:
ssh -L 6080:127.0.0.1:6080 acb@<vps>
# then open http://localhost:6080/vnc.html
```

**2. Edit the worker in place, no rebuild.** `infra/docker-compose.yml`
bind-mounts `apps/services/meeting_bot/app` over the image's copy, so an edit
on the box takes effect on restart:

```bash
sudo -e /opt/acb/app/apps/services/meeting_bot/app/meet.py
docker restart acb-meeting-bot && docker logs -f acb-meeting-bot
```
Git is still the source of truth — the next deploy overwrites it (`git reset
--hard`), so port anything that works back into a commit.

**3. Post-mortem evidence** (always on, no setup): every failure path snapshots
the page and persists the job.

```bash
docker exec acb-meeting-bot ls -lat /data   # screenshots + {job}.state.json
docker logs --tail 100 acb-meeting-bot      # step-by-step: goto, name, join
curl -s -H "Authorization: Bearer $MEETING_BOT_TOKEN" \
  localhost:8095/bots/<id>/diagnostics | jq
```

## Status & honest caveats

- **Google Meet only** in this MVP, and Zoom/Teams links are refused up front
  rather than dispatched into a Meet-shaped automation. Teams is the easier
  second platform (anonymous web join is still allowed by default policy);
  **Zoom's Meeting SDK now explicitly forbids notetaker bots** and its web
  client is CAPTCHA-walled, so for Zoom the sanctioned routes are RTMS (for
  meetings you host) or a managed provider. See
  `project-docs/specs/meeting_bot_platform_plan.md`.
- **Joining unattended requires a signed-in bot account.** Anonymous joining is
  not a degraded mode, it is a blocked one — see the wall section above.
- **Browser automation is inherently brittle.** Meet's DOM is not a public API;
  the join selectors in `app/meet.py` are best-effort and **will need occasional
  tuning** as Meet's UI changes. This is the unavoidable maintenance cost of any
  meeting bot (the reason managed services like Recall.ai exist). Verify against
  a real meeting on the deployment box and adjust selectors as needed.
- **Consent:** the bot joins under a visible name; recording participants may
  legally require their consent depending on jurisdiction. Get it.
- Not yet load-tested for many concurrent instances; start with 1–2 per host and
  size up.

## What has actually been run (and what hasn't)

Verified by running the service against a real PulseAudio/Xvfb/Chromium stack —
not inferred:

| Verified | Result |
|---|---|
| Audio stack (`entrypoint.sh`) | `meet` + `vmic` null sinks and their monitors come up. |
| **Recording path** (`_start_ffmpeg`) | A 440 Hz tone played into `meet` was recovered from `meet.monitor` as 16 kHz mono Opus — decoded back at 440 Hz, RMS ≈ 2050. The exact production ffmpeg args. |
| **Speak path** (virtual mic) | Audio played into the `vmic` sink is captured from `vmic.monitor` — the source Chrome uses as its microphone. |
| **Energy VAD** (`LIVE_VAD_RMS`) | Real PCM: speech frames RMS ≈ 1700, silence 0.0. The `300` default separates them cleanly. |
| **Live streaming + embeddings** | Against a stub ASR + callback: 16 s of real audio streamed, 46 segments forwarded, **42 carrying a per-utterance embedding**. |
| HTTP contract | `/health`, bearer auth (401 without it), `POST /bots` → status lifecycle, clean `failed` + error text on a bad join. |
| Chromium launch | Launches headful under Xvfb and drives `page.goto` (needs `CHROME_EXECUTABLE` when the box's browser build isn't this Playwright's). |

**Still unverified — needs a real meeting:** the Google Meet **join flow itself**
(`_maybe_fill_name` / `_click_join` / `_await_admission` selectors) and the
end-to-end capture of an actual call. Meet's DOM is not a public API, so expect
to tune those selectors on first run. A real `EMBED_CMD` model (CAM++/pyannote)
and a `TTS_CMD` voice also still need to be installed and pointed at.

## When a join fails

Since the selectors *will* drift, the worker is built to explain itself rather
than to be guessed at. Every failure path captures a snapshot before raising:
the page URL and title, a body excerpt, **and the label of every button Meet
actually rendered** — which is what tells you the next selector to write.

```bash
# What the worker did, step by step
docker logs --tail 100 acb-meeting-bot

# Structured detail for one bot (controls list, page text)
curl -s -H "Authorization: Bearer $MEETING_BOT_TOKEN" \
  localhost:8095/bots/<bot-id>/diagnostics | jq

# The green room exactly as the bot saw it
curl -s -H "Authorization: Bearer $MEETING_BOT_TOKEN" \
  localhost:8095/bots/<bot-id>/screenshot -o /tmp/bot.png
```

From Metorite itself the same detail is at
`GET /notes/meetings/{meeting_id}/bot/diagnostics`, and the failure text now
shows on the Notes screen for 30 minutes after it happens.

The failures that look identical from outside, and their fixes:

| Symptom | What it means | Fix |
|---|---|---|
| `Nobody admitted the notetaker within 150s` | It knocked and waited; no one answered. Distinct from a refusal — this one means the knock *was* delivered. | Click **Admit** when it knocks, or raise `MEET_JOIN_TIMEOUT`. |
| `Google Meet declined the notetaker's request to join` | Meet's bot check auto-declined it, usually within seconds. The host is never asked, so waiting won't help. | Sign the bot in and invite its address (see above), or use Quick access, or upload a recording. |
| `already in a meeting` (409) | Not a fault: one Chrome, one profile, one call at a time. | Wait for the current meeting, or run another worker instance. |
| `Nobody else ever joined…` | `end_reason=noone_joined` — it sat alone and left. **The audio stack is fine.** | Check the meeting actually happened and the link was right. |
| `The host removed the notetaker…` | `end_reason=kicked` — someone ejected it, quite possibly on purpose. | Ask before recording; re-send if it was accidental. |
| `Meet showed its landing page` | Meet decided the visitor was a bot (or the link invalid) and client-side-rendered its *marketing* page — no green room, no join button. The 2026-07-29 production failure. | The worker now launches Chrome with automation hidden (`--disable-blink-features=AutomationControlled`, no `--enable-automation`) and canonicalises invite links to the bare `meet.google.com/xxx-yyyy-zzz?hl=en` form, which prevents the known triggers. If it recurs, Meet has tightened detection — check the screenshot and update `looks_like_landing` / the launch flags. |
| `No join button on the meeting page` | A selector missed, or a dialog covered it. | Read the `controls` list in the error — it names the buttons that *were* there. |

Every status change is also mirrored to `/data/{job}.state.json`, so a worker
restart (deploys recreate the container) no longer 404s its bots or destroys
the evidence: after a restart, `GET /bots/{id}` serves the persisted terminal
state, and an interrupted in-flight job comes back as a clean `failed` with an
explanation. The deploy itself also refuses to recreate the container while
`/health` reports an active bot. From the Notes UI, a failed join now shows on
the meeting page with the error, the button list, and the screenshot.
