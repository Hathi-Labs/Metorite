export interface EmailAddress {
  name: string;
  email: string;
}

export interface Attachment {
  id: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  downloadUrl?: string;
}

export interface Email {
  id: string;
  providerMessageId: string;
  threadId?: string;
  /** Number of messages in this conversation (1 = standalone). */
  threadCount?: number;
  accountId: string;
  from: EmailAddress;
  to: EmailAddress[];
  cc?: EmailAddress[];
  bcc?: EmailAddress[];
  subject: string;
  bodyText: string;
  bodyHtml?: string;
  bodyTruncated: boolean;
  snippet: string;
  hasAttachments: boolean;
  attachments?: Attachment[];
  isRead: boolean;
  isStarred: boolean;
  isFlagged: boolean;
  /** Provider importance: 'high' | 'normal' | 'low' (Outlook), mapped for Gmail. */
  importance: "high" | "normal" | "low";
  /** Provider labels (Gmail) — used for folder/label chips. */
  labels: string[];
  /** Outlook categories / Gmail user labels with optional colors. */
  categories: string[];
  folder: string;
  receivedAt: string; // ISO 8601
  syncedAt: string;
  /** When set and in the future, the conversation is snoozed out of the inbox
   *  until this time (ISO 8601). Null/absent for normal mail. */
  snoozedUntil?: string | null;
  /** Search-only: relevance score (ts_rank_cd) — present on /email/search hits. */
  rank?: number;
  /** Search-only: highlighted snippet (<mark>…</mark>) showing why it matched. */
  highlight?: string;
}

export interface EmailAccount {
  id: string;
  provider: "gmail" | "microsoft" | "imap";
  emailAddress: string;
  label: string;
  avatar: string; // initials
  color: string; // hex color for avatar bg
  unreadCount: number;
  syncEnabled: boolean;
  lastSyncedAt?: string;
  /** Provider sync state: 'idle' | 'syncing' | 'error'. */
  syncStatus?: string;
  /** Last sync error (e.g. expired OAuth token), when syncStatus is 'error'. */
  syncError?: string;
  /** The user's default mailbox — the inbox the UI lands on. At most one. */
  isDefault?: boolean;
  /**
   * False while the first sync of a new mailbox runs (EM-T3a item 5). Absent
   * from a gateway that predates the flag, and absent means "do not wait".
   */
  initialSyncDone?: boolean;
}

// ── Contact card (the people card behind a sender's name/avatar) ────────────

/** One of the contact's recent messages, as a small card in the popover. */
export interface ContactRecentMessage {
  id: string;
  threadId?: string | null;
  accountId: string;
  subject: string;
  /** Truncated body preview, already shortened server-side. */
  preview: string;
  receivedAt?: string | null;
  isRead: boolean;
  hasAttachments: boolean;
  folder: string;
}

/** What the person's own email signature says about them. Every field is
 *  optional — a personal address usually yields nothing but the domain. */
export interface ContactDetails {
  phones: string[];
  title?: string | null;
  organization?: string | null;
  links: string[];
  /** The message the signature was read from, so the card can cite it. */
  sourceMessageId?: string | null;
}

export interface ContactStats {
  /** Messages received FROM this person. */
  received: number;
  /** Messages the user sent TO this person (To or Cc). */
  sent: number;
  unread: number;
  threads: number;
  firstSeen?: string | null;
  lastSeen?: string | null;
}

