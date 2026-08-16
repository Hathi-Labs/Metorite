"use client";

/**
 * WhatsApp voice calls — the dialer.
 *
 * Places 1:1 and group calls from a QR-paired PERSONAL number, the way
 * WhatsApp Desktop does. Calling rides the whatsmeow bridge + meowcaller, so it
 * exists only on bridge accounts: Meta's Cloud API calling product is 1:1-only,
 * has to be enabled per-number by Meta, and has no media leg here yet.
 *
 * This is the media seam for the note taker — every call's audio is recorded to
 * a WAV on the bridge, which is what the transcription pipeline will consume.
 * See project-docs/specs/whatsapp_calls_note_taker.md.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  callAction,
  fetchAccounts,
  fetchCallDiagnostics,
  fetchCallEvents,
  fetchCalls,
  placeCall,
} from "../lib/api";
import { callRecordingUrl } from "../lib/types";
import type { WaAccount, WaCall, WaCallDiagnostics } from "../lib/types";
import { prepareCallAudio, readAudioLog } from "../lib/callAudio";
import type { CallAudioState, PreparedCallAudio } from "../lib/callAudio";

/** Phases where the call is still going — drives polling and the hangup button. */
const LIVE_PHASES = new Set([
  "idle",
  "calling",
  "ringing",
  "connecting",
  "active",
  "waiting_room",
]);

const PHASE_LABEL: Record<string, string> = {
  idle: "Starting",
  calling: "Calling",
  ringing: "Ringing",
  connecting: "Connecting",
  active: "In call",
  waiting_room: "Waiting room",
  ended: "Ended",
};

const isLive = (c: WaCall) => LIVE_PHASES.has(c.phase);

function phaseTone(phase: string): string {
  if (phase === "active") return "bg-success/15 text-success";
  if (phase === "ended") return "bg-secondary text-muted-foreground";
  return "bg-warning/15 text-warning";
}

/** Elapsed mm:ss. `now` is passed in (not read from the clock here) so the
 *  value is a pure function of state — no server/client hydration mismatch. */
/** Turn the plumbing's generic failures into something you can act on.
 *
 *  "gateway unreachable" comes from the Next proxy when its fetch to the
 *  FastAPI gateway throws or hits the 30s abort — it says nothing about which
 *  service is actually broken, and it's the message users hit first. */
function explainError(raw: string): string {
  const e = raw.toLowerCase();
  if (e.includes("gateway unreachable")) {
    return (
      "Couldn't reach the Metorite gateway (the request timed out or was " +
      "refused). The call may not have been placed. Check the gateway is up — " +
      "`systemctl status acb-gateway` — and watch `journalctl -u acb-gateway -f` " +
      "while retrying."
    );
  }
  if (e.includes("bridge isn't reachable") || e.includes("bridge unreachable")) {
    return (
      "The gateway is up but can't reach the WhatsApp bridge. Check " +
      "`systemctl status acb-whatsapp-bridge` and that WHATSAPP_BRIDGE_URL " +
      "points at it."
    );
  }
  return raw;
}

function clockFrom(iso: string | undefined, now: number): string {
  if (!iso || !now) return "";
  const started = new Date(iso).getTime();
  if (Number.isNaN(started)) return "";
  const secs = Math.max(0, Math.floor((now - started) / 1000));
  const m = String(Math.floor(secs / 60)).padStart(2, "0");
  const s = String(secs % 60).padStart(2, "0");
  return `${m}:${s}`;
}

