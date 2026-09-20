"use client";

/**
 * /notes/meeting/[id] — meeting detail: transcript ↔ notes, action items,
 * live SSE pipeline progress (spec: note_taker_app.md §5.3/§5.4). Slice 1 adds
 * generated notes + draft action items on top of slice 0's transcript view.
 */

import Button from "@/components/ui/Button";
import Icon, { themedIcon } from "@/components/Icon";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import MarkdownMessage from "@/components/MarkdownMessage";
import AskPanel from "../../components/AskPanel";
import FollowupEmailModal from "../../components/FollowupEmailModal";
import JoinCallModal from "../../components/JoinCallModal";
import MeetingOutcomes from "../../components/MeetingOutcomes";
import MeetingPrep from "../../components/MeetingPrep";
import { speakerAvatar, speakerText } from "../../lib/speakers";
import {
  approveAllActions,
  audioUrl,
  botScreenshotUrl,
  deleteMeeting,
  dispatchAction,
  eventsUrl,
  formatClock,
  getBotDiagnostics,
  getMeeting,
  getMeetingBot,
  getNote,
  identifySpeakers,
  listActions,
  listTemplates,
  rejectAction,
  retranscribe,
  saveAttendees,
  saveScratchNotes,
  saveSpeakerNames,
  setMeetingTemplate,
  summarize,
} from "../../lib/api";
import type {
  ActionItem,
  Attendee,
  BotDiagnostics,
  MeetingBot,
  MeetingDetail,
  MeetingEvent,
} from "../../lib/types";

// Speaker colour is shared with the live console and roster (lib/speakers.ts),
// so the same person reads as the same person on every surface.
const speakerColor = speakerText;
const speakerAv = speakerAvatar;

