/** Wire types for the /notes gateway API (snake_case, matching the backend). */

export type MeetingStatus =
  | "draft"
  | "recording"
  | "processing"
  | "ready"
  | "failed";

export interface MeetingListItem {
  id: string;
  title: string | null;
  platform: string;
  status: MeetingStatus;
  language: string | null;
  duration_s: number | null;
  segment_count: number;
  has_notes: boolean;
  owner_email: string | null;
  template_key: string | null;
  start_at: string | null;
  created_at: string | null;
  /** When the meeting is planned for. Distinct from start_at (when capture
   *  actually began) — a meeting being prepared has one but not the other. */
  scheduled_at?: string | null;
  /** Per-meeting copilot decision. null = follow the account default. */
  copilot_enabled?: boolean | null;
  /** Signals the library bands on — see lib/bands.ts. */
  pending_actions?: number;
  action_count?: number;
  agenda_count?: number;
  has_brief?: boolean;
  attendee_count?: number;
  is_live?: boolean;
}

/** A meeting-notes template (shapes the generated summary). */
export interface NoteTemplate {
  key: string;
  label: string;
}

export interface Segment {
  id: string;
  idx: number;
  start_s: number;
  end_s: number;
  text: string;
  speaker_label: string | null;
  channel: string | null;
  confidence: number | null;
}

export interface Recording {
  id: string;
  channel: string;
  mime: string;
  duration_s: number | null;
  byte_size: number;
  created_at: string | null;
}