export default function WhatsAppCallsPage() {
  const [accounts, setAccounts] = useState<WaAccount[] | null>(null);
  const [accountId, setAccountId] = useState("");
  const [calls, setCalls] = useState<WaCall[]>([]);
  const [bridgeUp, setBridgeUp] = useState(true);
  const [target, setTarget] = useState("");
  const [mode, setMode] = useState<"direct" | "group">("direct");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diag, setDiag] = useState<WaCallDiagnostics | null>(null);
  const [showDiag, setShowDiag] = useState(false);
  const [prefillName, setPrefillName] = useState("");
  const [report, setReport] = useState<string | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [audioState, setAudioState] = useState<CallAudioState>("idle");
  const [audioCallId, setAudioCallId] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const audioRef = useRef<PreparedCallAudio | null>(null);
  /** Whether the attached call has appeared in a poll yet — see the teardown
   *  effect. Without this, attaching races the poll and always loses. */
  const seenAudioCallRef = useRef(false);
  // Starts at 0 so the first server render has no clock to mismatch on; the
  // poll below fills it in once mounted.
  const [now, setNow] = useState(0);

  // Calling only exists on the bridge transport; a Cloud API number in this
  // picker would just fail at the gateway, so it never gets offered.
  const callable = useMemo(
    () => (accounts ?? []).filter((a) => a.provider === "whatsmeow"),
    [accounts]
  );

  // Prefill from a chat's "Call" link. Read off window.location rather than
  // useSearchParams so the page needs no Suspense boundary. Prefilled, never
  // auto-dialled — a URL that places a call on navigation is too easy to trip.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const to = q.get("to");
    if (!to) return;
    setTarget(to);
    setPrefillName(q.get("name") || "");
    if (to.endsWith("@g.us")) setMode("group");
  }, []);

  useEffect(() => {
    void fetchAccounts().then((rows) => {
      setAccounts(rows);
      const bridge = rows.filter((a) => a.provider === "whatsmeow");
      const paired = bridge.find((a) => a.sync_status === "live") ?? bridge[0];
      if (paired) setAccountId(paired.id);
    });
  }, []);

  const refresh = useCallback(async () => {
    if (!accountId) return;
    const res = await fetchCalls(accountId);
    setCalls(res.calls);
    setBridgeUp(res.bridge_reachable);
  }, [accountId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll while anything is live, and re-render every second so the in-call
  // timer counts even when the phase itself hasn't changed.
  const anyLive = calls.some(isLive);
  useEffect(() => {
    if (!accountId) return;
    const every = anyLive ? 1500 : 8000;
    const id = setInterval(() => {
      setNow(Date.now());
      void refresh();
    }, every);
    return () => clearInterval(id);
  }, [accountId, anyLive, refresh]);

  async function dial() {
    const raw = target.trim();
    if (!raw || !accountId) return;
    setBusy(true);
    setError(null);

    // Mic FIRST, while the click's user activation is still valid, then
    // ringback immediately — so pressing Call is audible straight away instead
    // of silent until the far end answers. A refused mic doesn't block the
    // call; it just means this leg is listen-only.
    const prepared = await prepareAudio();
    prepared?.ringback(true);

    const parts = raw
      .split(/[,\n]/)
      .map((p) => p.trim())
      .filter(Boolean);

    const res = await placeCall(
      mode === "group"
        ? raw.includes("@g.us")
          ? { accountId, groupId: raw }
          : { accountId, targets: parts }
        : { accountId, to: parts[0] ?? raw }
    );

    setBusy(false);
    if (!res.ok) {
      // No call means nothing to ring for — don't leave a tone playing into a
      // failure, and don't leave the mic open either.
      stopAudio();
      setError(explainError(res.error ?? "The call couldn't be placed."));
      return;
    }
    setTarget("");

    // Join the call's audio immediately, while it's still ringing. Waiting for
    // a phase transition is what previously left the caller with no way in.
    if (prepared && res.data?.call_id) {
      await attachAudio(prepared, res.data);
    } else if (prepared) {
      prepared.ringback(false);
    }
    void refresh();
  }

  const stopAudio = useCallback(() => {
    audioRef.current?.stop();
    audioRef.current = null;
    seenAudioCallRef.current = false;
    setAudioCallId(null);
    setAudioState("idle");
    setMuted(false);
  }, []);

  const onAudioState = useCallback((s: CallAudioState, detail?: string) => {
    setAudioState(s);
    if (s === "idle" && detail) setError(detail);
  }, []);

  /** Open the mic + audio graph. Must run inside the click, before any await
   *  on the network — getUserMedia needs the user activation. */
  async function prepareAudio(): Promise<PreparedCallAudio | null> {
    stopAudio();
    try {
      const prepared = await prepareCallAudio({ accountId, onState: onAudioState });
      audioRef.current = prepared;
      return prepared;
    } catch (e) {
      setAudioState("failed");
      setError(explainError(e instanceof Error ? e.message : String(e)));
      return null;
    }
  }

  async function attachAudio(prepared: PreparedCallAudio, call: WaCall) {
    try {
      await prepared.attach(call.call_id);
      seenAudioCallRef.current = false;
      setAudioCallId(call.call_id);
    } catch (e) {
      prepared.ringback(false);
      setAudioState("failed");
      setError(explainError(e instanceof Error ? e.message : String(e)));
    }
  }

  /** Manual re-join, for a call this page didn't place (or after leaving). */
  async function joinAudio(call: WaCall) {
    setError(null);
    const prepared = await prepareAudio();
    if (prepared) await attachAudio(prepared, call);
  }

  function toggleMute() {
    const next = !muted;
    setMuted(next);
    audioRef.current?.setMuted(next);
  }

  // A call that ended (or was hung up elsewhere) must not leave the mic open.
  //
  // Only act once the call has actually been SEEN in a poll. We attach the
  // moment the call is placed, which is before the next poll lands — so
  // treating "not in the list" as "ended" would tear the audio down
  // milliseconds after connecting, every single time.
  useEffect(() => {
    if (!audioCallId) return;
    const still = calls.find((c) => c.call_id === audioCallId);
    if (still) {
      seenAudioCallRef.current = true;
      if (!isLive(still)) stopAudio();
      return;
    }
    // Vanished after we'd seen it — reaped or hung up elsewhere.
    if (seenAudioCallRef.current) stopAudio();
  }, [calls, audioCallId, stopAudio]);

  // Once the call is observably up, the local ringback has done its job. It
  // also stops on the first real audio frame; whichever happens first wins,
  // so a call that connects without media doesn't ring forever.
  useEffect(() => {
    if (!audioCallId) return;
    const call = calls.find((c) => c.call_id === audioCallId);
    if (call?.media_ready || call?.phase === "active") {
      audioRef.current?.ringback(false);
    }
  }, [calls, audioCallId]);

  // Leaving the page mid-call is the other way to strand an open microphone.
  useEffect(() => () => audioRef.current?.stop(), []);

  async function runDiagnostics() {
    if (!accountId) return;
    setShowDiag(true);
    setDiag(await fetchCallDiagnostics(accountId));
  }

  /**
   * Assemble everything needed to diagnose a call into one pasteable block:
   * what the server saw, what the browser saw, and the environment. Deliberately
   * plain text — a bug report should survive a chat window.
   */
  async function buildReport(call: WaCall): Promise<string> {
    const [timeline, readiness] = await Promise.all([
      fetchCallEvents(accountId, call.call_id),
      fetchCallDiagnostics(accountId),
    ]);
    const lines: string[] = [
      "=== WhatsApp call debug report ===",
      `generated: ${new Date().toISOString()}`,
      `call: ${call.call_id}  (${call.direction} ${call.kind})`,
      `phase: ${call.phase}  media_ready: ${call.media_ready ?? false}  ` +
        `audio: ${(call.audio_seconds ?? 0).toFixed(1)}s`,
      `client audio state: ${audioState}${muted ? " (muted)" : ""}`,
      "",
      "--- readiness ---",
      `bridge_reachable=${readiness.bridge_reachable} connected=${readiness.connected} ` +
        `logged_in=${readiness.logged_in} caller_ready=${readiness.caller_ready}`,
      `verdict: ${readiness.verdict}`,
      "",
      "--- server timeline ---",
      ...(timeline.bridge_reachable
        ? timeline.events.length
          ? timeline.events.map((e) => `${e.at}  [${e.kind}] ${e.detail}`)
          : ["(no events recorded)"]
        : ["(bridge unreachable — no server timeline)"]),
      "",
      "--- browser timeline ---",
      ...(readAudioLog().length
        ? readAudioLog().map((e) => `${e.at}  ${e.detail}`)
        : ["(no browser audio events — audio was never started)"]),
    ];
    // Peer numbers are personal data and add nothing to a protocol bug.
    return lines.join("\n");
  }

  async function copyReport(call: WaCall) {
    setReportBusy(true);
    try {
      const text = await buildReport(call);
      setReport(text);
      try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2500);
      } catch {
        /* clipboard blocked — the text is on screen to copy by hand */
      }
    } finally {
      setReportBusy(false);
    }
  }

  async function act(action: "hangup" | "answer" | "reject", call: WaCall) {
    setError(null);
    const res = await callAction(action, accountId, call.call_id);
    if (!res.ok) setError(explainError(res.error ?? `Couldn't ${action} that call.`));
    void refresh();
  }

  const live = calls.filter(isLive);
  const recent = calls.filter((c) => !isLive(c));

  // ── empty states ───────────────────────────────────────────────────────────

  if (accounts === null) {
    return (
      <div className="grid place-items-center p-12 text-muted-foreground">
        <Icon name="Loader2" className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (callable.length === 0) {
    return (
      <div className="mx-auto max-w-lg p-6">
        <h1 className="mb-2 text-lg font-bold">WhatsApp calls</h1>
        <div className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">
          <p className="mb-2 font-medium text-foreground">
            No personal number is paired.
          </p>
          <p className="leading-relaxed">
            Calling runs over the QR-paired personal (bridge) transport — a Cloud
            API business number can&apos;t place calls from here. Pair a number
            under <span className="font-mono text-xs">Numbers → Personal WhatsApp</span>{" "}
            and come back.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl p-4 sm:p-6">
      <header className="mb-4">
        <h1 className="flex items-center gap-2 text-lg font-bold text-foreground">
          <Icon name="Phone" className="h-4 w-4 text-primary" />
          WhatsApp calls
        </h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Place 1:1 and group calls from your paired personal number. Audio is
          recorded on the bridge — the raw material for meeting notes. The most
          reliable way to dial is the <b>Call</b> button in a chat: it passes
          that contact&apos;s WhatsApp id instead of a typed number.
        </p>
      </header>

      {/* The ToS posture is the first thing on the page, not a footnote: an
          automated client placing calls is the fastest way to lose a number. */}
      <div className="mb-3 flex items-start gap-2 rounded-xl bg-secondary px-3 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
        <Icon name="Headphones" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          Your mic and speakers join automatically when you place a call, so you
          hear it ringing straight away. <b className="text-foreground">Use
          headphones</b> — echo cancellation is best-effort on this path, and
          without them the other person hears themselves. Every call is also
          recorded server-side and playable from <b>Recent</b>.
        </span>
      </div>

      <div className="mb-4 flex items-start gap-2 rounded-xl bg-warning/10 px-3 py-2.5 text-[11px] leading-relaxed text-warning">
        <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          Calling from an unofficial client is outside WhatsApp&apos;s Terms of
          Service and the number can be banned. Use a number you&apos;re willing
          to lose — never the company&apos;s main line. Everyone on the call
          should know it&apos;s being recorded.
        </span>
      </div>

      {!bridgeUp && (
        <div className="mb-4 rounded-xl bg-destructive/10 px-3 py-2.5 text-xs text-destructive">
          The WhatsApp bridge isn&apos;t reachable, so calls can&apos;t be placed.
          Check that the <span className="font-mono">whatsapp_bridge</span>{" "}
          service is running.
        </div>
      )}

      {/* dialer */}
      <div className="rounded-2xl border border-border bg-card p-4">
        {callable.length > 1 && (
          <select
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            className="mb-3 w-full rounded-lg border border-border bg-background px-3 py-2 text-xs"
          >
            {callable.map((a) => (
              <option key={a.id} value={a.id}>
                {a.display_name || a.phone_number || "Personal WhatsApp"}
              </option>
            ))}
          </select>
        )}

        <div className="mb-3 inline-flex rounded-lg bg-secondary p-0.5 text-xs">
          {(["direct", "group"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 tech-transition ${
                mode === m
                  ? "bg-card font-medium text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {m === "direct" ? (
                <Icon name="Phone" className="h-3 w-3" />
              ) : (
                <Icon name="Users" className="h-3 w-3" />
              )}
              {m === "direct" ? "1:1" : "Group"}
            </button>
          ))}
        </div>

        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void dial();
            }}
            placeholder={
              mode === "direct"
                ? "+919876543210  (or open a chat and press Call)"
                : "Group id (…@g.us), or two or more numbers, comma-separated"
            }
            className="flex-1 rounded-lg border border-border bg-background px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <Button size="none" onClick={() => void dial()} disabled={busy || !target.trim() || !bridgeUp} className="gap-1.5 px-4 py-2 text-xs">
            {busy ? (
              <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Icon name="Phone" className="h-3.5 w-3.5" />
            )}
            {busy ? "Calling…" : "Call"}
          </Button>
        </div>

        {mode === "group" && (
          <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
            A group call needs a WhatsApp group id, or at least two numbers for
            an ad-hoc call. Group calling is experimental — expect rough edges.
          </p>
        )}

        {prefillName && (
          <p className="mt-2 text-[10px] text-muted-foreground">
            Calling <b className="text-foreground">{prefillName}</b> from their
            chat — using their WhatsApp id directly, so there&apos;s no number to
            mistype.
          </p>
        )}

        {error && (
          <div className="mt-2 rounded-lg bg-destructive/10 px-2.5 py-2 text-[11px] text-destructive">
            <p>{error}</p>
            <button
              onClick={() => void runDiagnostics()}
              className="mt-1.5 inline-flex items-center gap-1 rounded-md bg-destructive/15 px-2 py-1 font-medium hover:bg-destructive/25 tech-transition"
            >
              <Icon name="Stethoscope" className="h-3 w-3" />
              Check what&apos;s wrong
            </button>
          </div>
        )}
      </div>

      {/* Diagnostics — an offer that times out leaves no error to inspect, so
          the useful question is which precondition failed. */}
      {showDiag && (
        <div className="mt-3 rounded-2xl border border-border bg-card p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
              <Icon name="Stethoscope" className="h-3.5 w-3.5" />
              Calling readiness
            </h2>
            <Button variant="text" size="none" layout="" onClick={() => void runDiagnostics()} className="text-[10px]">
              Re-check
            </Button>
          </div>
          {diag === null ? (
            <Icon name="Loader2" className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : (
            <>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
                {(
                  [
                    ["Bridge reachable", diag.bridge_reachable],
                    ["Session exists", diag.session_exists],
                    ["Connected to WhatsApp", diag.connected],
                    ["Logged in", diag.logged_in],
                    ["Calling stack attached", diag.caller_ready],
                  ] as const
                ).map(([label, ok]) => (
                  <div key={label} className="flex items-center gap-1.5">
                    {ok ? (
                      <Icon name="CheckCircle2" className="h-3.5 w-3.5 shrink-0 text-success" />
                    ) : (
                      <Icon name="XCircle" className="h-3.5 w-3.5 shrink-0 text-destructive" />
                    )}
                    <dt className="text-muted-foreground">{label}</dt>
                  </div>
                ))}
              </dl>
              <p className="mt-2 rounded-lg bg-secondary px-2.5 py-1.5 text-[11px] text-foreground">
                {diag.verdict}
              </p>
              <div className="mt-2 space-y-0.5 text-[10px] text-muted-foreground">
                {diag.own_jid && (
                  <p>
                    Calling as <span className="font-mono">{diag.own_jid}</span>
                    {diag.push_name ? ` (${diag.push_name})` : ""}
                  </p>
                )}
                <p>Active calls: {diag.active_calls}</p>
                {diag.recording_dir && (
                  <p>
                    Recordings:{" "}
                    <span className="font-mono">{diag.recording_dir}</span>
                  </p>
                )}
              </div>
              <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
                Everything green but the call still times out? The offer is
                reaching WhatsApp and going unanswered — watch{" "}
                <code className="font-mono">
                  journalctl -u acb-whatsapp-bridge -f
                </code>{" "}
                while you dial; it logs the number actually dialled and every
                phase transition with timings.
              </p>
            </>
          )}
        </div>
      )}

      {/* live calls */}
      {live.length > 0 && (
        <section className="mt-5">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            On a call
          </h2>
          <div className="space-y-2">
            {live.map((c) => (
              <div
                key={c.call_id}
                className="flex items-center gap-3 rounded-xl border border-border bg-card p-3"
              >
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-primary/15 text-primary">
                  {c.direction === "incoming" ? (
                    <Icon name="PhoneIncoming" className="h-4 w-4" />
                  ) : c.kind === "group" ? (
                    <Icon name="Users" className="h-4 w-4" />
                  ) : (
                    <Icon name="Phone" className="h-4 w-4" />
                  )}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-foreground">
                    {c.peer || c.targets?.join(", ") || "Unknown"}
                  </p>
                  <p className="flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span className={`rounded px-1.5 py-0.5 ${phaseTone(c.phase)}`}>
                      {PHASE_LABEL[c.phase] ?? c.phase}
                    </span>
                    {c.phase === "active" && (
                      <span className="font-mono">
                        {clockFrom(c.started_at, now)}
                      </span>
                    )}
                    {c.recording && (
                      <span className="inline-flex items-center gap-1">
                        <Icon name="Mic" className="h-3 w-3" />
                        {c.audio_seconds
                          ? `${c.audio_seconds.toFixed(0)}s captured`
                          : "recording"}
                      </span>
                    )}
                    {c.media_ready && (
                      <span className="text-success">media up</span>
                    )}
                    {audioCallId === c.call_id && (
                      <span
                        className={
                          audioState === "live"
                            ? "font-medium text-success"
                            : "text-warning"
                        }
                      >
                        {audioState === "live"
                          ? muted
                            ? "mic muted"
                            : "mic + speakers live"
                          : `audio ${audioState}`}
                      </span>
                    )}
                  </p>
                </div>
                {/* Speaker/mic, offered for ANY live call.
                    This was gated on phase === "active" and the button simply
                    never appeared: the phase string is advisory and a call can
                    carry media without ever being reported active. Attaching
                    early is harmless — the call sends silence until the mic
                    arrives — whereas hiding the button is unrecoverable. */}
                {c.direction === "outgoing" || c.phase !== "ringing" ? (
                  audioCallId === c.call_id ? (
                    <div className="flex shrink-0 gap-1.5">
                      <button
                        onClick={toggleMute}
                        title={muted ? "Unmute" : "Mute"}
                        className={`rounded-lg border px-2.5 py-1.5 text-xs tech-transition ${
                          muted
                            ? "border-destructive/40 bg-destructive/10 text-destructive"
                            : "border-border text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {muted ? (
                          <Icon name="MicOff" className="h-3.5 w-3.5" />
                        ) : (
                          <Icon name="Mic" className="h-3.5 w-3.5" />
                        )}
                      </button>
                      <button
                        onClick={stopAudio}
                        title="Leave the call's audio (the call stays up)"
                        className="rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground tech-transition"
                      >
                        <Icon name="Headphones" className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => void joinAudio(c)}
                      disabled={audioState === "requesting-mic" || audioState === "connecting"}
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-success px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 tech-transition disabled:opacity-50"
                    >
                      {audioState === "requesting-mic" || audioState === "connecting" ? (
                        <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Icon name="Volume2" className="h-3.5 w-3.5" />
                      )}
                      {audioState === "requesting-mic"
                        ? "Mic…"
                        : audioState === "connecting"
                          ? "Joining…"
                          : "Join audio"}
                    </button>
                  )
                ) : null}

                <button
                  onClick={() => void copyReport(c)}
                  disabled={reportBusy}
                  title="Copy a debug report for this call"
                  className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground tech-transition disabled:opacity-50"
                >
                  {reportBusy ? (
                    <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
                  ) : copied ? (
                    <Icon name="ClipboardCheck" className="h-3.5 w-3.5 text-success" />
                  ) : (
                    <Icon name="ClipboardList" className="h-3.5 w-3.5" />
                  )}
                </button>

                {c.direction === "incoming" && c.phase === "ringing" ? (
                  <div className="flex gap-1.5">
                    <button
                      onClick={() => void act("answer", c)}
                      className="rounded-lg bg-success px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 tech-transition"
                    >
                      Answer
                    </button>
                    <button
                      onClick={() => void act("reject", c)}
                      className="rounded-lg bg-secondary px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground tech-transition"
                    >
                      Decline
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => void act("hangup", c)}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-destructive px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 tech-transition"
                  >
                    <Icon name="PhoneOff" className="h-3.5 w-3.5" />
                    Hang up
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* The report itself — shown as well as copied, because the clipboard is
          blocked in plenty of contexts and a report you can't get out of the
          browser is no report at all. */}
      {report && (
        <section className="mt-4 rounded-2xl border border-border bg-card p-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
              <Icon name="ClipboardList" className="h-3.5 w-3.5" />
              Debug report {copied && <span className="text-success">· copied</span>}
            </h2>
            <div className="flex gap-2">
              <Button variant="text" size="none" layout="" onClick={() => void navigator.clipboard.writeText(report)} className="text-[10px]">
                Copy again
              </Button>
              <Button variant="text" size="none" layout="" onClick={() => setReport(null)} className="text-[10px]">
                Dismiss
              </Button>
            </div>
          </div>
          <textarea
            readOnly
            value={report}
            rows={14}
            onFocus={(e) => e.currentTarget.select()}
            className="w-full resize-y rounded-lg border border-border bg-background p-2 font-mono text-[10px] leading-relaxed text-foreground"
          />
          <p className="mt-1.5 text-[10px] text-muted-foreground">
            Paste this into the conversation. It carries the server timeline,
            the browser timeline and any websocket close codes — no phone
            numbers and no tokens.
          </p>
        </section>
      )}

      {/* recent */}
      {recent.length > 0 && (
        <section className="mt-5">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Recent
          </h2>
          <div className="divide-y divide-border rounded-xl border border-border bg-card">
            {recent.map((c) => (
              <div key={c.call_id} className="p-3">
                <div className="flex items-center gap-3">
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-secondary text-muted-foreground">
                    <Icon name="PhoneOff" className="h-3.5 w-3.5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs text-foreground">
                      {c.peer || c.targets?.join(", ") || "Unknown"}
                    </p>
                    <p className="text-[10px] text-muted-foreground">
                      {c.direction === "incoming" ? "Incoming" : "Outgoing"}
                      {c.kind === "group" ? " group call" : ""}
                      {c.end_reason ? ` · ${c.end_reason}` : ""}
                      {c.audio_seconds
                        ? ` · ${c.audio_seconds.toFixed(0)}s of audio`
                        : ""}
                    </p>
                  </div>
                </div>

                {/* A connected call that captured nothing is the failure that
                    looks like success — call it out instead of offering an
                    empty file to play. */}
                {c.recording && !c.audio_seconds && (
                  <p className="mt-1.5 rounded-md bg-warning/10 px-2 py-1 text-[10px] leading-relaxed text-warning">
                    Connected, but no audio arrived — signalling worked and
                    media didn&apos;t. The recording is empty.
                  </p>
                )}

                {c.recording && !!c.audio_seconds && (
                  <audio
                    controls
                    preload="none"
                    src={callRecordingUrl(accountId, c.call_id)}
                    className="mt-1.5 h-8 w-full"
                  />
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {live.length === 0 && recent.length === 0 && bridgeUp && (
        <p className="mt-6 text-center text-xs text-muted-foreground">
          No calls yet. Dial a number above to place the first one.
        </p>
      )}
    </div>
  );
}
