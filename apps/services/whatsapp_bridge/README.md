# whatsapp_bridge

The **unofficial WhatsApp transport** for Metorite — pairs a **personal
number** by QR code (via the [whatsmeow](https://github.com/tulir/whatsmeow)
multi-device library) and streams its messages into the same WhatsApp app you
already use for a Cloud API number. It exists so you can manage a personal line
**without** going through Meta's WhatsApp Business API, app review, phone-number
migration, or the 24-hour messaging window.

> [!WARNING]
> **This is against WhatsApp's Terms of Service.** A personal number driven by
> an unofficial multi-device client **can be banned by WhatsApp** at any time.
> Use it only for a personal line you're willing to risk — never a business's
> primary number. When you're ready for a supported, ban-proof setup, connect a
> number through the Cloud API path instead (the app supports both side by side).

## Where it sits

```
  Your phone ──scan QR──▶ whatsapp_bridge (this service, holds the session)
                                │  normalized message JSON  ▲ /send /media
                                ▼                           │
                       gateway  POST /whatsapp/bridge/ingest │
                                │                            │
                    persist_sync_result + post-sync hooks    │
                                ▼                            │
                    the SAME triage brain (Reply Zero, intent, drafting,
                    search, pulse …) a Cloud API number gets
```

The bridge holds the WhatsApp session; the gateway holds **no** WhatsApp session
and never talks to WhatsApp. They authenticate to each other with a shared
secret (`WHATSAPP_BRIDGE_SECRET`). Inbound messages are normalized *here* into
the exact `SyncResult` shape the gateway's
`transport/bridge.py :: parse_bridge_payload` already consumes — so a personal
number reuses the entire vertical unchanged (`provider = 'whatsmeow'`).

## HTTP API (the gateway calls these)

| Method & path            | Body                                 | Returns            |
|--------------------------|--------------------------------------|--------------------|
| `POST /session`          | `{session}`                          | `{status, qr}`     |
| `GET  /session/{id}`     | —                                    | `{status, qr}`     |
| `POST /send`             | `{session,to,body,reply_to}`         | `{id}`             |
| `POST /media`            | `{session,media_id}`                 | raw bytes          |
| `POST /read`             | `{session,message_id,chat,sender}`   | `{ok}`             |
| `GET  /health`           | —                                    | `{ok}`             |

Voice calling adds `/call*` and `/calls*` — see [Voice calls](#voice-calls).

`session` is the `wa_accounts.id` (UUID) the gateway assigns when the user starts
pairing. `qr` is a ready-to-render `data:image/png;base64,…` of the current
pairing code — the frontend shows it with a plain `<img>`, and the phone scans it
under **WhatsApp → Linked devices → Link a device**.

All routes except `/health` require the `X-Bridge-Secret` header when a secret is
configured.

## Configure

Copy `.env.example` → `.env`. Key vars:

- `WHATSAPP_BRIDGE_ADDR` — listen address (default `:8790`).
- `WHATSAPP_BRIDGE_GATEWAY_URL` — the gateway base URL to stream messages to.
- `WHATSAPP_BRIDGE_SECRET` — shared secret; set the **same** value on the gateway.
- `WHATSAPP_BRIDGE_STORE` — sqlite path for the paired session (**treat as a
  secret**: whoever holds this file can send as the paired number).
- `WHATSAPP_BRIDGE_CALL_RECORD_DIR` — where call audio is written (default
  `./call-recordings`). Empty disables recording; calls still connect.
- `WHATSAPP_BRIDGE_CALL_REAP_MINS` — how long an ended call stays queryable
  before it's dropped from memory (default `60`).
- `WHATSAPP_BRIDGE_CALL_LOG_LEVEL` — verbosity of meowcaller's own media
  diagnostics (`info` default; `debug` for the full RTP/SRTP trace). This is
  where a connected-but-silent call is diagnosed: look for `first RTP sent to
  relay`, `relay silent after allocate`, `first authenticated peer SRTCP
  received`, and `peer SRTCP failed authentication`.
- `WHATSAPP_BRIDGE_CALL_RETENTION_DAYS` — days of recorded audio to keep
  (default `7`; `0` keeps forever). Recording runs at roughly **115 MB per hour
  of call**, so the sweep is what stops a busy line filling the disk.

On the gateway set the matching pair:

```
WHATSAPP_BRIDGE_URL=http://localhost:8790     # where this service listens
WHATSAPP_BRIDGE_SECRET=<same long random secret>
```

## Run

```bash
# from apps/services/whatsapp_bridge
cp .env.example .env && $EDITOR .env
go run .            # or: go build -o whatsapp_bridge . && ./whatsapp_bridge
```

Or with Docker (pure-Go, CGO-free static image):

```bash
docker build -t cc-whatsapp-bridge .
docker run --rm -p 8790:8790 \
  -e WHATSAPP_BRIDGE_GATEWAY_URL=http://host.docker.internal:8000 \
  -e WHATSAPP_BRIDGE_SECRET=your-secret \
  -v cc-wa-bridge:/data \
  cc-whatsapp-bridge
```

Then in the app: **Integrations → WhatsApp → Connect a personal number**, scan
the QR, and the number goes live. Sessions survive restarts (re-connected from
the sqlite store on boot).

## Voice calls

The bridge can place and answer WhatsApp voice calls — 1:1 and group — from the
paired number, the way WhatsApp Desktop does. whatsmeow carries call
*signalling* only, so the media half comes from
[meowcaller](https://github.com/purpshell/meowcaller): a pure-Go WhatsApp VoIP
stack (MLow codec, SRTP, RTP, relay) that wraps the same session. It's attached
in `newClient` **before** `Connect`, which the library requires so its low-level
`<call>` interception is in place before the receive loop starts.

```
POST /call         {session, to}                     # 1:1
POST /call         {session, group_id}               # ring a WhatsApp group
POST /call         {session, targets:[a,b,…]}        # ad-hoc group call
POST /call/hangup  {session, call_id}
POST /call/answer  {session, call_id}                # inbound is never auto-answered
POST /call/reject  {session, call_id}
GET  /calls?session=<id>
GET  /calls/diagnostics?session=<id>        # can this number place a call, and if not why
GET  /calls/recording?session=&call_id=     # the call's WAV, resolved via the registry
GET  /call/audio?session=&call_id=          # WebSocket: duplex mic/speaker (below)
```

Each call's decoded peer audio is recorded to
`$WHATSAPP_BRIDGE_CALL_RECORD_DIR/<call-id>.wav` as 16 kHz mono — the format
`acb_stt` already transcribes, which is what makes this the media seam for the
note taker. State transitions are pushed to the gateway at
`/whatsapp/bridge/call-event`.

In the app: **WhatsApp → Calls**.

### Two-way audio

`GET /call/audio?session=&call_id=` upgrades to a WebSocket carrying duplex
audio, so a browser can be the call's microphone and speakers:

```
browser mic  ──► WS ──► wsSource ──► Player ──► MLow ──► SRTP ──► peer
browser spkr ◄── WS ◄── callSink listener ◄── decoded peer frames
```

The wire format is raw little-endian **int16 mono at 16 kHz, one 960-sample
(60 ms) frame per binary message** — meowcaller's native framing, so neither end
resamples or reframes. The gateway proxies this socket (the bridge stays
localhost-only) and the browser authenticates with a short-lived signed token,
since a WebSocket can't carry the internal bearer.

Attaching a browser is optional: with none attached the call sends silence,
which is what holds the relay bridge open. Recording is unaffected either way.

> [!WARNING]
> Placing calls from an unofficial client carries the same ban risk as the rest
> of this service, and arguably more — automated calling is conspicuous. Group
> calling in meowcaller is marked **experimental**. Recording other people is
> regulated in many places: tell them.

## History backfill

On link, WhatsApp pushes a history-sync payload to the new device — the same
mechanism that populates WhatsApp Desktop's recent chats. The bridge handles the
`events.HistorySync` event, normalizes each conversation's messages, and streams
them to `/whatsapp/bridge/ingest` as a **backfill** batch: they're persisted (and
searchable/triaged) but the post-sync hooks are skipped, so **auto-reply never
fires on months-old messages**. Once the payloads stop arriving (~90s debounce),
the bridge calls `/whatsapp/bridge/reclassify` once to compute reply statuses.

- `WHATSAPP_BRIDGE_FULL_HISTORY=true` (default) requests the ~1-year desktop-profile
  sync; `false` gives the default ~3-month "recent" window.
- It's **recent history, not a full archive**: ~1 year is reliable, ~3 years is the
  server ceiling, and you only get what your phone still stores. Older-than-retention
  messages won't come, and **old media often 404s** (expired from WhatsApp's CDN) —
  the text/metadata still lands; the media download just fails.
- The bootstrap sync is delivered **once**, at link time — it can't be re-requested;
  the only reset is unlink + relink.
- On-demand "load older" pagination (`BuildHistorySyncRequest`) is a possible
  follow-up; it's best-effort (needs the phone online and WhatsApp may drop
  companion-device requests), so it's intentionally not wired in yet.

## Notes / limits

- Personal WhatsApp has **no templates** and **no 24-hour window** — the composer
  always sends free-form text. `send_template` is unsupported by design.
- `group_subject` is left to the gateway (it names group chats from the chat
  record); the bridge doesn't fetch group metadata per message.
- Media is downloaded lazily: the inbound event caches the message proto keyed by
  message id, and `/media` re-downloads on demand — matching the Cloud API
  provider's lazy `download_media`.
- Pure Go, no CGO: uses `modernc.org/sqlite`, so it cross-compiles and ships as a
  static binary.