export interface SummaryRun {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  stage: string | null;
  chunk_done: number;
  chunk_total: number;
  model: string | null;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface Attendee {
  name: string;
  email: string;
}

/** A decision from the structured notes, with the transcript segments (by idx)
 *  it was grounded in — powers tap-to-verify provenance. */
export interface SummaryDecision {
  text: string;
  refs?: number[];
}

/** Structured notes JSON (subset the UI reads for provenance). */
export interface SummaryJson {
  decisions?: SummaryDecision[];
}

export interface MeetingDetail extends MeetingListItem {
  transcript_source: string | null;
  summary_md: string | null;
  /** Structured notes; decisions carry `refs` (source segment indices). */
  summary_json: SummaryJson | null;
  scratch_notes: string | null;
  attendees: Attendee[];
  /** Human names for diarized speaker labels, e.g. { "S1": "Alex Rivera" }. */
  speaker_names: Record<string, string>;
  recordings: Recording[];
  segments: Segment[];
  runs: SummaryRun[];
}

export interface EmailDraft {
  to: string[];
  subject: string;
  body_text: string;
}

export interface EmailAccount {
  id: string;
  email_address: string;
  label: string;
  is_default: boolean;
}

export interface ActionItem {
  id: string;
  description: string;
  confidence: number;
  status: "draft" | "approved" | "created" | "rejected";
  due_hint: string | null;
  segment_ids: string[];
  resulting_task_id: string | null;
  /** Which system a dispatch routes this to. */
  kind: "task" | "email" | "document";
  /** Extraction hints (owner_hint, email_to) used at dispatch time. */
  payload: { owner_hint?: string; email_to?: string };
  /** Where a dispatch landed: task id, `sent:<id>`, `draft:<id>`, `artifact:<agent>/<path>`. */
  dispatch_ref: string | null;
  /** Why the last dispatch failed (item stays draft so it can be retried). */
  dispatch_error: string | null;
}

export interface NoteDoc {
  meeting_id: string;
  notes_md: string | null;
  notes_json: Record<string, unknown> | null;
  updated_by: string | null;
  updated_at: string | null;
}

/** A notetaker bot dispatched to join a live call (spec §3.13). */
export type MeetingBotStatus =
  | "requested"
  | "joining"
  | "waiting_room"
  | "in_call"
  | "processing"
  | "done"
  | "failed"
  | "left"
  | "not_admitted";

export interface MeetingBot {
  id: string;
  meeting_id: string;
  status: MeetingBotStatus;
  provider: string;
  meeting_url: string;
  bot_name: string | null;
  error: string | null;
  meeting_title: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** Snapshot pushed over the per-meeting SSE progress stream. */
export interface MeetingEvent {
  status: MeetingStatus;
  title: string | null;
  has_summary: boolean;
  runs: {
    kind: string;
    status: string;
    stage: string | null;
    chunk_done: number;
    chunk_total: number;
    error: string | null;
  }[];
}

/** A meeting being captured right now — powers the "live now" presence dock. */
export interface LiveSession {
  id: string;
  meeting_id: string;
  source: "bot" | "browser";
  owner_email: string | null;
  status: "live" | "ended";
  /** Opt-in, off by default; stored in Phase A, acted on by the orchestrator. */
  copilot_enabled: boolean;
  mode: "listening" | "interactive" | "speaking";
  /** May this session ask the business agents (CRM, tasks) for background? */
  deep_context: boolean;
  started_at: string | null;
  ended_at: string | null;
  title: string | null;
}

/** One live-transcript segment off the bus (bot or in-browser recorder). */
export interface LiveSegment {
  text: string;
  start_s: number;
  end_s: number;
  /** Stable across chunks — assigned by the live voiceprint gallery. */
  speaker_id: string | null;
  speaker_label: string | null;
  /** Bound live from a self-introduction ("I'm Priya"), when detected. */
  speaker_name: string | null;
  role: string | null;
  is_final: boolean;
  ts: number | null;
}

/** Who's on the call so far, per the live speaker registry. */
export interface LiveSpeaker {
  speaker_id: string;
  name: string | null;
  role: string | null;
  utterances: number;
}

/** Something the copilot surfaced during a live session. */
export interface CopilotEvent {
  kind: "suggestion" | "question" | "answer" | "fact" | "status";
  text: string;
  /** What it was grounded in — the window, speakers and trigger. */
  refs: {
    window?: string;
    speakers?: string[];
    topic?: string;
    trigger?: string;
    /** Mid-call agent lookup that grounded this (e.g. crm, tasks). */
    consulted?: string[];
    /** The question a `fact` event answered. */
    question?: string;
  };
  token_cost: number;
  ts: number | null;
}

/** What the worker's browser saw when a join failed (bot diagnostics). */
export interface BotDiagnostics {
  status: MeetingBotStatus | string;
  error: string | null;
  diagnostics: {
    status?: string;
    error?: string | null;
    has_screenshot?: boolean;
    diagnostics?: {
      tag?: string;
      url?: string | null;
      title?: string | null;
      controls?: string[];
      body?: string;
    };
  } | null;
  diagnostics_error?: string;
}

/** The notetaker's own Google identity.
 *
 *  Google auto-declines anonymous participants, so `signed_in` is the single
 *  fact that decides whether unattended joining can work at all. */
export interface BotIdentity {
  /** False when the active provider manages its own bot accounts (Recall) —
   *  the UI hides the whole section rather than offering a no-op. */
  supported: boolean;
  /** null when the worker couldn't be reached — distinct from "signed out". */
  signed_in: boolean | null;
  email?: string | null;
  /** Whether a persistent Chrome profile is configured. Without one a sign-in
   *  cannot survive a restart, so it would be pointless. */
  profile?: boolean;
  credentials_configured?: boolean;
  interactive_running?: boolean;
  /** Interactive sign-in needs VNC to actually be watchable. */
  vnc_enabled?: boolean;
  unreachable?: boolean;
  error?: string;
  provider?: string;
}

/** What the copilot knows about a meeting before anyone speaks. */
export interface MeetingContext {
  /** Layer 1 — what you told it. The highest-value source. */
  brief: string;
  attendees: string[];
  /** Layer 2 — past meetings with the same people. */
  past: string[];
  open_actions: string[];
  /** Layer 3 — what the business agents reported (CRM, tasks). */
  systems: Record<string, string>;
  is_empty: boolean;
}

/** One thing to cover in a meeting. Structured so it can be measured live. */
export interface AgendaItem {
  title: string;
  notes: string;
}

/** Live agenda coverage — what's been discussed, and how long you've had. */
export interface AgendaProgress {
  items: { title: string; notes: string; covered: boolean }[];
  covered_count: number;
  total: number;
  elapsed_s: number;
}

/** Note Taker settings — one row per user, read/written whole. */
export interface NotesSettings {
  copilot_instructions: string;
  copilot_default_on: boolean;
  copilot_sensitivity: "low" | "normal" | "high";
  /** auto = stream only while the copilot listens (saves per-minute ASR);
   *  always = stream every meeting; never = record-and-transcribe only. */
  live_transcription: "auto" | "always" | "never";
  /** Per-meeting-type overrides. Absent key = use the shipped default. */
  template_instructions: Record<string, string>;
  default_template: string | null;
  bot_name: string | null;
  /** Auto-dispatch confident action items after notes generation, per kind.
   *  Emails are additionally recipient-gated server-side: an unresolvable
   *  recipient always downgrades a send to a mailbox draft. */
  auto_dispatch_tasks: boolean;
  auto_dispatch_emails: boolean;
  auto_dispatch_docs: boolean;
}

/** A meeting type, with the copilot guidance shipped for it. */
export interface TemplateInfo {
  key: string;
  label: string;
  copilot_default: string;
  agenda_hint: string;
}

export interface NotesSettingsPayload {
  settings: NotesSettings;
  templates: TemplateInfo[];
  sensitivities: string[];
}