export interface ContactCard {
  email: string;
  name?: string | null;
  domain?: string | null;
  /** Sender rollup category (Newsletter / Conversation / …), when assigned. */
  category?: string | null;
  /** Cleaner disposition for this sender. */
  status: SenderStatus;
  stats: ContactStats;
  details: ContactDetails;
  recent: ContactRecentMessage[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

export interface QuickAction {
  label: string;
  action: string; // backend action key: 'summarize' | 'find_urgent' | 'draft_reply' | 'unsubscribe'
  prompt: string;
  icon: string; // Lucide icon name
}

/** The Email Automation features surfaced in the sidebar. */
export type AutomationFeature =
  | "chat"
  | "ai-settings"
  | "digest"
  | "unsubscribe"
  | "analytics";

export type DigestFrequency = "OFF" | "DAILY" | "WEEKLY";

/** One open loop (a thread in a Reply-Zero status), with enough identity to
 *  open it: the thread, its last message, and who the loop is with. */
export interface DigestThread {
  subject: string;
  age_days: number;
  thread_id: string;
  message_id: string | null;
  who: string;
  /** Priority signals (needs-reply queue, which the dashboard ranks by urgency
   *  rather than pure age). Let the row show WHY it's near the top. */
  important?: boolean;
  unread?: boolean;
}

export interface DigestData {
  period_days: number;
  totals: {
    inbox: number;
    unread: number;
    attachments: number;
    needs_reply: number;
    /** Threads where the user replied and the other side owes the next move. */
    awaiting: number;
  };
  by_category: { category: string; count: number }[];
  top_senders: { name: string; email: string; count: number }[];
  /** Oldest threads still awaiting the user's reply (Reply-Zero backlog, aged). */
  backlog?: DigestThread[];
  /** Who the user is WAITING ON — AWAITING threads, longest first. */
  awaiting?: DigestThread[];
  /** Open commitments (tasks captured from a sent reply); due may be null. */
  commitments?: {
    title: string;
    due: string | null;
    overdue: boolean;
    task_id?: string;
    thread_id?: string | null;
    /** Latest message of the linked thread — lets the row open the email the
     *  commitment came from. Null when the thread has no mirrored mail. */
    message_id?: string | null;
  }[];
  /** Opt-in LLM one-liner orienting the day ("2 urgent: X's quote, Y's
   *  contract"). Empty unless morning_brief_enabled is on. */
  brief?: string;
  markdown: string;
}

/** Sender categories (mirrors EMAIL_CATEGORIES in senders.py). The first six
 *  are rolled up from the rule engine's per-message labels. "Conversation" is
 *  derived instead — an ongoing exchange with that sender, never written onto a
 *  message — and Support/Unknown are vocabulary only, with no producer. */
export const EMAIL_CATEGORIES = [
  "Newsletter", "Marketing", "Receipt", "Calendar", "Notification",
  "Cold Email", "Conversation", "Support", "Unknown",
] as const;

export type ColdBlockerMode = "OFF" | "LABEL" | "ARCHIVE";

export interface ColdSender {
  from_email: string;
  status: "AI_LABELED_COLD" | "USER_REJECTED_COLD";
  reason: string | null;
  updated_at: string | null;
}

// ── Analytics ──────────────────────────────────────────────────────────────

/** One age band of the reply backlog. Labels come from the server so the two
 *  sides always bucket identically. */
export interface BacklogBucket {
  label: string;
  count: number;
}

export interface BacklogSide {
  threads: number;
  oldest_days: number | null;
  buckets: BacklogBucket[];
}

/** A sender whose mail you have never once replied to, in this window. */
export interface NoisySender {
  email: string;
  name: string;
  messages: number;
  unread: number;
  read: number;
  /** This sender's rate annualised — what silencing them is worth going forward. */
  projected_yearly: number;
  has_unsubscribe: boolean;
  last_seen: string | null;
}

export interface AnalyticsOverview {
  range: { days: number };
  /** Mail in and out over the window, each with the preceding window for trend. */
  flow: {
    received: number;
    received_prev: number;
    sent: number;
    sent_prev: number;
    unread: number;
    auto_handled: number;
    auto_handled_rate: number;
  };
  /** Per-THREAD response behaviour. `median_hours` is null when nothing in the
   *  window was replied to at all — which is not the same as "instant". */
  responsiveness: {
    inbound_threads: number;
    replied_threads: number;
    unreplied_threads: number;
    reply_rate: number;
    reply_rate_prev: number;
    median_hours: number | null;
    p90_hours: number | null;
    median_hours_prev: number | null;
  };
  /** Standing state, NOT windowed — label it "right now" in the UI, never with
   *  the range selector's caption. */
  backlog: {
    needs_reply: BacklogSide;
    awaiting: BacklogSide;
    /** How much of the mailbox Reply Zero has classified. Without this the two
     *  counts above are unreadable — a small backlog may just be a blind one. */
    coverage: { total: number; classified: number; rate: number };
  };
  volume: { day: string; received: number; sent: number }[];
  categories: { category: string; count: number; prev_count: number }[];
  noisy_senders: NoisySender[];
  /** Assistant automation: emails handled, by-rule breakdown, and whether it
   *  can be trusted to keep doing it. */
  rule_stats?: {
    processed: number;
    by_rule: { rule_name: string; count: number }[];
    trust: {
      decided: number;
      rejected: number;
      rejection_rate: number;
      /** Failed rule runs the Repair button can replay (message still present,
       *  a rule to re-apply). Matches retry_failed_executions exactly. A level,
       *  not windowed. */
      repairable: number;
      /** Failed rule runs no replay can fix — the message was moved or deleted. */
      permanent_failures: number;
      /** Learned patterns queued for review. Inert until approved. */
      unreviewed_patterns: number;
    };
  };
  /** Breakdown of actions the assistant took (LABEL/ARCHIVE/…). */
  action_stats?: { action: string; count: number }[];
  /** Regression alarm for the one-classification-per-conversation invariant.
   *  damaged_threads is 0 when healthy; non-zero means the cleaner/runner
   *  re-damaged statused conversations and the repair path needs to run. */
  data_health?: { damaged_threads: number };
}

// ── Senders (bulk archive / unsubscribe) ────────────────────────────────────

/** A correction that teaches the CLASSIFIER, rather than bypassing it.
 *
 *  The counterpart to LearnedRulePattern. A pattern REPLACES the model's
 *  judgment for one sender; guidance CHANGES it for everyone — so a correction
 *  about "vendor product digests are Newsletter" generalises to every vendor
 *  instead of exempting the one that was wrong. Stored apart so the Learned
 *  Patterns screen can separate "improves the AI" from "replaces the AI". */
export interface RuleGuidance {
  id: string;
  /** null = applies to the whole classification prompt, not one rule. */
  rule_id: string | null;
  rule_name: string | null;
  guidance: string;
  source: string; // FIX | USER
  thread_id: string | null;
  created_at: string | null;
}

/** A learned classification pattern (sender → rule include/exclude) — the real
 *  "Learned Patterns", taught by the Fix flow / label corrections. */
export interface LearnedRulePattern {
  id: string;
  rule_id: string;
  rule_name: string | null;
  pattern_type: string; // FROM | SUBJECT
  value: string;
  exclude: boolean;
  source: string; // FIX | LABEL_ADDED | LABEL_REMOVED | AI | USER
  reason: string | null;
  created_at: string | null;
  /** Roughly how many messages in the mailbox this pattern matches. A ceiling:
   *  the server approximates _pattern_hit with a substring match, skipping the
   *  generalised-subject and address-boundary refinements. */
  reach?: number;
  /** Review state. A pattern the user authored (Fix, a label changed in their
   *  mail client, a hand-typed rule) is approved on creation; only auto-learned
   *   'AI' patterns arrive unreviewed, and the Email Cleaner will not project
   *  those until they are approved. */
  approved_at?: string | null;
  rejected_at?: string | null;
}

export type NewsletterStatus = "APPROVED" | "UNSUBSCRIBED" | "AUTO_ARCHIVED";
/** Display status for a sender — adds "UNHANDLED" (no decision made yet). Only
 *  the three NewsletterStatus values are ever persisted via upsertNewsletter. */
export type SenderStatus = NewsletterStatus | "UNHANDLED";

export interface SenderStat {
  email: string;
  name: string;
  /** Total messages counted for this sender. For a sender with a saved
   *  disposition this reaches outside the requested folder (so an unsubscribed
   *  sender doesn't disappear from the list) — use `in_folder` for "how much is
   *  still in the inbox". */
  count: number;
  /** How many of `count` are actually in the requested folder. */
  in_folder?: number;
  unread: number;
  archived: number;
  read_rate: number;
  last_received: string | null;
  unsubscribe_link: string | null;
  status: SenderStatus;
  /** True when a provider-native auto-archive filter is in place for this sender
   *  (future mail blocked at the source — Gmail filter / Outlook rule). */
  filter_active?: boolean;
  /** Dominant cleanup category for this sender, derived from the rule-labelled
   *  per-message categories (never a provisional guess). null = uncategorized. */
  category?: string | null;
  /** All distinct cleanup categories present on this sender's mail — powers the
   *  category filter tabs in the Email Cleaner. A sender legitimately appears
   *  under several: a colleague sends both Calendar invites and Notifications. */
  categories?: string[];
  /** How many of this sender's messages carry each cleanup category. With a
   *  category tab open, the row must report THIS number rather than `count` —
   *  otherwise a person with 69 messages of which 7 are notifications reads as
   *  69 notifications. */
  category_counts?: Record<string, number>;
  /** Provenance of `category`. Always 'rule'/'user' now (provisional 'inferred'
   *  guesses were removed); retained for forward-compat. */
  category_source?: string | null;
  /** How many of this sender's messages carry ANY rule label (cleanup or
   *  conversation). 0 means the rules never reached this sender — which is a
   *  different problem from "the rules ran and found nothing to clean up", and
   *  is what the Email Cleaner's "Uncategorized" filter surfaces. */
  labelled?: number;
}

/** Result of a real unsubscribe attempt (POST /email/unsubscribe). */
export interface UnsubscribeResult {
  ok: boolean;
  /** What was actually done: a one-click POST, a mailto send, or a block
   *  (auto-archive + provider filter) when no usable link / the attempt failed. */
  method: "one-click" | "mailto" | "blocked" | "none";
  detail: string;
  status: NewsletterStatus;
  archived: number;
  unsubscribe_link: string | null;
}

// ── Assistant rules ─────────────────────────────────────────────────────────

export type RuleActionType =
  | "ARCHIVE"
  | "LABEL"
  | "MARK_READ"
  | "STAR"
  | "MARK_SPAM"
  | "TRASH"
  | "MOVE_FOLDER"
  | "REPLY"
  | "FORWARD"
  | "DRAFT_EMAIL"
  | "CALL_WEBHOOK";

/** A draft attachment sourced from the email-assistant workspace. */
export interface RuleActionAttachment {
  /** Workspace-relative path the file lives at (e.g. agent-data/budget.pdf). */
  path?: string | null;
  artifact_id?: string | null;
  name?: string | null;
  /** AI-selected sources the assistant may pick from, vs always-attach. */
  ai_selected?: boolean;
}

export interface RuleAction {
  id?: string;
  type: RuleActionType;
  label?: string | null;
  subject?: string | null;
  content?: string | null;
  to_address?: string | null;
  cc_address?: string | null;
  bcc_address?: string | null;
  url?: string | null;
  /** Minutes to wait before running this action (inbox-zero delay). */
  delay_minutes?: number | null;
  /** Draft attachments (from artifacts) for draft/reply/forward actions. */
  attachments?: RuleActionAttachment[];
  /** LABEL: `label` is an AI prompt ({{…}}) resolved per-email, not a fixed label. */
  label_ai?: boolean;
  /** Draft: use the authored `content` template (false = the AI writes the body). */
  content_manual?: boolean;
}

export interface AutomationRule {
  id?: string;
  account_id: string;
  name: string;
  instructions?: string | null;
  enabled: boolean;
  /** Apply actions automatically (true) or only propose for approval (false). */
  automated: boolean;
  run_on_threads: boolean;
  conditional_operator: "AND" | "OR";
  from_pattern?: string | null;
  to_pattern?: string | null;
  subject_pattern?: string | null;
  body_pattern?: string | null;
  system_type?: string | null;
  actions: RuleAction[];
}

export interface RuleTestResult {
  matched: boolean;
  rule: { id: string; name: string } | null;
  reason: string;
  actions: RuleAction[];
}

/** The rule conditions surfaced in a history row's hover popover. */
export interface RuleConditions {
  instructions: string | null;
  from_pattern: string | null;
  to_pattern: string | null;
  subject_pattern: string | null;
  body_pattern: string | null;
  conditional_operator: "AND" | "OR";
}

export interface ExecutedRule {
  id: string;
  rule_id?: string | null;
  rule_name: string | null;
  subject: string | null;
  from: string | null;
  /** APPLIED | SKIPPED | PENDING | REJECTED | UNDONE | ERROR */
  status: string;
  automated: boolean;
  /** Action types actually taken (e.g. ["LABEL", "ARCHIVE"]). */
  actions: string[];
  reason: string | null;
  snippet?: string;
  created_at: string | null;
  /** The email's received date — History is ordered/grouped by this (mail date). */
  received_at?: string | null;
  /** The underlying email's id — for opening a preview / re-running rules on it. */
  message_id?: string | null;
  /** Labels/categories currently on the email (inbox-zero shows these per row). */
  labels?: string[] | null;
  /** Which condition type fired: "pattern" | "static" | "ai" (matched via …). */
  match_source?: string | null;
  /** Actions that failed during the run ({type, error}) — "Action issues". */
  action_errors?: { type: string; error: string }[] | null;
  /** The AI draft generated for this thread (DRAFT_EMAIL), to preview in the pill. */
  draft_preview?: string | null;
  /** Matched rule's conditions, for the hover popover (null when no match). */
  conditions?: RuleConditions | null;
  /** Matched rule's full action specs (label/to/subject…), for the popover. */
  rule_actions?: RuleAction[];
}

/** One event in a single message's audit timeline (GET /messages/{id}/timeline).
 *  "received" = the mail arrived; "rule" = a rule matched & acted; "skipped" =
 *  a run where nothing matched. */
export interface MessageTimelineEvent {
  kind: "received" | "rule" | "skipped";
  /** ISO timestamp; null only for malformed audit rows. */
  at: string | null;
  /** received: sender display name/email. */
  from?: string;
  from_email?: string;
  /** rule/skipped: */
  rule_id?: string | null;
  rule_name?: string | null;
  /** APPLIED | SKIPPED | FAILED | UNDONE | REJECTED … */
  status?: string;
  automated?: boolean;
  actions?: string[];
  action_errors?: { type: string; error: string }[];
  match_source?: string | null;
  reason?: string | null;
}

export interface MessageTimeline {
  message_id: string;
  subject: string;
  events: MessageTimelineEvent[];
}

/** Result of running rules against a single message (Test tab Test/Apply). */
export interface RunMessageResult {
  matched: boolean;
  applied: boolean;
  rule: { id: string; name: string } | null;
  reason: string;
  /** Full action specs when matched (test mode) or types taken (apply mode). */
  actions: (RuleAction | string)[];
  /** Every rule name applied in multi-rule execution (apply mode). */
  applied_rules?: string[];
  /** Post-apply category array on the message (apply mode) — lets the caller
   *  refresh the inbox row so a reran "Uncategorized" pill resolves in place. */
  categories?: string[];
  /** Post-apply folder (apply mode) — the actions may have archived/moved it. */
  folder?: string | null;
  /** True when the AI classifier was unreachable — a backend outage, NOT a
   *  rules gap. Callers must not treat this like "no rule matched". */
  unavailable?: boolean;
}

export interface AssistantSettings {
  account_id: string;
  about: string;
  signature: string;
  /** The SERVER's plain-text rendering of the signature — the composers seed
   *  this exact text into the body so the backend's idempotent re-signing
   *  recognises it (a client-side re-derivation could drift by whitespace and
   *  get double-signed). */
  signature_text?: string;
  auto_run: boolean;
  cold_email_blocker: ColdBlockerMode;
  /** Model for rule evaluation / classification / labeling (default tier-fast). */
  rule_model: string;
  /** Model for BACKGROUND draft writing — follow-ups, DRAFT_EMAIL rule actions
   *  (default tier-powerful). */
  draft_model: string;
  /** Model for MANUAL drafting — the composer's "Draft with AI" button, where
   *  the user is waiting (default tier-fast). */
  compose_model: string;
  /** Model the interactive email chat panel uses (default tier-powerful). */
  chat_model: string;
  /** Scheduled inbox-digest cadence. */
  digest_frequency: DigestFrequency;
  /** Global "always do this" guidance for the assistant. */
  personal_instructions: string;
  /** Tone/length/style guidance for drafted replies (can be auto-derived). */
  writing_style: string;
  /** Read-only: style distilled from how the user edits this account's drafts.
   *  Advisory — an explicit `writing_style` outranks it. */
  learned_writing_style?: string;
  /** Whether the assistant drafts replies for emails that need one. */
  draft_replies: boolean;
  /** Legacy alias for follow_up_awaiting_days (kept for back-compat). */
  follow_up_days: number;
  /** How sure the AI must be before drafting a reply. */
  draft_confidence: DraftConfidence;
  /** Remind when THEY haven't replied after N days (0 = off). */
  follow_up_awaiting_days: number;
  /** Remind when I haven't replied after N days (0 = off). */
  follow_up_needs_reply_days: number;
  /** Auto-draft a nudge for awaiting threads. */
  follow_up_auto_draft: boolean;
  /** Rule names (+ "Cold Emails") to include in the digest; empty = all. */
  digest_categories: string[];
  /** 0=Sun … 6=Sat — used when digest_frequency is WEEKLY. */
  digest_day_of_week: number;
  /** HH:MM (24h, UTC) the digest is sent. The backend treats this as UTC; there
   *  is no per-account timezone yet, so the UI labels it UTC to stay honest. */
  digest_time_of_day: string;
  /** Email the digest to the account address. */
  digest_send_to_email: boolean;
  /** Opt-in AI one-liner atop the dashboard + digest (default off; costs a
   *  model call each build). */
  morning_brief_enabled?: boolean;
  /** Allow more than one rule to run on the same email (inbox-zero multi-rule). */
  multi_rule_execution: boolean;
  /** Skip auto-drafting on emails that look like they carry sensitive data. */
  sensitive_data_protection: boolean;
  /** Extra "your organisation" domains (beyond the account's own) whose mail is
   *  treated as internal/outbound for direction-aware classification. */
  org_domains: string[];
  /** Read-only: the account's own email domain, always treated as internal.
   *  Returned by GET/PUT for display; never sent back to change it. */
  own_domain?: string;
}

export type DraftConfidence = "ALL_EMAILS" | "STANDARD" | "HIGH_CONFIDENCE";

export const DRAFT_CONFIDENCE_OPTIONS: {
  value: DraftConfidence;
  label: string;
  description: string;
}[] = [
  {
    value: "ALL_EMAILS",
    label: "All emails",
    description: "Draft a reply for every email, even when uncertain.",
  },
  {
    value: "STANDARD",
    label: "Standard",
    description: "Skip drafting when the AI is unsure how to respond.",
  },
  {
    value: "HIGH_CONFIDENCE",
    label: "High confidence",
    description: "Only draft when the AI is very sure of the right reply.",
  },
];

/** Sunday-first weekday labels for the digest day-of-week selector. */
export const WEEKDAYS = [
  "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
] as const;

/** A preference the assistant learned from how the user edits its drafts. */
export interface LearnedPattern {
  id: string;
  pattern: string;
  weight: number;
  /** FACT | PROCEDURE | PREFERENCE */
  kind?: string | null;
  /** SENDER | DOMAIN | TOPIC | GLOBAL */
  scope_type?: string | null;
  /** The sender email / domain / topic keyword for non-global scopes. */
  scope_value?: string | null;
}

/** A draft knowledge-base entry the assistant draws on when writing replies. */
export interface KnowledgeEntry {
  id?: string;
  account_id: string;
  title: string;
  content: string;
  /** 'manual' (user-authored) | 'voice_profile' (proposed by the profile builder). */
  source?: string;
  /** 'active' (feeds drafts) | 'suggested' (awaiting the user's approval). */
  status?: string;
  updated_at?: string | null;
}

/** Structured writing-style traits learned by the voice-profile builder. */
export interface VoiceProfileTraits {
  tone?: string;
  formality?: string;
  typical_length?: string;
  sentence_style?: string;
  emoji_usage?: string;
  formatting_habits?: string;
  greetings?: string[];
  signoffs?: string[];
  common_phrases?: string[];
  languages?: string[];
  dos?: string[];
  donts?: string[];
}

export type VoiceProfileStatus = "EMPTY" | "BUILDING" | "READY" | "FAILED";

/** The account's voice & writing-style profile, learned from past mail. */
export interface VoiceProfile {
  account_id: string;
  status: VoiceProfileStatus;
  /** Whether the profile feeds the drafting prompt (READY profiles only). */
  enabled: boolean;
  /** Narrative style guide — the prompt-facing text, hand-editable. */
  style_guide: string;
  traits: VoiceProfileTraits;
  /** Folders it was learned from: 'sent' and/or 'drafts'. */
  sources: string[];
  range_start: string | null;
  range_end: string | null;
  analyzed_count: number;
  built_at: string | null;
  last_error: string | null;
  /** Knowledge entries the builder proposed that still await review. */
  suggested_knowledge: number;
}

/** Live counts for the builder's range picker. */
export interface VoiceProfilePreview {
  sent: number;
  drafts: number;
  total: number;
  will_analyze: number;
  cap: number;
}

/** Progress of a running (or the most recent) voice-profile build. */
export interface VoiceProfileBuildStatus {
  status: "idle" | "running" | "done" | "error";
  phase?: "collecting" | "analyzing" | "synthesizing" | "knowledge" | "done";
  processed?: number;
  total?: number;
  sample_count?: number;
  suggested_knowledge?: number;
  error?: string;
}

/** A configurable LLM tier/provider, from GET /api/settings/llm. */
export interface LLMTier {
  tier_name: string;
  tier_id: string;
  model: string;
  provider: string;
  provider_configured: boolean;
}

export interface LLMProvider {
  id: string;
  label: string;
  configured: boolean;
  models: string[];
}

export interface LLMConfigResponse {
  tiers: LLMTier[];
  providers: LLMProvider[];
}

export interface RecentTestResult {
  email_id: string;
  subject: string;
  from: string;
  matched: boolean;
  rule: { id: string; name: string } | null;
  reason: string;
  actions: string[];
}

export interface EmailFolder {
  icon: string; // Lucide icon name
  label: string;
  key: string;
  count: number;
  /** 'system' for canonical folders, 'user' for provider-created folders/labels. */
  type?: "system" | "user";
  /** Unread count from the provider, when available. */
  unread?: number;
}

/** A user-applicable label/category with its assigned colour. */
export interface LabelInfo {
  name: string;
  /** Canonical preset token ('preset0'..'preset24'), or null when unset. */
  color: string | null;
}
