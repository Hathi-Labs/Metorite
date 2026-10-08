// Wire shapes returned by the gateway /whatsapp/* routes (see
// apps/services/gateway/gateway/routes/whatsapp/core.py). Kept in sync by hand —
// a small, stable surface for the read-only W0 app.

import { themedIcon } from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";

export type WaAccount = {
  id: string;
  phone_number: string;
  phone_number_id: string;
  waba_id: string | null;
  display_name: string;
  avatar_color: string;
  sync_status: string;
  sync_error: string | null;
  history_import_phase: number;
  quality_rating: string | null;
  last_synced_at: string | null;
  is_default: boolean;
  /** 'cloud' (Meta Cloud API) or 'whatsmeow' (QR-paired personal bridge).
   *  Voice calling exists only on the bridge transport. */
  provider?: string;
  /** WS-20 WA-C3: the coexistence history import. null for an account that
   *  is not coexistence. Else pending | requested | failed | declined |
   *  complete. */
  history_sync_state?: string | null;
  history_sync_error?: string | null;
  /** Meta's progress, 0 to 100, of the highest phase seen. */
  history_import_progress?: number | null;
  /** ISO time when Meta's 24-hour window for the sync closes. */
  history_sync_deadline?: string | null;
  /** True when this server runs the history sync (`WHATSAPP_HISTORY_SYNC`).
   *  While false, the UI offers no start and no reconnect advice. */
  history_sync_available?: boolean;
};

/** One voice call as the bridge reports it. Mirrors callInfo in calls.go. */
export type WaCall = {
  call_id: string;
  account_id: string;
  peer: string;
  direction: "outgoing" | "incoming" | "";
  kind: "direct" | "group" | "";
  phase: string;
  targets?: string[];
  started_at?: string;
  ended_at?: string;
  end_reason?: string;
  recording?: string;
  /** Peer audio actually received. Zero on a connected call means signalling
   *  worked but media never flowed. */
  audio_seconds?: number;
  /** Relay bound and frames moving. The authoritative "audio can attach now"
   *  signal — the phase string is advisory and may never read "active". */
  media_ready?: boolean;
};

/** Playable URL for a call's recording, via the binary-safe proxy route. */
export function callRecordingUrl(accountId: string, callId: string): string {
  return `/api/whatsapp/calls/${encodeURIComponent(
    callId
  )}/recording?account_id=${encodeURIComponent(accountId)}`;
}

export type WaCallList = {
  calls: WaCall[];
  bridge_reachable: boolean;
};

/** One server-side thing that happened to a call, in order. */
export type WaCallEvent = { at: string; kind: string; detail: string };

export type WaCallTimeline = {
  call: WaCall | null;
  events: WaCallEvent[];
  bridge_reachable: boolean;
};

/** Whether a paired number can place a call, and if not, what's missing. */
export type WaCallDiagnostics = {
  account_id: string;
  session_exists: boolean;
  logged_in: boolean;
  connected: boolean;
  caller_ready: boolean;
  own_jid?: string;
  push_name?: string;
  active_calls: number;
  recording_dir?: string;
  verdict: string;
  bridge_reachable: boolean;
};

// Connect wizard (W11) + Embedded Signup (W12).
export type WaConnectionInfo = {
  webhook_url: string;
  webhook_path: string;
  verify_token: string;
  base_configured: boolean;
  embedded_signup: boolean;
  fb_app_id: string;
  es_config_id: string;
  graph_version: string;
};

export type WaEmbeddedResult = {
  account_id: string;
  display_name: string;
  phone_number: string;
  subscribed: boolean;
  /** WS-20 WA-C3: null for a plain Cloud connect. */
  history_sync?: "requested" | "failed" | "pending" | null;
};

/** `POST /whatsapp/accounts/{id}/history-sync` (WS-20 WA-C3 P4). */
export type WaHistorySyncResult = {
  history_sync: "requested" | "failed";
  history_sync_error: string | null;
};

export type WaVerifyResult = {
  ok: boolean;
  display_phone_number: string | null;
  verified_name: string | null;
  quality_rating: string | null;
  error: string | null;
};
// A native WhatsApp label as it hangs off a chat row (mirrored read-only, W16).
export type WaChatLabel = {
  wa_label_id: string;
  name: string;
  color: string | null;
};