export default function MeetingPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const [meeting, setMeeting] = useState<MeetingDetail | null>(null);
  const [notesMd, setNotesMd] = useState<string | null>(null);
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [progress, setProgress] = useState<MeetingEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summarizing, setSummarizing] = useState(false);
  const [actioning, setActioning] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [showEmail, setShowEmail] = useState(false);
  const [showJoin, setShowJoin] = useState(false);
  const [addName, setAddName] = useState("");
  const [addEmail, setAddEmail] = useState("");
  const [scratch, setScratch] = useState("");
  const [tab, setTab] = useState<"summary" | "transcript" | "actions" | "ask">(
    "summary"
  );
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  const [identifying, setIdentifying] = useState(false);
  const [savingSpeaker, setSavingSpeaker] = useState(false);
  const [templates, setTemplates] = useState<{ key: string; label: string }[]>([]);
  const [retranscribing, setRetranscribing] = useState(false);
  const [bot, setBot] = useState<MeetingBot | null>(null);
  const [botDiag, setBotDiag] = useState<BotDiagnostics | null>(null);
  const [showBotDiag, setShowBotDiag] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const scratchLoaded = useRef(false);
  const audioRef = useRef<HTMLAudioElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [m, note, acts, b] = await Promise.all([
        getMeeting(id),
        getNote(id).catch(() => null),
        listActions(id).catch(() => []),
        getMeetingBot(id).catch(() => null),
      ]);
      setMeeting(m);
      setNotesMd(note?.notes_md ?? m.summary_md ?? null);
      setActions(acts);
      setBot(b);
      // Seed the scratch editor once, then leave the user's live edits alone.
      if (!scratchLoaded.current) {
        setScratch(m.scratch_notes ?? "");
        scratchLoaded.current = true;
      }
      setError(null);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }, [id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    listTemplates()
      .then(setTemplates)
      .catch(() => {});
  }, []);

  // Live pipeline progress over SSE. On every state change (and on terminal),
  // refetch the detail so transcript/notes/actions stay in step. The stream
  // closes itself once the pipeline is terminal; a deleted meeting emits a
  // named `gone` event.
  useEffect(() => {
    const es = new EventSource(eventsUrl(id));
    es.onmessage = (ev) => {
      try {
        const snap = JSON.parse(ev.data) as MeetingEvent;
        setProgress(snap);
        void refresh();
      } catch {
        /* heartbeat / comment frame */
      }
    };
    es.addEventListener("gone", () => {
      es.close();
      router.push("/notes");
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [id, refresh, router]);

  async function onSummarize() {
    setSummarizing(true);
    try {
      await summarize(id);
      await refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSummarizing(false);
    }
  }

  // Re-run transcription on the existing recording with the current STT model
  // (e.g. after switching to Deepgram to get named speakers).
  async function onRetranscribe() {
    setRetranscribing(true);
    setError(null);
    try {
      await retranscribe(id);
      await refresh(); // status → processing; live progress takes over
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setRetranscribing(false);
    }
  }

  // Switch the notes template and regenerate through the new lens.
  async function onPickTemplate(key: string) {
    if (!meeting || key === (meeting.template_key ?? "standard_meeting")) return;
    setMeeting({ ...meeting, template_key: key });
    setSummarizing(true);
    try {
      await setMeetingTemplate(id, key);
      await summarize(id);
      await refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSummarizing(false);
    }
  }

  function seekTo(s: number) {
    if (audioRef.current) {
      audioRef.current.currentTime = s;
      void audioRef.current.play();
    }
  }

  function jumpToSegment(segmentId: string) {
    // A citation can be clicked from any tab — switch to the transcript first,
    // then scroll once the panel has mounted.
    setTab("transcript");
    setTimeout(() => {
      const seg = meeting?.segments.find((x) => x.id === segmentId);
      const el = document.getElementById(`seg-${segmentId}`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      if (el) {
        el.classList.add("ring-1", "ring-primary");
        setTimeout(() => el.classList.remove("ring-1", "ring-primary"), 1600);
      }
      if (seg) seekTo(seg.start_s);
    }, 60);
  }

  function flashToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  }

  async function persistAttendees(next: Attendee[]) {
    if (!meeting) return;
    setMeeting({ ...meeting, attendees: next });
    try {
      await saveAttendees(id, next);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      await refresh();
    }
  }

  function addAttendee() {
    const name = addName.trim();
    const email = addEmail.trim();
    if (!name && !email) return;
    void persistAttendees([...(meeting?.attendees ?? []), { name, email }]);
    setAddName("");
    setAddEmail("");
  }

  const speakerNames = meeting?.speaker_names ?? {};
  function displayName(label: string | null): string {
    if (!label) return "";
    return speakerNames[label] || label;
  }
  function initials(s: string): string {
    const p = s.trim().split(/\s+/).filter(Boolean);
    if (p.length >= 2) return (p[0][0] + p[1][0]).toUpperCase();
    return (s.trim().slice(0, 2) || "?").toUpperCase();
  }
  function openRename(label: string) {
    setEditingSpeaker(label);
    setNameDraft(speakerNames[label] ?? "");
  }
  async function saveSpeaker() {
    if (!editingSpeaker || !meeting) return;
    const label = editingSpeaker;
    const name = nameDraft.trim();
    setSavingSpeaker(true);
    try {
      const next: Record<string, string> = { ...speakerNames };
      if (name) next[label] = name;
      else delete next[label];
      const saved = await saveSpeakerNames(id, next);
      setMeeting({ ...meeting, speaker_names: saved });
      setEditingSpeaker(null);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSavingSpeaker(false);
    }
  }

  async function onIdentifySpeakers() {
    if (!meeting || identifying) return;
    setIdentifying(true);
    try {
      const { names, detected } = await identifySpeakers(id);
      setMeeting({ ...meeting, speaker_names: names });
      const hits = Object.entries(detected);
      flashToast(
        hits.length > 0
          ? `Named ${hits.map(([, v]) => v).join(", ")} from the conversation`
          : "No clear self-introductions found — name speakers manually"
      );
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setIdentifying(false);
    }
  }

  // Distinct diarized speakers present in the transcript.
  const speakerLabels = Array.from(
    new Set(
      (meeting?.segments ?? [])
        .map((s) => s.speaker_label)
        .filter((x): x is string => !!x)
    )
  );
  const chipCls =
    "inline-flex items-center gap-1 rounded-full border border-border bg-card px-2.5 py-1 text-xs text-foreground";
  async function onApprove(a: ActionItem) {
    setActioning(a.id);
    try {
      const res = await dispatchAction(a.id);
      await refresh();
      if (res.dispatch_error) {
        setError(`Couldn't dispatch: ${res.dispatch_error}`);
      } else if (res.kind === "email") {
        flashToast(
          res.dispatch_ref?.startsWith("sent:")
            ? "Email sent"
            : "Email drafted in your mailbox — recipient needs confirming"
        );
      } else if (res.kind === "document") {
        flashToast("Document drafted — view in Artifacts");
      } else {
        flashToast("Task created — view in Tasks");
      }
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setActioning(null);
    }
  }

  async function onReject(actionId: string) {
    setActioning(actionId);
    try {
      await rejectAction(actionId);
      await refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setActioning(null);
    }
  }

  async function onApproveAll() {
    setActioning("all");
    try {
      const { created } = await approveAllActions(id, 0.8);
      await refresh();
      flashToast(
        created.length
          ? `${created.length} item${created.length > 1 ? "s" : ""} dispatched`
          : "No draft items at 80%+ confidence"
      );
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setActioning(null);
    }
  }

  const summaryRun = meeting?.runs.find((r) => r.kind === "summary");
  const transcribeRun = meeting?.runs.find((r) => r.kind === "transcribe");
  // Busy reads the meeting's OWN runs too, not just SSE snapshots — a
  // regenerate creates a queued run the stream may never report (it closes
  // once terminal), and this flag is what drives the poll fallback below.
  const busy =
    meeting?.status === "processing" ||
    progress?.runs.some((r) => r.status === "queued" || r.status === "running") ||
    meeting?.runs.some((r) => r.status === "queued" || r.status === "running");

  // Poll while the pipeline works. The SSE stream is the fast path, but it
  // dies silently (proxy blip, terminal-then-regenerate); this is the
  // correctness net that keeps the page from freezing on stale state.
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => void refresh(), 5000);
    return () => clearInterval(t);
  }, [busy, refresh]);

  // A dispatched notetaker that is still out there (or died trying) — the
  // meeting page must tell that story; before, it showed "No transcript."
  const botActive =
    !!bot &&
    ["requested", "joining", "waiting_room", "in_call", "processing"].includes(
      bot.status
    );
  const botFailed =
    !!bot && ["failed", "not_admitted"].includes(bot.status);

  async function onShowBotDiag() {
    setShowBotDiag((v) => !v);
    if (!botDiag) {
      try {
        setBotDiag(await getBotDiagnostics(id));
      } catch {
        /* endpoint 404s when there was never a bot */
      }
    }
  }

  // Diarization is only real on a provider that returns speakers (AssemblyAI,
  // Deepgram, or a local sherpa pass); Whisper alone returns none. Surface that
  // so "no named speakers" isn't a silent mystery.
  const diarized = speakerLabels.length > 0;
  const sttModel = meeting?.transcript_source ?? "";
  const canDiarize =
    sttModel.startsWith("assemblyai/") ||
    sttModel.startsWith("deepgram/") ||
    // The local diarization pass appends this to the model id when it ran.
    sttModel.includes("sherpa-diar");
  const showDiarizeHint =
    !!meeting && meeting.segments.length > 0 && !diarized && !canDiarize && !busy;

  // Provenance: map a segment id → segment so an action item's segment_ids can
  // render as clickable "source" chips that jump to the exact transcript moment.
  const segById = new Map((meeting?.segments ?? []).map((s) => [s.id, s]));
  // …and by idx, since the structured notes cite decisions by segment index.
  const segByIdx = new Map((meeting?.segments ?? []).map((s) => [s.idx, s]));
  const citedDecisions = (meeting?.summary_json?.decisions ?? []).filter(
    (d) => d && d.text && (d.refs?.length ?? 0) > 0
  );

  // A meeting exists before it happens. With no audio and nothing captured,
  // the workspace below (audio scrubber, transcript, actions) has nothing to
  // show — what you actually want is to get ready. Keyed on "has any
  // recording" rather than on status, because status advances the moment
  // capture starts while a draft can also be an upload mid-flight.
  const isPrep =
    !!meeting &&
    meeting.recordings.length === 0 &&
    meeting.segments.length === 0 &&
    !busy &&
    meeting.status !== "recording";

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 border-b border-border shrink-0">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <button
            onClick={() => router.push("/notes")}
            className="shrink-0 p-2 rounded-lg border border-border text-muted-foreground hover:bg-secondary tech-transition"
            aria-label="Back to notes"
          >
            <Icon name="ArrowLeft" className="w-4 h-4" />
          </button>
          <div className="min-w-0">
            <h1 className="text-base sm:text-lg font-bold text-foreground truncate">
              {meeting?.title || "Untitled meeting"}
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5 truncate">
              {busy
                ? "Processing…"
                : meeting?.transcript_source
                  ? `Transcribed by ${meeting.transcript_source}`
                  : "Meeting detail"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {notesMd && (
            <Button variant="secondary" size="none" layout="" onClick={() => setShowEmail(true)} aria-label="Draft a follow-up email" title="Draft a follow-up email" className="shrink-0 px-2.5 sm:px-3 py-2 text-sm">
              <span className="flex items-center gap-1.5">
                <Icon name="Mail" className="w-4 h-4" />
                <span className="hidden sm:inline">Follow-up</span>
              </span>
            </Button>
          )}
          {meeting && meeting.segments.length > 0 && (
            <Button variant="secondary" size="none" layout="" onClick={onSummarize} disabled={summarizing || busy} aria-label={notesMd ? "Regenerate notes" : "Generate notes"} title={notesMd ? "Regenerate notes" : "Generate notes"} className="shrink-0 px-2.5 sm:px-3 py-2 text-sm">
              <span className="flex items-center gap-1.5">
                {summarizing ? (
                  <Icon name="Loader2" className="w-4 h-4 animate-spin" />
                ) : notesMd ? (
                  <Icon name="RefreshCw" className="w-4 h-4" />
                ) : (
                  <Icon name="Sparkles" className="w-4 h-4" />
                )}
                <span className="hidden sm:inline">
                  {notesMd ? "Regenerate" : "Generate notes"}
                </span>
              </span>
            </Button>
          )}
          <Button variant="secondary" size="none" layout="" onClick={async () => {
              setRefreshing(true);
              await refresh();
              setRefreshing(false);
            }} disabled={refreshing} aria-label="Refresh" title="Refresh" className="p-2 text-sm">
            <Icon name="RefreshCw" className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} />
          </Button>
          {meeting && (
            <Button variant="destructive" size="none" layout="" onClick={async () => {
                if (!confirm("Delete this meeting, its audio and transcript?"))
                  return;
                setDeleting(true);
                try {
                  await deleteMeeting(id);
                  router.push("/notes");
                } catch (e) {
                  setError(
                    `Couldn't delete: ${String(e instanceof Error ? e.message : e)}`
                  );
                  setDeleting(false);
                }
              }} disabled={deleting} aria-label="Delete this meeting" title="Delete this meeting" className="px-3 py-2 text-sm">
              {deleting ? (
                <Icon name="Loader2" className="w-4 h-4 animate-spin" />
              ) : (
                <Icon name="Trash2" className="w-4 h-4" />
              )}
            </Button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-4 space-y-4">
          {error && (
            <div className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}

          {toast && (
            <div className="rounded-lg bg-success/10 px-3 py-2 text-sm text-success flex items-center gap-2">
              <Icon name="Check" className="w-4 h-4" />
              {toast}
            </div>
          )}

          {/* Before the meeting: prepare it. The workspace below has nothing to
              show yet, and this is the only moment you can actually brief the
              copilot. */}
          {isPrep && meeting && (
            <MeetingPrep
              meeting={meeting}
              onChanged={() => void refresh()}
              onSendBot={() => setShowJoin(true)}
            />
          )}

          {!isPrep && meeting?.recordings.length ? (
            <audio
              ref={audioRef}
              controls
              preload="metadata"
              src={audioUrl(id)}
              className="w-full"
            />
          ) : null}

          {/* At-a-glance meta strip */}
          {!isPrep && meeting && (meeting.duration_s != null || meeting.transcript_source) && (
            <div className="flex flex-wrap items-center gap-2">
              {meeting.created_at && (
                <span className={chipCls}>
                  {new Date(meeting.created_at).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                  })}
                </span>
              )}
              {meeting.duration_s != null && (
                <span className={chipCls}>
                  <Icon name="Clock" className="w-3 h-3" />
                  {formatClock(meeting.duration_s)}
                </span>
              )}
              {speakerLabels.length > 0 && (
                <span className={chipCls}>
                  <Icon name="Users" className="w-3 h-3" />
                  {speakerLabels.length} speaker
                  {speakerLabels.length > 1 ? "s" : ""}
                </span>
              )}
              {meeting.language && (
                <span className={`${chipCls} capitalize`}>{meeting.language}</span>
              )}
              {meeting.transcript_source && (
                <span className={`${chipCls} font-mono !text-[10px] text-muted-foreground`}>
                  {meeting.transcript_source}
                </span>
              )}
            </div>
          )}

          {!isPrep && meeting && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                Attendees
              </span>
              {meeting.attendees.map((a, i) => (
                <span
                  key={`${a.email}-${i}`}
                  className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-1 text-xs text-foreground"
                  title={a.email}
                >
                  {a.name || a.email}
                  <button
                    onClick={() =>
                      persistAttendees(
                        meeting.attendees.filter((_, j) => j !== i)
                      )
                    }
                    className="text-muted-foreground hover:text-destructive"
                    aria-label="Remove attendee"
                  >
                    <Icon name="X" className="w-3 h-3" />
                  </button>
                </span>
              ))}
              <span className="inline-flex items-center gap-1">
                <input
                  value={addName}
                  onChange={(e) => setAddName(e.target.value)}
                  placeholder="Name"
                  className="w-20 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                />
                <input
                  value={addEmail}
                  onChange={(e) => setAddEmail(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addAttendee()}
                  placeholder="email@…"
                  className="w-32 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                />
                <Button variant="secondary" size="icon-xs" radius="keep" layout="" onClick={addAttendee} aria-label="Add attendee" className="rounded-md">
                  <Icon name="Plus" className="w-3.5 h-3.5" />
                </Button>
              </span>
            </div>
          )}

          {/* The notetaker's own story — was it sent, is it in the call, why
              did it fail. Without this card a failed join reads as a meeting
              with mysteriously "No transcript." */}
          {bot && (botActive || botFailed) && (
            <div
              className={`rounded-xl border p-4 ${
                botFailed
                  ? "border-destructive/40 bg-destructive/[0.04]"
                  : "border-primary/25 bg-primary/[0.04]"
              }`}
            >
              <div className="flex items-center gap-3">
                <Icon name="Video"
                  className={`w-5 h-5 shrink-0 ${
                    botFailed ? "text-destructive" : "text-primary"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-foreground">
                    {botFailed
                      ? "The notetaker couldn't join this call"
                      : bot.status === "in_call"
                        ? "Notetaker is in the call, recording"
                        : bot.status === "waiting_room"
                          ? "Notetaker is knocking — someone in the call must admit it"
                          : bot.status === "processing"
                            ? "Notetaker left the call — processing the recording"
                            : "Notetaker is joining the call…"}
                  </p>
                  <p className="text-[11px] text-muted-foreground truncate">
                    {bot.meeting_url}
                  </p>
                </div>
                {botActive && (
                  <Link
                    href={`/notes/live/${id}`}
                    className="shrink-0 inline-flex items-center gap-1.5 rounded-lg border border-primary/30 px-2.5 py-1.5 text-xs text-primary hover:bg-primary/10 tech-transition"
                  >
                    <Icon name="Radio" className="w-3.5 h-3.5" />
                    Live console
                  </Link>
                )}
              </div>
              {botFailed && bot.error && (
                <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-destructive/10 px-2.5 py-1.5 text-xs leading-relaxed text-destructive">
                  <Icon name="AlertTriangle" className="mt-0.5 w-3.5 h-3.5 shrink-0" />
                  <span>{bot.error}</span>
                </p>
              )}
              {botFailed && (
                <div className="mt-2">
                  <button
                    onClick={() => void onShowBotDiag()}
                    className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2 tech-transition"
                  >
                    {showBotDiag ? "Hide details" : "What did the bot see?"}
                  </button>
                  {showBotDiag && (
                    <div className="mt-2 space-y-2">
                      {botDiag?.diagnostics?.diagnostics?.controls?.length ? (
                        <p className="text-[11px] text-muted-foreground">
                          Buttons on the page:{" "}
                          <span className="font-mono">
                            {botDiag.diagnostics.diagnostics.controls
                              .slice(0, 10)
                              .join(" · ")}
                          </span>
                        </p>
                      ) : null}
                      {botDiag?.diagnostics?.diagnostics?.title ? (
                        <p className="text-[11px] text-muted-foreground">
                          Page title:{" "}
                          <span className="font-mono">
                            {botDiag.diagnostics.diagnostics.title}
                          </span>
                        </p>
                      ) : null}
                      {botDiag?.diagnostics?.has_screenshot ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={botScreenshotUrl(id)}
                          alt="The meeting page exactly as the bot saw it"
                          className="max-w-full rounded-lg border border-border"
                        />
                      ) : (
                        <p className="text-[11px] text-muted-foreground">
                          {botDiag
                            ? "No screenshot was captured for this attempt."
                            : "Loading details…"}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {busy && (
            <div className="rounded-xl border border-border bg-card p-4 flex items-center gap-3">
              <Icon name="Loader2" className="w-4 h-4 animate-spin text-warning" />
              <div className="min-w-0">
                <p className="text-sm font-semibold text-foreground">
                  {progress?.runs.find((r) => r.status === "running")?.kind ===
                  "summary"
                    ? "Writing notes…"
                    : "Transcribing…"}
                </p>
                <p className="text-xs text-muted-foreground">
                  {(() => {
                    const run = progress?.runs.find(
                      (r) => r.status === "running"
                    );
                    if (run?.chunk_total && run.chunk_total > 1)
                      return `${run.stage ?? "working"} — chunk ${run.chunk_done}/${run.chunk_total}`;
                    return run?.stage ?? "queued";
                  })()}{" "}
                  — updates live.
                </p>
              </div>
            </div>
          )}

          {meeting?.status === "failed" && !botFailed && (
            <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-4">
              <p className="text-sm font-semibold text-destructive">
                Pipeline failed
              </p>
              <p className="text-xs text-muted-foreground mt-1 font-mono break-all">
                {summaryRun?.error ??
                  transcribeRun?.error ??
                  bot?.error ??
                  "No error detail recorded."}
              </p>
            </div>
          )}

          {/* What came out of it, before the tabs — the yield of a meeting is
              its notes, actions and follow-up, and actions used to be tab
              three. */}
          {!isPrep && meeting && meeting.segments.length > 0 && (
            <MeetingOutcomes
              meeting={meeting}
              actions={actions}
              hasNotes={!!notesMd}
              onOpenActions={() => setTab("actions")}
              onDraftFollowup={() => setShowEmail(true)}
            />
          )}

          {/* Tabbed workspace — the summary is the hero */}
          {!isPrep && (
          <>
          <div className="flex items-center gap-1 border-b border-border overflow-x-auto">
            {(
              [
                ["summary", "Summary"],
                ["transcript", "Transcript"],
                ["actions", "Actions"],
                ["ask", "Ask"],
              ] as const
            ).map(([tid, label]) => (
              <button
                key={tid}
                onClick={() => setTab(tid)}
                className={`relative px-3 py-2 text-sm whitespace-nowrap tech-transition ${
                  tab === tid
                    ? "text-primary font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <span className="flex items-center gap-1.5">
                  {label}
                  {tid === "actions" && actions.length > 0 && (
                    <span className="text-[10px] rounded-full bg-secondary px-1.5 py-0.5 text-muted-foreground">
                      {actions.length}
                    </span>
                  )}
                </span>
                {tab === tid && (
                  <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" />
                )}
              </button>
            ))}
          </div>

          <div className="pt-4">
            {/* Transcript */}
            {tab === "transcript" && (
            <div>
              {showDiarizeHint && (
                <div className="mb-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3">
                  <div className="flex items-start gap-2.5">
                    <Icon name="Users" className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                    <div className="min-w-0 space-y-2">
                      <p className="text-xs text-foreground">
                        <span className="font-semibold">
                          Speakers aren&apos;t separated.
                        </span>{" "}
                        Your current transcription model doesn&apos;t identify who
                        said what. Switch to an AssemblyAI (or Deepgram) model in{" "}
                        <Link
                          href="/settings/models"
                          className="text-primary underline underline-offset-2"
                        >
                          Settings → Models
                        </Link>
                        , then re-transcribe to label each speaker.
                      </p>
                      <button
                        onClick={() => void onRetranscribe()}
                        disabled={retranscribing || busy}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted tech-transition disabled:opacity-60"
                      >
                        {retranscribing ? (
                          <Icon name="Loader2" className="w-3 h-3 animate-spin" />
                        ) : (
                          <Icon name="RefreshCw" className="w-3 h-3" />
                        )}
                        Re-transcribe
                      </button>
                    </div>
                  </div>
                </div>
              )}
              {speakerLabels.length > 0 &&
                !speakerLabels.every((l) => speakerNames[l]) && (
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                    <p className="text-[11px] text-muted-foreground">
                      {speakerLabels.length} speaker
                      {speakerLabels.length > 1 ? "s" : ""} — click a name to
                      label them, or let AI find self-introductions.
                    </p>
                    <button
                      onClick={() => void onIdentifySpeakers()}
                      disabled={identifying}
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary hover:bg-primary/20 tech-transition disabled:opacity-60"
                      title="Detect who's who from lines like “Hi, I'm Priya”"
                    >
                      {identifying ? (
                        <Icon name="Loader2" className="w-3 h-3 animate-spin" />
                      ) : (
                        <Icon name="Sparkles" className="w-3 h-3" />
                      )}
                      {identifying ? "Detecting…" : "Auto-detect names"}
                    </button>
                  </div>
                )}
              {meeting && meeting.segments.length > 0 ? (
                <div className="rounded-xl border border-border bg-card divide-y divide-border max-h-[70vh] overflow-y-auto">
                  {meeting.segments.map((seg) => (
                    <div
                      key={seg.id}
                      id={`seg-${seg.id}`}
                      className="flex gap-3 px-4 py-2.5 rounded-lg tech-transition"
                    >
                      <button
                        onClick={() => seekTo(seg.start_s)}
                        className="shrink-0 flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-primary tech-transition mt-0.5"
                        title="Play from here"
                      >
                        <Icon name="Play" className="w-3 h-3" />
                        {formatClock(seg.start_s)}
                      </button>
                      <div className="min-w-0">
                        {seg.speaker_label ? (
                          <button
                            onClick={() => openRename(seg.speaker_label!)}
                            className="group inline-flex items-center gap-1.5 mb-0.5"
                            title="Click to name this speaker"
                          >
                            <span
                              className={`w-4 h-4 rounded-full grid place-items-center text-[8px] font-bold ${speakerAv(seg.speaker_label)}`}
                            >
                              {initials(displayName(seg.speaker_label))}
                            </span>
                            <span
                              className={`text-[10px] font-semibold uppercase tracking-wide ${speakerColor(seg.speaker_label)}`}
                            >
                              {displayName(seg.speaker_label)}
                            </span>
                            <Icon name="Pencil" className="w-2.5 h-2.5 text-muted-foreground reveal-on-hover tech-transition" />
                          </button>
                        ) : seg.channel ? (
                          <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                            {seg.channel === "mic" ? "You" : seg.channel}
                          </span>
                        ) : null}
                        <p className="text-sm text-foreground">{seg.text}</p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground py-8 text-center">
                  {busy ? "Transcript is being generated…" : "No transcript."}
                </p>
              )}
            </div>
            )}

            {/* Summary — the generated notes (hero) + your steering notes */}
            {tab === "summary" && (
            <div className="max-w-3xl space-y-4">
              {meeting && meeting.segments.length > 0 && templates.length > 0 && (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-muted-foreground">
                    Notes template
                  </span>
                  <select
                    value={meeting.template_key ?? "standard_meeting"}
                    onChange={(e) => void onPickTemplate(e.target.value)}
                    disabled={summarizing || busy}
                    className="rounded-lg border border-border bg-card px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
                  >
                    {templates.map((t) => (
                      <option key={t.key} value={t.key}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                  {notesMd && (
                    <span className="text-[10px] text-muted-foreground">
                      changing regenerates the notes
                    </span>
                  )}
                </div>
              )}
              <div>
                <h2 className="text-sm font-semibold text-foreground mb-2 flex items-center gap-1.5">
                  Your notes
                  <span className="text-[10px] text-muted-foreground font-normal">
                    (merged into the summary as emphasis)
                  </span>
                </h2>
                <textarea
                  value={scratch}
                  onChange={(e) => setScratch(e.target.value)}
                  onBlur={() => void saveScratchNotes(id, scratch).catch(() => {})}
                  placeholder="Jot the points that matter — decisions, who owns what, things to follow up. The AI will expand these from the transcript when you generate notes."
                  rows={3}
                  className="w-full rounded-xl border border-border bg-card p-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-y"
                />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-foreground mb-2">
                  Notes
                </h2>
                {notesMd ? (
                  <div className="rounded-xl border border-border bg-card p-4 max-h-[70vh] overflow-y-auto text-sm">
                    <MarkdownMessage content={notesMd} />
                  </div>
                ) : (
                  <div className="rounded-xl border border-border bg-card p-6 text-center">
                    <Icon name="Sparkles" className="w-6 h-6 mx-auto text-muted-foreground mb-2" />
                    <p className="text-xs text-muted-foreground">
                      {busy
                        ? "Notes will appear once the transcript is ready."
                        : meeting?.segments.length
                          ? "Click “Generate notes” to summarize this meeting."
                          : "Notes appear after transcription."}
                    </p>
                  </div>
                )}
              </div>
              {citedDecisions.length > 0 && (
                <div>
                  <h2 className="text-sm font-semibold text-foreground mb-2 flex items-center gap-1.5">
                    Decisions
                    <span className="text-[10px] text-muted-foreground font-normal">
                      (tap a timestamp to hear it in context)
                    </span>
                  </h2>
                  <div className="rounded-xl border border-border bg-card divide-y divide-border">
                    {citedDecisions.map((d, i) => (
                      <div key={i} className="px-4 py-2.5">
                        <p className="text-sm text-foreground">{d.text}</p>
                        <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                          {(d.refs ?? []).slice(0, 6).map((idx) => {
                            const seg = segByIdx.get(idx);
                            if (!seg) return null;
                            return (
                              <button
                                key={idx}
                                onClick={() => jumpToSegment(seg.id)}
                                title="Jump to this moment in the transcript"
                                className="inline-flex items-center gap-0.5 rounded-md border border-border bg-muted/40 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground hover:text-primary hover:border-primary/30 tech-transition"
                              >
                                <Icon name="Play" className="w-2.5 h-2.5" />
                                {formatClock(seg.start_s)}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            )}

            {/* Actions */}
            {tab === "actions" &&
              (actions.length > 0 ? (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <h2 className="text-sm font-semibold text-foreground flex items-center gap-1.5">
                      <Icon name="CheckSquare" className="w-4 h-4" />
                      Action items
                    </h2>
                    {actions.some(
                      (a) => a.status === "draft" && a.confidence >= 0.8
                    ) && (
                      <Button variant="secondary" size="none" radius="keep" layout="" onClick={onApproveAll} disabled={actioning !== null} title="Tasks are created, emails sent (or drafted when the recipient is unclear), documents drafted" className="text-[11px] rounded-md px-2 py-1">
                        Dispatch all ≥80%
                      </Button>
                    )}
                  </div>
                  <div className="space-y-2">
                    {actions.map((a) => {
                      const rowBusy = actioning === a.id || actioning === "all";
                      const KindIcon =
                        a.kind === "email"
                          ? themedIcon("Mail")
                          : a.kind === "document"
                            ? themedIcon("FileText")
                            : themedIcon("CheckSquare");
                      const kindLabel =
                        a.kind === "email"
                          ? "Email"
                          : a.kind === "document"
                            ? "Document"
                            : "Task";
                      return (
                        <div
                          key={a.id}
                          className={`rounded-lg border p-3 tech-transition ${
                            a.status === "created"
                              ? "border-success/30 bg-success/5"
                              : a.status === "rejected"
                                ? "border-border bg-card opacity-50"
                                : "border-border bg-card"
                          }`}
                        >
                          <div className="flex items-start gap-2">
                            <span
                              className="mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-full bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground"
                              title={`Dispatches as a ${kindLabel.toLowerCase()}`}
                            >
                              <KindIcon className="w-3 h-3" />
                              {kindLabel}
                            </span>
                            <p
                              className={`min-w-0 flex-1 text-sm ${a.status === "rejected" ? "line-through text-muted-foreground" : "text-foreground"}`}
                            >
                              {a.description}
                            </p>
                          </div>
                          {a.kind === "email" && a.payload?.email_to && (
                            <p className="mt-1 text-[10px] text-muted-foreground">
                              To: {a.payload.email_to}
                            </p>
                          )}
                          {a.dispatch_error && a.status !== "created" && (
                            <p className="mt-1.5 flex items-start gap-1 rounded-md bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
                              <Icon name="AlertTriangle" className="mt-0.5 w-3 h-3 shrink-0" />
                              {a.dispatch_error}
                            </p>
                          )}
                          <div className="flex items-center gap-2 mt-1.5">
                            <div className="flex-1 h-1 rounded-full bg-muted overflow-hidden">
                              <div
                                className="h-full bg-primary"
                                style={{
                                  width: `${Math.round(a.confidence * 100)}%`,
                                }}
                              />
                            </div>
                            <span className="text-[10px] text-muted-foreground shrink-0">
                              {Math.round(a.confidence * 100)}%
                            </span>
                            {a.due_hint && (
                              <span className="text-[10px] text-warning shrink-0">
                                {a.due_hint}
                              </span>
                            )}
                          </div>
                          {a.segment_ids.length > 0 && (
                            <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                              <span className="text-[10px] text-muted-foreground">
                                From
                              </span>
                              {a.segment_ids.slice(0, 5).map((sid) => {
                                const seg = segById.get(sid);
                                return (
                                  <button
                                    key={sid}
                                    onClick={() => jumpToSegment(sid)}
                                    title="Jump to this moment in the transcript"
                                    className="inline-flex items-center gap-0.5 rounded-md border border-border bg-muted/40 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground hover:text-primary hover:border-primary/30 tech-transition"
                                  >
                                    <Icon name="Play" className="w-2.5 h-2.5" />
                                    {seg ? formatClock(seg.start_s) : "source"}
                                  </button>
                                );
                              })}
                            </div>
                          )}
                          <div className="flex items-center gap-2 mt-2">
                            {a.status === "draft" && (
                              <>
                                <button
                                  onClick={() => onApprove(a)}
                                  disabled={rowBusy}
                                  className="flex-1 flex items-center justify-center gap-1 rounded-md bg-primary/10 text-primary px-2 py-1 text-xs hover:bg-primary/20 tech-transition disabled:opacity-60"
                                >
                                  {rowBusy ? (
                                    <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" />
                                  ) : (
                                    <Icon name="Check" className="w-3.5 h-3.5" />
                                  )}
                                  {a.kind === "email"
                                    ? "Approve → Send email"
                                    : a.kind === "document"
                                      ? "Approve → Draft doc"
                                      : "Approve → Task"}
                                </button>
                                <button
                                  onClick={() => onReject(a.id)}
                                  disabled={rowBusy}
                                  aria-label="Reject this action item"
                                  title="Reject"
                                  className="flex items-center justify-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-destructive hover:border-destructive/30 tech-transition disabled:opacity-60"
                                >
                                  <Icon name="X" className="w-3.5 h-3.5" />
                                </button>
                              </>
                            )}
                            {a.status === "created" &&
                              (a.kind === "email" ? (
                                <span className="flex items-center gap-1 text-xs text-success">
                                  <Icon name="Mail" className="w-3.5 h-3.5" />
                                  {a.dispatch_ref?.startsWith("sent:")
                                    ? "Email sent"
                                    : "Drafted in your mailbox"}
                                </span>
                              ) : a.kind === "document" ? (
                                <Link
                                  href="/artifacts"
                                  className="flex items-center gap-1 text-xs text-success hover:underline"
                                >
                                  <Icon name="FileText" className="w-3.5 h-3.5" /> Draft in
                                  Artifacts
                                  <Icon name="ExternalLink" className="w-3 h-3" />
                                </Link>
                              ) : (
                                <Link
                                  href={
                                    a.resulting_task_id
                                      ? `/tasks?item=${a.resulting_task_id}`
                                      : "/tasks"
                                  }
                                  className="flex items-center gap-1 text-xs text-success hover:underline"
                                >
                                  <Icon name="Check" className="w-3.5 h-3.5" /> In Tasks
                                  <Icon name="ExternalLink" className="w-3 h-3" />
                                </Link>
                              ))}
                            {a.status === "rejected" && (
                              <span className="text-xs text-muted-foreground">
                                Rejected
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground py-8 text-center">
                  {busy
                    ? "Action items appear once notes are generated."
                    : "No action items yet."}
                </p>
              ))}

            {/* Ask the meeting */}
            {tab === "ask" &&
              (meeting && meeting.segments.length > 0 ? (
                <AskPanel meetingId={id} onCite={jumpToSegment} />
              ) : (
                <p className="text-sm text-muted-foreground py-8 text-center">
                  Ask becomes available once there&apos;s a transcript.
                </p>
              ))}
          </div>
          </>
          )}
        </div>
      </div>

      {showJoin && meeting && (
        <JoinCallModal
          meetingId={id}
          defaultTitle={meeting.title ?? ""}
          onClose={() => setShowJoin(false)}
          onJoined={() => {
            setShowJoin(false);
            void refresh();
          }}
        />
      )}

      {showEmail && (
        <FollowupEmailModal
          meetingId={id}
          onClose={() => setShowEmail(false)}
          onSent={(msg) => {
            setShowEmail(false);
            flashToast(msg);
          }}
        />
      )}

      {editingSpeaker && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-4"
          onClick={() => setEditingSpeaker(null)}
        >
          <div
            className="w-full max-w-sm rounded-xl border border-border bg-card shadow-xl p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2">
              <span
                className={`w-6 h-6 rounded-full grid place-items-center text-[10px] font-bold ${speakerAv(editingSpeaker)}`}
              >
                {initials(displayName(editingSpeaker))}
              </span>
              <h2 className="text-sm font-semibold text-foreground">
                Name this speaker
              </h2>
            </div>
            <p className="text-[11px] text-muted-foreground mt-1.5">
              Renames{" "}
              <span className="font-mono text-foreground">{editingSpeaker}</span>{" "}
              across the transcript, notes, action owners &amp; the follow-up
              email. Regenerate notes to apply names there.
            </p>
            <input
              autoFocus
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void saveSpeaker()}
              placeholder="e.g. Alex Rivera"
              className="w-full mt-3 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
            {meeting && meeting.attendees.some((a) => a.name.trim()) && (
              <div className="mt-2">
                <p className="text-[10px] text-muted-foreground mb-1">
                  From attendees
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {meeting.attendees
                    .filter((a) => a.name.trim())
                    .map((a, i) => (
                      <button
                        key={`${a.name}-${i}`}
                        onClick={() => setNameDraft(a.name)}
                        className="rounded-full bg-secondary px-2.5 py-1 text-xs text-foreground hover:bg-primary/10 hover:text-primary tech-transition"
                      >
                        {a.name}
                      </button>
                    ))}
                </div>
              </div>
            )}
            <div className="flex items-center gap-2 mt-4">
              <Button size="none" layout="flex items-center" onClick={() => void saveSpeaker()} disabled={savingSpeaker} className="gap-1.5 px-4 py-2 text-xs">
                {savingSpeaker ? (
                  <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Icon name="Check" className="w-3.5 h-3.5" />
                )}
                Save
              </Button>
              {speakerNames[editingSpeaker] && (
                <button
                  onClick={() => {
                    setNameDraft("");
                    void saveSpeaker();
                  }}
                  disabled={savingSpeaker}
                  className="rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground hover:text-destructive hover:border-destructive/30 tech-transition disabled:opacity-50"
                >
                  Clear name
                </button>
              )}
              <button
                onClick={() => setEditingSpeaker(null)}
                className="rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground hover:text-foreground tech-transition ml-auto"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