export type WaChat = {
  id: string;
  account_id: string;
  wa_chat_id: string;
  kind: string;
  name: string;
  category: string | null;
  status: string | null; // NEEDS_REPLY | AWAITING | FYI | DONE
  last_message_at: string | null;
  last_snippet: string;
  window_open: boolean;
  window_expires_at: string | null;
  snoozed_until: string | null; // set while snoozed (W6)
  labels: WaChatLabel[]; // native WhatsApp labels on this chat (W16)
  avatar_url: string | null; // native WhatsApp profile picture, synced (W17)
};

// A native WhatsApp label/list the founder created, synced from their number
// (W16) — distinct from WaCategory, which is our policy carrier. Read-only.
export type WaLabel = {
  wa_label_id: string;
  name: string;
  color: string | null;
  color_index: number | null;
  list_type: string | null;
  sort_order: number;
  chat_count: number;
};

export type WaMessage = {
  id: string;
  chat_id: string;
  wa_message_id: string;
  direction: string; // 'in' | 'out'
  kind: string;
  sender_name: string;
  body_text: string;
  transcript_text: string | null; // voice-note transcription (W4.3)
  quoted_wa_message_id: string | null;
  categories: string[];
  intent: string | null;
  send_regime: string | null;
  sent_at: string | null;
};

export type WaStreams = {
  needs_reply: number;
  waiting: number;
  groups: number;
  all: number;
  snoozed: number;
};

export type WaTemplate = {
  id: string;
  name: string;
  language: string;
  category: string;
  body: string;
  variables: string[];
  meta_status: string;
  cost_hint: string | null;
};

export type WaCategory = {
  id: string;
  name: string;
  icon: string | null;
  wa_label_id: string | null;
  notify_policy: string; // instant | digest | mention_only | never
  auto_reply_policy: string; // never | holding | answer_from_system
  draft_policy: string; // always | on_intent | never
  escalate_after_mins: number | null;
  sort_order: number;
};

export type WaSavedReply = {
  id: string;
  title: string;
  body: string;
  shortcut: string | null;
  sort_order: number;
};

export const NOTIFY_POLICIES = ["instant", "digest", "mention_only", "never"];
export const AUTO_REPLY_POLICIES = ["never", "holding", "answer_from_system"];
export const DRAFT_POLICIES = ["always", "on_intent", "never"];

export type WaRulePreviewItem = {
  chat_id: string;
  name: string;
  intent: string | null;
  category: string | null;
  action: string; // answer_from_system | holding_reply | draft | none
  reason: string;
  requires_approval: boolean;
  via_template: boolean;
};

export type WaRulePreview = {
  items: WaRulePreviewItem[];
  summary: Record<string, number>;
};

// WhatsApp Pulse — the founder's "am I keeping up?" projection (W7).
export type WaPulse = {
  window_days: number;
  inbound: number;
  outbound: number;
  active_chats: number;
  response: {
    replied: number;
    median_minutes: number | null;
    p90_minutes: number | null;
  };
  waiting_longest: {
    chat_id: string;
    name: string;
    waited_hours: number;
    snippet: string;
  }[];
  by_intent: { key: string; count: number }[];
  busiest: { chat_id: string; name: string; count: number }[];
};

export type WaEntityRef = { system: string; kind: string; id: string };

export type WaOpenLoop = {
  id: string;
  title: string;
  disposition: string;
  kind: string;
};

// A promise they owe us in this chat — nudgeable by id (W4.2).
export type WaWaitingOn = {
  id: string;
  text: string;
  due_hint: string | null;
};

export type WaChatContext = {
  chat_id: string;
  contact: {
    phone_number: string;
    display_name: string;
    category: string | null;
    entity: WaEntityRef | null;
  } | null;
  open_loops: WaOpenLoop[];
  waiting_on: WaWaitingOn[];
  stats: { message_count: number; first_seen: string | null; last_seen: string | null };
  crm: Record<string, unknown> | null;
};

// The triage streams shown in the nav — the single organizing spine.
// Triage streams. Icons are the native lucide set (rendered as components), not
// emoji, so they inherit the app's colour + sizing like every other icon.
export const STREAMS: { key: string; label: string; icon: ThemedIcon }[] = [
  { key: "needs_reply", label: "Needs reply", icon: themedIcon("Sparkles") },
  { key: "waiting", label: "Waiting on them", icon: themedIcon("Clock") },
  { key: "groups", label: "Groups", icon: themedIcon("Users") },
  { key: "all", label: "All chats", icon: themedIcon("MessageSquare") },
  { key: "snoozed", label: "Snoozed", icon: themedIcon("Moon") },
];
