"use client";

/**
 * EmailToolCards — interactive AG-UI cards for the email-assistant agent's tools.
 *
 * Rendered by the shared <AgentChat> for any assistant message whose tool events
 * include email-assistant tools.  Because it's a no-op when no email tools are
 * present, it's safe to render for EVERY agent — so the rich email cards appear
 * in BOTH the main chat app and the email app.
 *
 * Card types:
 *   • DraftResultCard   — editable draft + Save-to-Drafts + Send (draft_reply)
 *   • RuleResultCard     — rule made/updated + Disable / Delete (create/update_rule)
 *   • SettingsUpdatedCard— what settings changed + Open settings (update_assistant_settings)
 *   • EmailListCard      — clickable results that open the email (search / find_*)
 *   • EmailPreviewCard   — opened email summary + Open (read_email)
 *   • ActionResultCard   — generic confirmation for every other mutating tool
 *
 * Cards self-source account / email / rule ids from the tool-call `args` (the
 * email-assistant tools take `account_id`, `email_id`, `rule_id`), falling back
 * to the optional `accountId` / `emailId` props the email app passes from its
 * current selection — so they work in the chat app too.
 */

import Button from "@/components/ui/Button";
import AppIcon from "@/components/Icon";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import type { ToolEvent } from "@/components/MarkdownMessage";
import {
  saveDraftText, sendDraft, deleteRule, listRules, updateRule,
  fetchFullBody, updateEmail as patchEmail, updateEmailLabels, getEmail, listThread,
  deleteLearnedPattern, deleteRulePattern,
  type FullBodyResponse,
} from "@/app/email/lib/api";
import type { Email } from "@/app/email/lib/types";
import { MessageContent } from "@/app/email/components/MessageContent";
import { LabelChip } from "@/app/email/components/LabelChip";
import { useEmailStore } from "@/app/email/lib/emailStore";
import { ToolCardShell, DismissableCard } from "@/components/ToolCardShell";
import { useDismissedToolCards, dismissToolCard } from "@/lib/dismissedTools";

// ── Tool → card routing ───────────────────────────────────────────────────────

const DRAFT_TOOLS = new Set(["draft_reply", "draft_email"]);
const RULE_TOOLS = new Set(["create_rule", "update_rule"]);
// Tools whose result is a list of emails (each line carries `id=<id>`) — these
// render as a clickable list that opens the message. Includes the assistant's
// primary inbox tools (query_inbox / get_important_emails), not just search.
const LIST_TOOLS = new Set([
  "query_inbox",
  "find_priority",
  // Legacy tool names (pre-consolidation) — kept so older transcripts still
  // render as list cards. The live agent now emits find_priority / query_inbox.
  "get_important_emails",
  "search_emails",
  "find_urgent",
  "find_needs_reply",
]);
const READ_TOOL = "read_email";
const READ_THREAD_TOOL = "read_thread";
const SETTINGS_TOOL = "update_assistant_settings";
// The agent-driven categorized board: emails grouped under LLM-chosen headings
// (HR / Finance / R&D, by project, by urgency…), each an interactive section.
const GROUPS_TOOL = "present_email_groups";

/** Tools that return a human-readable LIST / overview (a header + bullet lines,
 *  with no clickable email ids) — e.g. list_learned_patterns, list_senders.
 *  These used to render NO card, so their output only appeared in the thinking
 *  timeline ("does not show properly"). They render as a titled, scrollable
 *  full-text card (InfoResultCard) so the whole list is visible. */
const INFO_META: Record<string, { icon: string; label: string }> = {
  list_accounts: { icon: "Mail", label: "Accounts" },
  get_unread_count: { icon: "Mail", label: "Unread counts" },
  generate_writing_style: { icon: "PenLine", label: "Writing style" },
  list_labels: { icon: "Tag", label: "Labels" },
  list_artifacts: { icon: "BookOpen", label: "Files" },
  get_sender_categories: { icon: "Tag", label: "Sender categories" },
  list_senders: { icon: "Mail", label: "Top senders" },
  get_rules_and_settings: { icon: "Sparkles", label: "Rules & settings" },
  list_rule_history: { icon: "Clock", label: "Rule history" },
  list_knowledge: { icon: "BookOpen", label: "Knowledge base" },
  list_cold_senders: { icon: "Archive", label: "Cold senders" },
  suggest_unsubscribes: { icon: "Archive", label: "Unsubscribe candidates" },
  get_account_overview: { icon: "Mail", label: "Account overview" },
  digest: { icon: "Send", label: "Digest" },
  get_digest: { icon: "Send", label: "Digest" },
  create_rules_from_prompt: { icon: "Sparkles", label: "Rules created" },
  test_rule_match: { icon: "Wrench", label: "Rule match test" },
};
const INFO_TOOLS = new Set(Object.keys(INFO_META));

/** Learned-pattern tools render an EDITABLE list: each pattern is a row you can
 *  delete (forget). Two separate systems — drafting style (list_learned_patterns)
 *  and rule classification pins from Fix (list_rule_patterns) — each with its own
 *  delete endpoint. */
type PatternMeta = {
  icon: string;
  label: string;
  remove: (id: string) => Promise<void>;
};
const PATTERN_META: Record<string, PatternMeta> = {
  list_learned_patterns: {
    icon: "PenLine", label: "Learned writing preferences", remove: deleteLearnedPattern,
  },
  list_rule_patterns: {
    icon: "Sparkles", label: "Learned rule patterns", remove: deleteRulePattern,
  },
};
// The consolidated list_patterns tool selects the store via its `kind` arg;
// legacy names keep their own entries above so old transcripts still render.
const PATTERN_TOOLS = new Set([...Object.keys(PATTERN_META), "list_patterns"]);

/** The right icon/label/delete-endpoint for a pattern-list event — from the
 *  `kind` arg for the consolidated list_patterns, else from the tool name. */
function patternMeta(e: ToolEvent): PatternMeta {
  if (e.name === "list_patterns") {
    const kind = String((e.args as Record<string, unknown> | undefined)?.kind ?? "draft");
    return kind === "rule" ? PATTERN_META.list_rule_patterns : PATTERN_META.list_learned_patterns;
  }
  return PATTERN_META[e.name] ?? PATTERN_META.list_learned_patterns;
}

/** Friendly label + icon for the generic confirmation card (mutating tools). */
const ACTION_META: Record<string, { icon: string; label: string; danger?: boolean }> = {
  manage_inbox: { icon: "Archive", label: "Inbox updated" },
  apply_labels: { icon: "Tag", label: "Labels updated" },
  move_to_folder: { icon: "FolderInput", label: "Moved to folder" },
  create_label: { icon: "Tag", label: "Label ready" },
  send_email: { icon: "Send", label: "Email sent" },
  send_reply: { icon: "Reply", label: "Reply sent" },
  send_draft: { icon: "Send", label: "Draft sent" },
  delete_rule: { icon: "Trash2", label: "Rule deleted", danger: true },
  reset_rules: { icon: "Sparkles", label: "Rules reset", danger: true },
  run_rules_now: { icon: "Wrench", label: "Rules run" },
  run_rules: { icon: "Wrench", label: "Rules run" },
  update_rule_state: { icon: "Sparkles", label: "Rule updated" },
  resolve_execution: { icon: "CheckCircle2", label: "Execution resolved" },
  learn_rule_pattern: { icon: "Sparkles", label: "Pattern learned" },
  install_default_rules: { icon: "Sparkles", label: "Default rules installed" },
  process_past_emails: { icon: "Clock", label: "Processing past mail" },
  approve_execution: { icon: "CheckCircle2", label: "Approved" },
  reject_execution: { icon: "X", label: "Rejected" },
  undo_execution: { icon: "RefreshCw", label: "Undone" },
  import_artifact: { icon: "BookOpen", label: "Artifact imported" },
  add_knowledge: { icon: "BookOpen", label: "Knowledge added" },
  update_knowledge: { icon: "BookOpen", label: "Knowledge updated" },
  save_knowledge: { icon: "BookOpen", label: "Knowledge saved" },
  delete_knowledge: { icon: "Trash2", label: "Knowledge deleted", danger: true },
  delete_learned_pattern: { icon: "Trash2", label: "Pattern forgotten" },
  delete_rule_pattern: { icon: "Trash2", label: "Pattern forgotten" },
  forget_pattern: { icon: "Trash2", label: "Pattern forgotten" },
  find_follow_ups: { icon: "Clock", label: "Follow-ups scanned" },
  mark_thread_done: { icon: "CheckCircle2", label: "Thread updated" },
  reclassify_reply_zero: { icon: "RefreshCw", label: "Reply Zero reclassifying" },
  unsubscribe_sender: { icon: "Archive", label: "Unsubscribed", danger: true },
  keep_newsletter: { icon: "Mail", label: "Newsletter kept" },
  set_cold_sender: { icon: "Archive", label: "Cold sender updated" },
  set_sender_status: { icon: "Tag", label: "Sender updated" },
  categorize_senders: { icon: "Tag", label: "Categorizing senders" },
  send_digest: { icon: "Send", label: "Digest sent" },
  sync_account: { icon: "RefreshCw", label: "Syncing" },
  resync_account: { icon: "RefreshCw", label: "Re-syncing" },
};

/** True when this completed tool event has a rich card to render. read_email is
 *  handled separately (folded into one group), so it is NOT listed here. */
function hasEmailCard(e: ToolEvent): boolean {
  if (e.status !== "done") return false;
  return (
    DRAFT_TOOLS.has(e.name) ||
    RULE_TOOLS.has(e.name) ||
    LIST_TOOLS.has(e.name) ||
    INFO_TOOLS.has(e.name) ||
    PATTERN_TOOLS.has(e.name) ||
    e.name === READ_THREAD_TOOL ||
    e.name === GROUPS_TOOL ||
    e.name === SETTINGS_TOOL ||
    e.name in ACTION_META
  );
}

/** Dispatch a single tool event to its card component (read_email + read_thread
 *  are handled by the caller — folded group / collapsed thread card). */
function renderCard(
  e: ToolEvent, accountId?: string | null, emailId?: string | null,
): React.ReactNode {
  if (DRAFT_TOOLS.has(e.name)) {
    return <DraftResultCard event={e} accountId={accountId} emailId={emailId} />;
  }
  if (RULE_TOOLS.has(e.name)) return <RuleResultCard event={e} accountId={accountId} />;
  if (e.name === SETTINGS_TOOL) return <SettingsUpdatedCard event={e} />;
  // LIST_TOOLS are merged into ONE card by the caller (see EmailToolCards loop).
  if (e.name === "apply_labels") return <LabelUpdateCard event={e} />;
  if (e.name === "manage_inbox") {
    // manage_inbox(action="label") gets the label-chip card; everything else
    // (archive / trash / read / move / …) gets the inbox-action card.
    const action = String(
      (e.args as Record<string, unknown> | undefined)?.action ?? "",
    ).toLowerCase();
    return action === "label"
      ? <LabelUpdateCard event={e} />
      : <ManageInboxCard event={e} />;
  }
  return <ActionResultCard event={e} />;
}

/** Folded "Read N message(s)" card for a run of read_email context calls — one
 *  collapsed line instead of N full email cards flooding the chat. The full
 *  preview for each only mounts (and fetches its body) once expanded. */
function ReadGroupCard({ events }: { events: ToolEvent[] }) {
  const n = events.length;
  return (
    <ToolCardShell
      title={`Read ${n} message${n > 1 ? "s" : ""}`}
      icon={<AppIcon name="MailOpen" size={12} />}
      defaultCollapsed
      onDismiss={() => events.forEach((e) => dismissToolCard(e.id))}
    >
      <div className="space-y-2">
        {events.map((e) => (
          <EmailPreviewCard key={e.id} event={e} />
        ))}
      </div>
    </ToolCardShell>
  );
}

/** One whole conversation (read_thread) as a SINGLE collapsed card whose body is
 *  the thread's messages — each an expandable row (latest open). This is the key
 *  difference from read_email (one message): a thread is shown as one thread, not
 *  N separate email cards. The body fetches the real conversation (so each
 *  message is openable) and falls back to the tool's text summary if that fails. */
function ThreadCard({ event, accountId }: { event: ToolEvent; accountId?: string | null }) {
  const first = (event.result || "").split("\n")[0] || "Email thread";
  // "Thread: <subject> — N message(s), oldest first:" → just the subject + count.
  const title =
    first
      .replace(/^Thread:\s*/i, "")
      .replace(/,?\s*oldest first:?\s*$/i, "")
      .trim() || "Email thread";
  return (
    <ToolCardShell
      title={title}
      icon={<AppIcon name="Mail" size={12} />}
      defaultCollapsed
      onDismiss={() => dismissToolCard(event.id)}
    >
      <ThreadBody event={event} accountId={accountId} />
    </ToolCardShell>
  );
}

/** Fetches and renders a thread's messages. Mounts only when the card is
 *  expanded (ToolCardShell renders children lazily), so the fetch is on-demand. */
function ThreadBody({ event, accountId }: { event: ToolEvent; accountId?: string | null }) {
  const args = event.args as Record<string, unknown> | undefined;
  const threadId = (args?.thread_id as string) || "";
  const emailId = (args?.email_id as string) || "";
  const acct = (args?.account_id as string) || accountId || undefined;
  const [msgs, setMsgs] = useState<Email[] | null>(null);
  const [state, setState] = useState<"loading" | "error" | "done">("loading");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        let tid = threadId;
        let single: Email | null = null;
        if (!tid && emailId) {
          single = await getEmail(emailId);
          tid = single.threadId || "";
        }
        const list = tid
          ? await listThread(acct, tid)
          : single
            ? [single]
            : [];
        if (alive) {
          setMsgs(list);
          setState("done");
        }
      } catch {
        if (alive) setState("error");
      }
    })();
    return () => {
      alive = false;
    };
  }, [threadId, emailId, acct]);

  if (state === "loading") {
    return (
      <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground py-2">
        <AppIcon name="Loader2" size={11} className="animate-spin" /> Loading thread…
      </div>
    );
  }
  // Couldn't fetch the conversation — show the tool's text summary so the user
  // still gets the content.
  if (state === "error" || !msgs) {
    return (
      <div className="text-xs whitespace-pre-wrap break-words text-foreground/90 max-h-80 overflow-y-auto overflow-x-hidden scrollbar-thin">
        {event.result}
      </div>
    );
  }
  if (msgs.length === 0) {
    return <div className="text-[10px] text-muted-foreground py-1">No messages in this thread.</div>;
  }
  return (
    <div className="space-y-1">
      {msgs.map((m, i) => (
        <EmailRow
          key={m.id}
          row={{
            id: m.id,
            sender: m.from?.name || m.from?.email || "(unknown sender)",
            subject: m.subject || "(no subject)",
            date: threadDate(m.receivedAt),
          }}
          defaultOpen={i === msgs.length - 1}
        />
      ))}
    </div>
  );
}

/**
 * Render rich, interactive cards for the email-assistant tool calls in a message.
 * Returns null when there are none (inert for non-email agents).
 *
 * Cards render INLINE in transcript order. A run of read_email context calls is
 * folded into one collapsed "Read N messages" group (instead of N full email
 * cards), read_thread renders as one collapsed thread card, and every card is
 * dismissable (persisted via the dismissed-tool registry).
 */
export default function EmailToolCards({
  toolEvents,
  accountId,
  emailId,
}: {
  toolEvents?: ToolEvent[];
  accountId?: string | null;
  emailId?: string | null;
}) {
  const dismissed = useDismissedToolCards();
  const all = (toolEvents ?? []).filter((e) => !dismissed.has(e.id));

  // When the agent read a whole thread, the thread card IS the canonical view —
  // the per-message read_email reads and the search/list lookup that led to it
  // are redundant and just clutter. Show only the thread (+ any draft/action
  // cards) so "show me this thread" yields one card, not three.
  const hasThread = all.some(
    (e) => e.status === "done" && e.name === READ_THREAD_TOOL,
  );
  // A categorized board supersedes the flat list tools it was built from — the
  // agent gathers ids with find_needs_reply / query_inbox / … and then regroups
  // them. Show only the board (one card, categorized), not the raw flat list too.
  const hasGroups = all.some(
    (e) => e.status === "done" && e.name === GROUPS_TOOL,
  );

  const items: React.ReactNode[] = [];
  let readRun: ToolEvent[] = [];
  // The email-list card is rendered ONCE (merging every list-tool result); this
  // guards against pushing a second card for the next list tool in the run.
  let listPushed = false;
  const flushReads = () => {
    if (readRun.length > 0) {
      const run = readRun;
      items.push(<ReadGroupCard key={`reads-${run[0].id}`} events={run} />);
      readRun = [];
    }
  };
  for (const e of all) {
    // Fold consecutive read_email context calls into one group — dropped
    // entirely when a thread card is present (subsumed by the thread).
    if (e.status === "done" && e.name === READ_TOOL) {
      if (!hasThread) readRun.push(e);
      continue;
    }
    if (!hasEmailCard(e)) continue;
    // Drop the flat list/search cards when a thread OR a categorized board is
    // shown — both subsume the raw list the lookup produced.
    if ((hasThread || hasGroups) && LIST_TOOLS.has(e.name)) continue;
    flushReads();
    // Merge ALL list-tool results into ONE interactive card at the position of
    // the first list event; the rest are folded in (not rendered separately).
    if (LIST_TOOLS.has(e.name)) {
      if (listPushed) continue;
      const listEvents = all.filter(
        (x) => x.status === "done" && LIST_TOOLS.has(x.name),
      );
      items.push(
        <DismissableCard
          key={`emaillist-${e.id}`}
          onDismiss={() => listEvents.forEach((le) => dismissToolCard(le.id))}
        >
          <EmailListCard events={listEvents} />
        </DismissableCard>,
      );
      listPushed = true;
      continue;
    }
    if (e.name === READ_THREAD_TOOL) {
      items.push(<ThreadCard key={e.id} event={e} accountId={accountId} />);
      continue;
    }
    // Agent-driven categorized board — grouped, interactive sections.
    if (e.name === GROUPS_TOOL) {
      items.push(
        <DismissableCard key={e.id} onDismiss={() => dismissToolCard(e.id)}>
          <EmailGroupsCard event={e} />
        </DismissableCard>,
      );
      continue;
    }
    // Editable learned-pattern list (delete per row) — own chrome.
    if (PATTERN_TOOLS.has(e.name)) {
      items.push(<PatternListCard key={e.id} event={e} meta={patternMeta(e)} />);
      continue;
    }
    // Structured overview cards — these parse the tool's text into real UI
    // (rule rows with state + actions; label chips) instead of dumping the raw
    // "id=… | …" lines. InfoResultCard is their fallback for unparseable output.
    if (e.name === "get_rules_and_settings") {
      items.push(
        <RulesOverviewCard key={e.id} event={e} accountId={accountId} />,
      );
      continue;
    }
    if (e.name === "list_labels") {
      items.push(<LabelsCard key={e.id} event={e} />);
      continue;
    }
    // Info/list cards bring their own ToolCardShell chrome (collapse + X), so
    // render them directly rather than inside a DismissableCard.
    if (INFO_TOOLS.has(e.name)) {
      items.push(<InfoResultCard key={e.id} event={e} />);
      continue;
    }
    items.push(
      <DismissableCard key={e.id} onDismiss={() => dismissToolCard(e.id)}>
        {renderCard(e, accountId, emailId)}
      </DismissableCard>,
    );
  }
  flushReads();

  if (items.length === 0) return null;
  // min-w-0 + overflow-hidden keep a wide email body (tables, long URLs) from
  // forcing the card — and the chat column — to scroll sideways.
  return <div className="mt-3 space-y-2 min-w-0 overflow-hidden">{items}</div>;
}

// ── Navigation helper ─────────────────────────────────────────────────────────

/** Open an email in the email app: select it, leave any full-screen scene (the
 *  Chat scene now covers the inbox, so we must exit it for the email to show),
 *  and route to /email (a no-op in the email app, navigation from the chat app). */
function useOpenEmail() {
  const router = useRouter();
  return (id: string) => {
    try {
      // Fetch-and-show even when the message isn't in the current folder's list,
      // so opening works from any view (not just when the inbox is active).
      void useEmailStore.getState().openEmailById(id);
    } catch {
      /* store not ready — still navigate */
    }
    // Tell the email page to close any open automation scene (Chat / Reply Zero
    // / …) and reveal the inbox + detail. Harmless elsewhere.
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("cc-email-open", { detail: id }));
    }
    router.push("/email");
  };
}

// ── Draft card ────────────────────────────────────────────────────────────────

/** Editable draft — Save to Drafts or Send. */
function DraftResultCard({
  event: e,
  accountId,
  emailId,
}: {
  event: ToolEvent;
  accountId?: string | null;
  emailId?: string | null;
}) {
  const args = e.args as Record<string, unknown> | undefined;
  // draft_reply returns "Draft[ (saved…)]:\n\n<body>".
  const raw = e.result || "";
  const initial = raw.includes("\n\n")
    ? raw.slice(raw.indexOf("\n\n") + 2).trim()
    : raw.trim();
  const targetId = (args?.email_id as string) || emailId || "";
  const acctId = (args?.account_id as string) || accountId || "";
  const [body, setBody] = useState(initial);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "sending" | "sent" | "error">("idle");

  const canAct = !!acctId && !!targetId && !!body.trim();

  const save = async () => {
    if (!canAct) return;
    setState("saving");
    try {
      await saveDraftText(acctId, targetId, body);
      setState("saved");
    } catch {
      setState("error");
    }
  };

  const send = async () => {
    if (!canAct || !confirm("Send this reply now?")) return;
    setState("sending");
    try {
      const res = await saveDraftText(acctId, targetId, body);
      if (res?.id) {
        await sendDraft(acctId, res.id);
        setState("sent");
      } else {
        // Couldn't resolve a draft id to send — left it in Drafts.
        setState("saved");
      }
    } catch {
      setState("error");
    }
  };

  const done = state === "saved" || state === "sent";

  return (
    <div className="rounded-lg border border-primary/40 bg-primary/5 px-2.5 py-2">
      <div className="flex items-center gap-1.5 mb-1.5">
        <AppIcon name="PenLine" size={12} className="text-primary" />
        <span className="text-[11px] font-medium text-foreground">Draft reply</span>
      </div>
      <textarea
        value={body}
        onChange={(ev) => setBody(ev.target.value)}
        rows={5}
        disabled={done}
        className="w-full bg-background border border-border rounded-md px-2 py-1.5 text-[11px] text-foreground outline-none focus:border-primary resize-y leading-relaxed disabled:opacity-70"
      />
      <div className="flex items-center gap-2 mt-1.5">
        {state === "sent" ? (
          <span className="flex items-center gap-1 text-[10px] text-success">
            <AppIcon name="Send" size={11} /> Sent
          </span>
        ) : state === "saved" ? (
          <span className="flex items-center gap-1 text-[10px] text-success">
            <AppIcon name="CheckCircle2" size={11} /> Saved to Drafts
          </span>
        ) : (
          <>
            <button
              onClick={save}
              disabled={!canAct || state === "saving" || state === "sending"}
              className="flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border border-border text-foreground hover:bg-secondary disabled:opacity-50"
            >
              {state === "saving" ? <AppIcon name="Loader2" size={11} className="animate-spin" /> : <AppIcon name="PenLine" size={11} />}
              Save to Drafts
            </button>
            <Button size="none" radius="keep" layout="flex items-center" onClick={send} disabled={!canAct || state === "saving" || state === "sending"} className="gap-1 text-[10px] px-2 py-1 rounded-md">
              {state === "sending" ? <AppIcon name="Loader2" size={11} className="animate-spin" /> : <AppIcon name="Send" size={11} />}
              Send
            </Button>
          </>
        )}
        {state === "error" && (
          <span className="text-[10px] text-destructive">Something went wrong.</span>
        )}
        {!targetId && (
          <span className="text-[10px] text-muted-foreground">Open the email to enable saving.</span>
        )}
      </div>
    </div>
  );
}

// ── Rule card ─────────────────────────────────────────────────────────────────

const RULE_ID_RE = /id=([0-9a-fA-F-]{8,})/;

/** Condition rows ("When") from a create/update_rule tool call's args. */
function ruleConditions(a: Record<string, unknown>): { label: string; value: string }[] {
  const out: { label: string; value: string }[] = [];
  const push = (label: string, v: unknown) => {
    if (v != null && String(v).trim()) out.push({ label, value: String(v).trim() });
  };
  push("AI", a.instructions);
  push("From", a.from_pattern);
  push("To", a.to_pattern);
  push("Subject", a.subject_pattern);
  push("Body", a.body_pattern);
  return out;
}

/** Action badges ("Then") from a create/update_rule tool call's args. */
function ruleActions(a: Record<string, unknown>): string[] {
  const out: string[] = [];
  const norm = (t: unknown) => String(t).replace(/_/g, " ");
  if (a.action_type) {
    out.push(a.label ? `${norm(a.action_type)}: ${a.label}` : norm(a.action_type));
  } else if (a.label) {
    out.push(`LABEL: ${a.label}`); // create_rule defaults action_type to LABEL
  }
  if (a.second_action_type) {
    out.push(a.second_action_label ? `${norm(a.second_action_type)}: ${a.second_action_label}` : norm(a.second_action_type));
  }
  if (a.add_action_type) {
    out.push(a.add_action_label ? `${norm(a.add_action_type)}: ${a.add_action_label}` : norm(a.add_action_type));
  }
  if (a.forward_to) out.push(`FORWARD → ${a.forward_to}`);
  return out;
}

/** Rule made/updated — shows its When → Then, and can disable or delete it. */
function RuleResultCard({
  event: e,
  accountId,
}: {
  event: ToolEvent;
  accountId?: string | null;
}) {
  const args = e.args as Record<string, unknown> | undefined;
  const acctId = (args?.account_id as string) || accountId || "";
  const ruleId = (args?.rule_id as string) || (e.result?.match(RULE_ID_RE)?.[1] ?? "");
  const name = (args?.name as string) || e.result?.match(/'([^']+)'/)?.[1] || "rule";
  const created = e.name === "create_rule";
  const a = args ?? {};
  const conds = ruleConditions(a);
  const actions = ruleActions(a);
  const op = String(a.conditional_operator ?? "OR").toUpperCase() === "AND" ? "and" : "or";
  const [state, setState] = useState<"idle" | "busy" | "deleted" | "disabled">("idle");

  const del = async () => {
    if (!ruleId || !confirm(`Delete rule "${name}"?`)) return;
    setState("busy");
    try {
      await deleteRule(ruleId);
      setState("deleted");
    } catch {
      setState("idle");
    }
  };
  const disable = async () => {
    if (!ruleId || !acctId) return;
    setState("busy");
    try {
      const rules = await listRules(acctId);
      const r = rules.find((x) => x.id === ruleId);
      if (r) await updateRule(ruleId, { ...r, enabled: false });
      setState("disabled");
    } catch {
      setState("idle");
    }
  };

  return (
    <div className="rounded-lg border border-primary/40 bg-primary/5 px-2.5 py-2">
      <div className="flex items-center gap-2 pr-5">
        <AppIcon name="Sparkles" size={13} className="text-primary flex-shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium text-foreground">
            {created ? "Created rule" : "Updated rule"}: {name}
          </div>
          {state === "deleted" && <div className="text-[10px] text-destructive">Deleted.</div>}
          {state === "disabled" && <div className="text-[10px] text-muted-foreground">Disabled.</div>}
        </div>
        {state === "idle" && ruleId && (
          <div className="flex items-center gap-1 flex-shrink-0">
            {acctId && (
              <button
                onClick={disable}
                className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
              >
                Disable
              </button>
            )}
            <button
              onClick={del}
              className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-destructive hover:bg-destructive/10"
            >
              Delete
            </button>
          </div>
        )}
        {state === "busy" && <AppIcon name="Loader2" size={12} className="animate-spin text-muted-foreground" />}
      </div>

      {/* When → Then summary (from the tool-call args). For update_rule these
          are just the changed fields; for create_rule, the whole rule. */}
      {state !== "deleted" && (conds.length > 0 || actions.length > 0) && (
        <div className="mt-1.5 pl-[21px] space-y-1">
          {conds.length > 0 && (
            <div className="text-[10px] leading-relaxed">
              <span className="text-muted-foreground">When </span>
              {conds.map((c, i) => (
                <span key={i}>
                  {i > 0 && <span className="text-muted-foreground/70"> {op} </span>}
                  <span className="text-muted-foreground">{c.label}:</span>{" "}
                  <span className="text-foreground">
                    {c.value.length > 60 ? c.value.slice(0, 60) + "…" : c.value}
                  </span>
                </span>
              ))}
            </div>
          )}
          {actions.length > 0 && (
            <div className="flex flex-wrap items-center gap-1 text-[10px]">
              <span className="text-muted-foreground">Then</span>
              {actions.map((act, i) => (
                <span
                  key={i}
                  className="px-1.5 py-0.5 rounded bg-secondary/70 text-foreground"
                >
                  {act}
                </span>
              ))}
            </div>
          )}
          {a.automated === false && (
            <div className="text-[10px] text-warning">
              Proposes for approval (not auto-applied)
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Settings card ─────────────────────────────────────────────────────────────

/** Map of setting key → friendly label for the "settings updated" summary. */
const SETTING_LABELS: Record<string, string> = {
  about: "About you",
  signature: "Signature",
  auto_run: "Auto-run rules",
  cold_email_blocker: "Cold-email blocker",
  personal_instructions: "Personal instructions",
  writing_style: "Writing style",
  draft_replies: "Auto-draft replies",
  draft_confidence: "Draft confidence",
  follow_up_awaiting_days: "Follow-up (awaiting)",
  follow_up_needs_reply_days: "Follow-up (needs reply)",
  follow_up_auto_draft: "Follow-up auto-draft",
  digest_frequency: "Digest frequency",
  digest_categories: "Digest categories",
  digest_day_of_week: "Digest day",
  digest_time_of_day: "Digest time",
  digest_send_to_email: "Email the digest",
  multi_rule_execution: "Multi-rule execution",
  sensitive_data_protection: "Sensitive-data protection",
  rule_model: "Rule model",
  draft_model: "Draft model",
  chat_model: "Chat model",
};

function fmtVal(v: unknown): string {
  if (typeof v === "boolean") return v ? "on" : "off";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "all";
  const s = String(v);
  return s.length > 40 ? s.slice(0, 40) + "…" : s;
}

/** Shows which settings the assistant changed, with a shortcut to the settings UI. */
function SettingsUpdatedCard({ event: e }: { event: ToolEvent }) {
  const router = useRouter();
  const args = e.args as Record<string, unknown> | undefined;
  const changed = Object.entries(args ?? {}).filter(
    ([k, v]) => k !== "account_id" && v !== null && v !== undefined && k in SETTING_LABELS,
  );

  return (
    <div className="rounded-lg border border-primary/40 bg-primary/5 px-2.5 py-2">
      <div className="flex items-center gap-2 pr-5">
        <AppIcon name="Settings2" size={13} className="text-primary flex-shrink-0" />
        <span className="text-[11px] font-medium text-foreground flex-1">Settings updated</span>
        <button
          onClick={() => router.push("/email")}
          className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
        >
          <AppIcon name="ExternalLink" size={10} /> Open
        </button>
      </div>
      {changed.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {changed.map(([k, v]) => (
            <span
              key={k}
              className="text-[10px] px-1.5 py-0.5 rounded bg-secondary/70 text-muted-foreground"
              title={`${SETTING_LABELS[k]}: ${fmtVal(v)}`}
            >
              {SETTING_LABELS[k]}: <span className="text-foreground">{fmtVal(v)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Email list card ───────────────────────────────────────────────────────────

interface ParsedRow {
  id: string;
  sender: string;
  subject: string;
  date?: string;
  /** Which list tool(s) surfaced this row — shown as chips when several tools
   *  are merged into one card (e.g. a mail that's both "Important" and
   *  "Needs reply"). */
  prov?: string[];
}

/** Compact "Jun 28, 3:42 PM" for ordering messages within a thread. */
function threadDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** Parse the email-list rows the read tools emit. Handles every format the
 *  email-assistant produces:
 *    • id=<id> | <sender>: <subject> — <snippet>            (search/find/priority)
 *    • id=<id> | <date> | <sender>: <subject>[flags] — <…>  (query_inbox)
 *    • id=<id> [account] | <sender>: <subject>              (multi-account)
 *  Strategy: pull the id, drop an optional [account] tag + leading metadata
 *  pipe-segments, then split the LAST segment into "<sender>: <subject>". */
function parseEmailRows(result: string): ParsedRow[] {
  const rows: ParsedRow[] = [];
  for (const line of result.split("\n")) {
    const idM = line.match(/\bid=([^\s|\]]+)/);
    if (!idM || idM.index === undefined) continue;
    const id = idM[1].trim();
    // Text after the id, minus a leading " [account]" tag.
    const rest = line
      .slice(idM.index + idM[0].length)
      .replace(/^\s*\[[^\]]*\]/, "");
    // Pipe-separated; the LAST non-empty segment is "<sender>: <subject>…".
    // Earlier segments (date) are metadata we don't show in the row label.
    const segs = rest.split("|").map((s) => s.trim()).filter(Boolean);
    const last = segs[segs.length - 1] ?? "";
    const colon = last.indexOf(":");
    if (colon === -1) continue;
    const sender = last.slice(0, colon).trim();
    const subject = last
      .slice(colon + 1)
      .split(" — ")[0] // drop trailing snippet/reason
      .replace(/\[[^\]]*\]\s*$/, "") // drop trailing [unread, star] flags
      .trim();
    rows.push({ id, sender, subject: subject || "(no subject)" });
  }
  return rows;
}

const LIST_META: Record<string, { icon: string; label: string }> = {
  query_inbox: { icon: "Mail", label: "Inbox results" },
  find_priority: { icon: "Clock", label: "Priority emails" },
  get_important_emails: { icon: "Clock", label: "Important to check" },
  search_emails: { icon: "Search", label: "Search results" },
  find_urgent: { icon: "Clock", label: "Urgent / needs attention" },
  find_needs_reply: { icon: "Reply", label: "Needs a reply" },
};

/** Short per-tool chip shown on a row when several list tools are merged into
 *  one card, so you can tell WHY each mail is here (important vs needs-reply…). */
const LIST_PROV: Record<string, string> = {
  get_important_emails: "Important",
  find_needs_reply: "Needs reply",
  find_urgent: "Urgent",
  query_inbox: "Inbox",
  search_emails: "Search",
};

/** find_priority carries its "why" in the `kind` arg (one tool, many kinds), so
 *  its chip is derived from the call args rather than the tool name. */
const PRIORITY_CHIP: Record<string, string> = {
  needs_reply: "Needs reply",
  important: "Important",
  urgent: "Urgent",
};

/** The provenance chip for a list event — from the `kind` arg for find_priority,
 *  else from the tool name. */
function listChip(e: ToolEvent): string | undefined {
  if (e.name === "find_priority") {
    const kind = String((e.args as Record<string, unknown> | undefined)?.kind ?? "needs_reply");
    return PRIORITY_CHIP[kind] ?? "Priority";
  }
  return LIST_PROV[e.name];
}

/** Quick-categorize options offered per row (the rule-engine categories). */
const QUICK_CATEGORIES = [
  "Newsletter", "Marketing", "Receipt", "Calendar", "Notification",
  "Cold Email", "FYI",
];

/** Merge several list-tool results into ONE deduped row set (by id), collecting
 *  each row's provenance (which tools surfaced it). A mail returned by both
 *  get_important_emails and find_needs_reply becomes one row with both chips —
 *  instead of two detached cards showing the same message twice. */
function mergeListRows(events: ToolEvent[]): ParsedRow[] {
  const byId = new Map<string, ParsedRow>();
  for (const e of events) {
    const chip = listChip(e);
    for (const r of parseEmailRows(e.result || "")) {
      const existing = byId.get(r.id);
      if (existing) {
        if (chip && !existing.prov?.includes(chip)) {
          existing.prov = [...(existing.prov ?? []), chip];
        }
      } else {
        byId.set(r.id, { ...r, prov: chip ? [chip] : [] });
      }
    }
  }
  return [...byId.values()];
}

/** A row's optimistic triage state (lifted to the card so bulk + per-row share). */
type RowStatus = "idle" | "busy" | "archived" | "read";

/** Email results — each row expands its body inline (lazy), can be
 *  Archived / Marked-read in place (optimistic, with bulk "all" shortcuts), and
 *  has an "Open in inbox" jump. Mirrors inbox-zero's inline list. */
function EmailListCard({ events }: { events: ToolEvent[] }) {
  const [expanded, setExpanded] = useState(false);
  const [statuses, setStatuses] = useState<Record<string, RowStatus>>({});
  // Merge every list-tool result into one deduped set so "important + needs
  // reply" is a single integrated list, not two detached cards over the same mail.
  const rows = mergeListRows(events);
  // No parseable rows (e.g. "Nothing needs a reply") → generic summary card.
  if (rows.length === 0) return <ActionResultCard event={events[0]} />;
  // One tool keeps its specific label; several merged tools get a combined
  // heading and each row shows provenance chips (why it's listed).
  const names = [...new Set(events.map((e) => e.name))];
  const multi = names.length > 1;
  // Show per-row "why it's here" chips when several tools merged OR when one
  // tool (find_priority) produced rows of mixed provenance (different `kind`s).
  const distinctProv = new Set(rows.flatMap((r) => r.prov ?? []));
  const showProv = multi || distinctProv.size > 1;
  const meta = !multi
    ? LIST_META[names[0]] ?? { icon: "Mail", label: "Emails" }
    : names.some((n) =>
          n === "find_priority" || n === "get_important_emails" ||
          n === "find_needs_reply" || n === "find_urgent")
      ? { icon: "Clock", label: "High-priority emails" }
      : { icon: "Mail", label: "Emails" };
  const iconName = meta.icon;
  const shown = expanded ? rows : rows.slice(0, 5);

  const set = (id: string, s: RowStatus) =>
    setStatuses((prev) => ({ ...prev, [id]: s }));

  // Archive / mark-read fire to the provider directly (the inbox view, when
  // loaded, picks the change up on its next background poll).
  const archive = async (id: string) => {
    set(id, "busy");
    try {
      await patchEmail(id, { folder: "archive" });
      set(id, "archived");
    } catch {
      set(id, "idle");
    }
  };
  const markRead = async (id: string) => {
    set(id, "busy");
    try {
      await patchEmail(id, { isRead: true });
      set(id, "read");
    } catch {
      set(id, "idle");
    }
  };

  // Bulk acts on the visible, not-yet-handled rows.
  const actionable = shown.filter(
    (r) => !["archived", "read", "busy"].includes(statuses[r.id] ?? "idle"),
  );

  return (
    <div className="rounded-lg border border-sidebar-border bg-secondary/40 px-2.5 py-2">
      {/* pr-5 leaves room for the dismiss (X) overlay at the card's corner. */}
      <div className="flex items-center gap-1.5 mb-1.5 pr-5">
        <AppIcon name={iconName} size={12} className="text-primary" />
        <span className="text-[11px] font-medium text-foreground">{meta.label}</span>
        <span className="text-[10px] text-muted-foreground">({rows.length})</span>
        {actionable.length > 1 && (
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => actionable.forEach((r) => markRead(r.id))}
              title="Mark all as read"
              className="flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
            >
              <AppIcon name="MailOpen" size={10} /> Read all
            </button>
            <button
              onClick={() => actionable.forEach((r) => archive(r.id))}
              title="Archive all"
              className="flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
            >
              <AppIcon name="Archive" size={10} /> Archive all
            </button>
          </div>
        )}
      </div>
      <div className="space-y-1">
        {shown.map((r) => (
          <EmailRow
            key={r.id}
            row={r}
            showProv={showProv}
            status={statuses[r.id] ?? "idle"}
            onArchive={() => archive(r.id)}
            onMarkRead={() => markRead(r.id)}
          />
        ))}
      </div>
      {rows.length > 5 && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-[10px] text-primary hover:opacity-80"
        >
          {expanded ? "Show less" : `Show ${rows.length - 5} more`}
        </button>
      )}
    </div>
  );
}

// ── Categorized email board (present_email_groups) ────────────────────────────

interface EmailGroup {
  title: string;
  note?: string;
  rows: ParsedRow[];
}

/** Parse present_email_groups output into titled groups of rows. The tool emits:
 *    Categorized emails — <total> across <n> group(s):
 *    ## <Title> (<count>)[ — <note>]
 *    • id=<id> | <sender>: <subject>
 *    …
 *  Each "##" line opens a new group; the "• id=…" lines under it are its rows
 *  (reusing the shared row parser). Empty groups are dropped. */
function parseGroupedRows(result: string): EmailGroup[] {
  const groups: EmailGroup[] = [];
  let current: EmailGroup | null = null;
  for (const line of result.split("\n")) {
    const head = line.match(/^\s*##\s+(.*)$/);
    if (head) {
      let label = head[1].trim();
      let note: string | undefined;
      const dash = label.indexOf(" — ");
      if (dash !== -1) {
        note = label.slice(dash + 3).trim() || undefined;
        label = label.slice(0, dash).trim();
      }
      // Drop a trailing "(count)" from the heading — the card recomputes it.
      label = label.replace(/\s*\(\d+\)\s*$/, "").trim();
      current = { title: label || "Untitled", note, rows: [] };
      groups.push(current);
      continue;
    }
    if (current) {
      const parsed = parseEmailRows(line);
      if (parsed.length) current.rows.push(...parsed);
    }
  }
  return groups.filter((g) => g.rows.length > 0);
}

/** Interactive, categorized board of emails — the agent groups mail under its
 *  own headings (HR / Finance / R&D, by project, by urgency…) and each group is
 *  a titled, collapsible section of the same interactive rows as the flat list
 *  (open / archive / mark-read / categorize). Triage state is lifted here so a
 *  group's bulk actions and its rows stay in sync. Falls back to the generic
 *  confirmation card when there's nothing parseable to group. */
function EmailGroupsCard({ event: e }: { event: ToolEvent }) {
  const groups = parseGroupedRows(e.result || "");
  const [statuses, setStatuses] = useState<Record<string, RowStatus>>({});
  if (groups.length === 0) return <ActionResultCard event={e} />;

  const total = groups.reduce((n, g) => n + g.rows.length, 0);
  const set = (id: string, s: RowStatus) =>
    setStatuses((prev) => ({ ...prev, [id]: s }));
  const archive = async (id: string) => {
    set(id, "busy");
    try {
      await patchEmail(id, { folder: "archive" });
      set(id, "archived");
    } catch {
      set(id, "idle");
    }
  };
  const markRead = async (id: string) => {
    set(id, "busy");
    try {
      await patchEmail(id, { isRead: true });
      set(id, "read");
    } catch {
      set(id, "idle");
    }
  };

  return (
    <div className="rounded-lg border border-sidebar-border bg-secondary/40 px-2.5 py-2 min-w-0 overflow-hidden">
      <div className="flex items-center gap-1.5 mb-2 pr-5">
        <AppIcon name="Layers" size={12} className="text-primary" />
        <span className="text-[11px] font-medium text-foreground">
          Categorized emails
        </span>
        <span className="text-[10px] text-muted-foreground">
          ({total} in {groups.length} group{groups.length > 1 ? "s" : ""})
        </span>
      </div>
      <div className="space-y-1.5">
        {groups.map((g, i) => (
          <GroupSection
            key={`${g.title}-${i}`}
            group={g}
            statuses={statuses}
            onArchive={archive}
            onMarkRead={markRead}
          />
        ))}
      </div>
    </div>
  );
}

/** One category within the board: a titled header (count + optional note) with
 *  Read-all / Archive-all shortcuts, collapsible, over its interactive rows. */
function GroupSection({
  group,
  statuses,
  onArchive,
  onMarkRead,
}: {
  group: EmailGroup;
  statuses: Record<string, RowStatus>;
  onArchive: (id: string) => void;
  onMarkRead: (id: string) => void;
}) {
  const [open, setOpen] = useState(true);
  // Bulk acts on this group's not-yet-handled rows.
  const actionable = group.rows.filter(
    (r) => !["archived", "read", "busy"].includes(statuses[r.id] ?? "idle"),
  );
  return (
    <div className="rounded-md border border-border/70 bg-background/40">
      <div className="flex items-center gap-1.5 px-2 py-1.5">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
        >
          <AppIcon name="ChevronDown"
            size={11}
            className={`text-muted-foreground flex-shrink-0 transition-transform ${open ? "" : "-rotate-90"}`}
          />
          <span className="text-[11px] font-semibold text-foreground truncate">
            {group.title}
          </span>
          <span className="text-[10px] text-muted-foreground flex-shrink-0">
            ({group.rows.length})
          </span>
          {group.note && (
            <span className="text-[10px] text-muted-foreground/80 truncate hidden sm:inline">
              — {group.note}
            </span>
          )}
        </button>
        {open && actionable.length > 1 && (
          <div className="flex items-center gap-1 flex-shrink-0">
            <button
              onClick={() => actionable.forEach((r) => onMarkRead(r.id))}
              title="Mark all in this group as read"
              className="flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
            >
              <AppIcon name="MailOpen" size={10} /> Read all
            </button>
            <button
              onClick={() => actionable.forEach((r) => onArchive(r.id))}
              title="Archive all in this group"
              className="flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
            >
              <AppIcon name="Archive" size={10} /> Archive all
            </button>
          </div>
        )}
      </div>
      {open && (
        <div className="px-1.5 pb-1.5 space-y-1">
          {group.rows.map((r) => (
            <EmailRow
              key={r.id}
              row={r}
              status={statuses[r.id] ?? "idle"}
              onArchive={() => onArchive(r.id)}
              onMarkRead={() => onMarkRead(r.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** One email row: click to expand the body inline (fetched lazily); Archive /
 *  Mark-read in place; "Open in inbox" jumps to the app. Triage is controlled
 *  by a parent (the list, for bulk) when status/handlers are passed, else
 *  self-managed (single-email cards). `defaultOpen` shows the body immediately. */
function EmailRow({
  row,
  status: extStatus,
  onArchive: extArchive,
  onMarkRead: extMarkRead,
  defaultOpen = false,
  showProv = false,
}: {
  row: ParsedRow;
  status?: RowStatus;
  onArchive?: () => void;
  onMarkRead?: () => void;
  defaultOpen?: boolean;
  /** Show the per-row provenance chips (only meaningful in a merged card). */
  showProv?: boolean;
}) {
  const openEmail = useOpenEmail();
  const [open, setOpen] = useState(defaultOpen);
  const [body, setBody] = useState<FullBodyResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [localStatus, setLocalStatus] = useState<RowStatus>("idle");
  // Per-row quick-categorize (applies a label to the message via the same
  // PATCH the inbox uses; the inbox picks the change up on its next poll).
  const [catOpen, setCatOpen] = useState(false);
  const [catBusy, setCatBusy] = useState(false);
  const [labeled, setLabeled] = useState<string | null>(null);
  const categorize = async (cat: string) => {
    setCatOpen(false);
    setCatBusy(true);
    try {
      await updateEmailLabels(row.id, [cat], []);
      setLabeled(cat);
    } catch {
      /* leave unlabeled — the user can retry */
    } finally {
      setCatBusy(false);
    }
  };

  const status = extStatus ?? localStatus;
  const archive =
    extArchive ??
    (async () => {
      setLocalStatus("busy");
      try {
        await patchEmail(row.id, { folder: "archive" });
        setLocalStatus("archived");
      } catch {
        setLocalStatus("idle");
      }
    });
  const markRead =
    extMarkRead ??
    (async () => {
      setLocalStatus("busy");
      try {
        await patchEmail(row.id, { isRead: true });
        setLocalStatus("read");
      } catch {
        setLocalStatus("idle");
      }
    });

  const archived = status === "archived";
  const done = archived || status === "read";

  // Lazy-load the body whenever the row is open and not yet loaded — covers both
  // click-to-expand and a defaultOpen single-email card.
  useEffect(() => {
    if (open && !body && !loading && !error) {
      // Lazy body fetch on expand — a deliberate fetch-on-state-change effect.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoading(true);
      fetchFullBody(row.id)
        .then(setBody)
        .catch(() => setError(true))
        .finally(() => setLoading(false));
    }
  }, [open, body, loading, error, row.id]);

  return (
    <div
      className={`rounded-md border transition-colors ${
        open ? "border-border bg-background" : "border-transparent hover:border-border"
      } ${archived ? "opacity-60" : ""}`}
    >
      <div className="flex items-center gap-1">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex items-center gap-1.5 flex-1 min-w-0 text-left px-1.5 py-1 rounded-md hover:bg-secondary/60"
        >
          <AppIcon name="ChevronDown"
            size={11}
            className={`text-muted-foreground flex-shrink-0 transition-transform ${open ? "" : "-rotate-90"}`}
          />
          <span className="min-w-0 flex-1">
            <span
              className={`block text-[11px] truncate ${
                archived ? "line-through text-muted-foreground" : "text-foreground"
              }`}
            >
              {row.subject}
            </span>
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground min-w-0">
              <span className="truncate">
                {row.date && (
                  <span className="text-foreground/70">{row.date} · </span>
                )}
                {row.sender}
              </span>
              {showProv &&
                row.prov?.map((p) => (
                  <span
                    key={p}
                    className="flex-shrink-0 px-1 py-px rounded-full bg-primary/10 text-primary text-[9px] leading-none"
                  >
                    {p}
                  </span>
                ))}
              {labeled && (
                <span className="flex-shrink-0 px-1 py-px rounded-full bg-success/15 text-success text-[9px] leading-none">
                  {labeled}
                </span>
              )}
            </span>
          </span>
        </button>
        <div className="flex items-center flex-shrink-0 mr-0.5">
          {done ? (
            <span className="flex items-center gap-0.5 text-[9px] text-success px-1">
              <AppIcon name="CheckCircle2" size={10} /> {archived ? "Archived" : "Read"}
            </span>
          ) : status === "busy" ? (
            <AppIcon name="Loader2" size={12} className="animate-spin text-muted-foreground mx-1" />
          ) : (
            <>
              <button
                onClick={() => setCatOpen((v) => !v)}
                title="Categorize"
                aria-label="Categorize"
                aria-expanded={catOpen}
                className={`p-1 rounded hover:text-foreground hover:bg-secondary ${
                  catOpen ? "text-primary" : "text-muted-foreground"
                }`}
              >
                {catBusy ? (
                  <AppIcon name="Loader2" size={11} className="animate-spin" />
                ) : (
                  <AppIcon name="Tag" size={11} />
                )}
              </button>
              <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={markRead} title="Mark as read" aria-label="Mark as read" className="rounded">
                <AppIcon name="MailOpen" size={11} />
              </Button>
              <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={archive} title="Archive" aria-label="Archive" className="rounded">
                <AppIcon name="Archive" size={11} />
              </Button>
              <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={() => openEmail(row.id)} title="Open in inbox" aria-label="Open in inbox" className="rounded">
                <AppIcon name="ExternalLink" size={11} />
              </Button>
            </>
          )}
        </div>
      </div>
      {catOpen && (
        <div className="px-2 pb-2 pt-1 flex flex-wrap items-center gap-1 border-t border-border/50">
          <span className="text-[10px] text-muted-foreground mr-0.5">Label as:</span>
          {QUICK_CATEGORIES.map((c) => (
            <button
              key={c}
              onClick={() => categorize(c)}
              className="px-1.5 py-0.5 rounded-full border border-border text-[10px] text-foreground hover:bg-secondary"
            >
              {c}
            </button>
          ))}
        </div>
      )}
      {open && (
        <div className="px-2 pb-2 pt-1.5 border-t border-border/50">
          {loading ? (
            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground py-2">
              <AppIcon name="Loader2" size={11} className="animate-spin" /> Loading email…
            </div>
          ) : error ? (
            <div className="text-[10px] text-muted-foreground py-1">
              Couldn&apos;t load this email.{" "}
              <button onClick={() => openEmail(row.id)} className="text-primary underline">
                Open in inbox
              </button>
            </div>
          ) : body ? (
            <>
              {body.from && (
                <div className="text-[10px] text-muted-foreground mb-1 truncate">
                  From: {body.from}
                </div>
              )}
              <div className="max-h-72 overflow-y-auto overflow-x-hidden rounded">
                <MessageContent html={body.body_html} text={body.body_text} />
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

// ── Email preview card ────────────────────────────────────────────────────────

/** Opened email (read_email) — reuses the email row so the body expands inline
 *  (full HTML), with Archive / Mark-read / Open. Shown expanded by default. */
function EmailPreviewCard({ event: e }: { event: ToolEvent }) {
  const args = e.args as Record<string, unknown> | undefined;
  const id = (args?.email_id as string) || "";
  const result = e.result || "";
  const from = result.match(/^From:\s*(.+)$/m)?.[1]?.trim() ?? "";
  const subject = result.match(/^Subject:\s*(.+)$/m)?.[1]?.trim() ?? "(no subject)";

  // Without an id we can't fetch/open — fall back to a static header.
  if (!id) {
    return (
      <div className="rounded-lg border border-sidebar-border bg-secondary/40 px-2.5 py-2">
        <div className="flex items-center gap-2">
          <AppIcon name="Mail" size={13} className="text-primary flex-shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-medium text-foreground truncate">{subject}</div>
            {from && <div className="text-[10px] text-muted-foreground truncate">{from}</div>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-sidebar-border bg-secondary/40 px-2.5 py-2 min-w-0 overflow-hidden">
      <EmailRow
        row={{ id, sender: from || "(unknown sender)", subject }}
        defaultOpen
      />
    </div>
  );
}

// ── Label-update card ─────────────────────────────────────────────────────────

/** apply_labels result — shows the labels added (as their coloured chips) and
 *  removed (struck through), so the chat reflects what actually changed. */
function LabelUpdateCard({ event: e }: { event: ToolEvent }) {
  const args = e.args as Record<string, unknown> | undefined;
  const toList = (v: unknown) =>
    Array.isArray(v) ? v.map(String).map((s) => s.trim()).filter(Boolean) : [];
  // apply_labels uses add/remove; manage_inbox(action="label") uses
  // add_labels/remove_labels — accept either shape.
  const add = toList(args?.add ?? args?.add_labels);
  const remove = toList(args?.remove ?? args?.remove_labels);
  const ids = args?.message_ids;
  const count = Array.isArray(ids) ? ids.length : 0;
  // Nothing structured to show → fall back to the generic confirmation.
  if (add.length === 0 && remove.length === 0) return <ActionResultCard event={e} />;
  const failed = e.status === "error";

  return (
    <div
      className={`rounded-lg border px-2.5 py-2 ${
        failed ? "border-destructive/40 bg-destructive/5" : "border-sidebar-border bg-secondary/40"
      }`}
    >
      <div className="flex items-center gap-1.5">
        <AppIcon name="Tag" size={12} className={failed ? "text-destructive" : "text-primary"} />
        <span className="text-[11px] font-medium text-foreground">
          {failed ? "Couldn't update labels" : "Labels updated"}
          {count > 0 && (
            <span className="text-muted-foreground font-normal">
              {" "}· {count} email{count > 1 ? "s" : ""}
            </span>
          )}
        </span>
      </div>
      {add.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <span className="text-[10px] text-muted-foreground">Added</span>
          {add.map((l) => (
            <LabelChip key={l} name={l} icon className="text-[10px] px-2 py-0.5" />
          ))}
        </div>
      )}
      {remove.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <span className="text-[10px] text-muted-foreground">Removed</span>
          {remove.map((l) => (
            <span
              key={l}
              className="text-[10px] px-2 py-0.5 rounded-full bg-secondary text-muted-foreground line-through"
            >
              {l}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Inbox-action card ─────────────────────────────────────────────────────────

/** Per-action label/icon for manage_inbox (archive/trash/read/star/…). */
const INBOX_ACTION_META: Record<
  string,
  { icon: string; verb: string; danger?: boolean }
> = {
  archive: { icon: "Archive", verb: "Archived" },
  trash: { icon: "Trash2", verb: "Trashed", danger: true },
  read: { icon: "MailOpen", verb: "Marked read" },
  unread: { icon: "Mail", verb: "Marked unread" },
  star: { icon: "Star", verb: "Starred" },
  unstar: { icon: "Star", verb: "Unstarred" },
  move: { icon: "FolderInput", verb: "Moved" },
};

/** manage_inbox result — names the action + email count ("Archived 3 emails")
 *  instead of a generic "Inbox updated". */
function ManageInboxCard({ event: e }: { event: ToolEvent }) {
  const args = e.args as Record<string, unknown> | undefined;
  const action = String(args?.action ?? "").toLowerCase();
  const ids = args?.message_ids;
  const count = Array.isArray(ids) ? ids.length : 0;
  const meta = INBOX_ACTION_META[action];
  // Unknown action or no count → fall back to the generic confirmation.
  if (!meta || count === 0) return <ActionResultCard event={e} />;
  const failed = e.status === "error";
  const iconName = failed ? "X" : meta.icon;

  return (
    <div
      className={`rounded-lg border px-2.5 py-2 ${
        failed
          ? "border-destructive/40 bg-destructive/5"
          : meta.danger
            ? "border-warning/40 bg-warning/5"
            : "border-sidebar-border bg-secondary/40"
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className={
            failed ? "text-destructive" : meta.danger ? "text-warning" : "text-success"
          }
        >
          <AppIcon name={iconName} size={13} />
        </span>
        <span className="text-[11px] font-medium text-foreground">
          {failed ? `Couldn't ${meta.verb.toLowerCase()}` : meta.verb}{" "}
          {count} email{count > 1 ? "s" : ""}
          {action === "move" && args?.folder
            ? ` → ${String(args.folder)}`
            : ""}
        </span>
      </div>
    </div>
  );
}

// ── Learned-pattern list card (editable) ──────────────────────────────────────

interface PatternRow { id: string; chip?: string; text: string }

/** Parse learned-pattern tool output: lines like "• id=<id> [<chip>] <text>".
 *  The id + leading [chip] are pulled out so each pattern renders as a row with
 *  a delete button. */
function parsePatternRows(result: string): PatternRow[] {
  const rows: PatternRow[] = [];
  for (const line of result.split("\n")) {
    const m = line.match(/^\s*•\s*id=(\S+)\s*(.*)$/);
    if (!m) continue;
    let rest = m[2].trim();
    let chip: string | undefined;
    const chipM = rest.match(/^\[([^\]]*)\]\s*/);
    if (chipM) {
      chip = chipM[1];
      rest = rest.slice(chipM[0].length);
    }
    rows.push({ id: m[1], chip, text: rest || "(pattern)" });
  }
  return rows;
}

/** Editable list of learned patterns — each row can be forgotten (deleted),
 *  optimistically. Used for BOTH learned writing preferences and learned rule
 *  pins (the delete endpoint differs, passed via `meta.remove`). Shows the WHOLE
 *  pattern text (no truncation) so the full list is visible and manageable in
 *  chat. There is no edit endpoint, so "edit" is forget-and-re-teach. */
function PatternListCard({
  event: e,
  meta,
}: {
  event: ToolEvent;
  meta: { icon: string; label: string; remove: (id: string) => Promise<void> };
}) {
  const iconName = meta.icon;
  const [rows, setRows] = useState<PatternRow[]>(() => parsePatternRows(e.result || ""));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState(false);

  const forget = async (id: string) => {
    setBusy(id);
    setError(false);
    try {
      await meta.remove(id);
      setRows((rs) => rs.filter((r) => r.id !== id));
    } catch {
      setError(true);
    } finally {
      setBusy(null);
    }
  };

  // No parseable rows → show the tool's text (e.g. "No learned patterns yet.").
  if (rows.length === 0) {
    return (
      <ToolCardShell
        title={meta.label}
        icon={<AppIcon name={iconName} size={12} />}
        onDismiss={() => dismissToolCard(e.id)}
      >
        <div className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words">
          {(e.result || "").trim() || "Nothing learned yet."}
        </div>
      </ToolCardShell>
    );
  }

  return (
    <ToolCardShell
      title={`${meta.label} (${rows.length})`}
      icon={<AppIcon name={iconName} size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div className="space-y-1">
        {rows.map((r) => (
          <div
            key={r.id}
            className="flex items-start gap-1.5 rounded-md px-1.5 py-1 hover:bg-secondary/50"
          >
            {r.chip && (
              <span
                className="text-[9px] px-1 py-0.5 rounded bg-secondary text-muted-foreground flex-shrink-0 mt-0.5 max-w-[120px] truncate"
                title={r.chip}
              >
                {r.chip}
              </span>
            )}
            <span className="text-[11px] text-foreground/90 flex-1 min-w-0 break-words leading-relaxed">
              {r.text}
            </span>
            <button
              onClick={() => forget(r.id)}
              disabled={busy === r.id}
              title="Forget this pattern"
              aria-label="Forget this pattern"
              className="p-0.5 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 flex-shrink-0 disabled:opacity-50"
            >
              {busy === r.id ? (
                <AppIcon name="Loader2" size={11} className="animate-spin" />
              ) : (
                <AppIcon name="Trash2" size={11} />
              )}
            </button>
          </div>
        ))}
      </div>
      {error && (
        <div className="mt-1 text-[10px] text-destructive">
          Couldn&apos;t forget that one — try again.
        </div>
      )}
    </ToolCardShell>
  );
}

// ── Rules overview card (get_rules_and_settings) ─────────────────────────────

interface ParsedRuleRow {
  id: string;
  name: string;
  auto: boolean;
  enabled: boolean;
  condition: string;
  actions: string[];
}

/** Parse get_rules_and_settings output into structured rule rows:
 *    N rule(s):
 *    • id=<id> | <name> [auto|manual/on|off] — if: <condition> → ACT[:label], …
 *    Settings: auto-run=…, cold-blocker=…, about set=…
 *  "(static)" (no AI condition) parses as an empty condition. */
function parseRulesOverview(
  result: string,
): { rules: ParsedRuleRow[]; settings: string } {
  const rules: ParsedRuleRow[] = [];
  let settings = "";
  for (const raw of (result || "").split("\n")) {
    const line = raw.trim();
    if (line.startsWith("Settings:")) {
      settings = line.replace(/^Settings:\s*/, "");
      continue;
    }
    const m = line.match(
      /^•\s*id=(\S+)\s*\|\s*(.+?)\s*\[(auto|manual)\/(on|off)\]\s*—\s*if:\s*(.*)$/,
    );
    if (!m) continue;
    let condition = m[5].trim();
    let actions: string[] = [];
    const arrow = condition.lastIndexOf("→");
    if (arrow !== -1) {
      actions = condition
        .slice(arrow + 1)
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean);
      condition = condition.slice(0, arrow).trim();
    }
    rules.push({
      id: m[1],
      name: m[2],
      auto: m[3] === "auto",
      enabled: m[4] === "on",
      condition: condition === "(static)" ? "" : condition,
      actions,
    });
  }
  return { rules, settings };
}

/** One "Then" chip: "MOVE_FOLDER:Newsletter" → "move folder: Newsletter". */
function ActionChip({ action }: { action: string }) {
  const colon = action.indexOf(":");
  const type = (colon === -1 ? action : action.slice(0, colon))
    .replace(/_/g, " ")
    .toLowerCase();
  const val = colon === -1 ? "" : action.slice(colon + 1).trim();
  return (
    <span className="px-1.5 py-0.5 rounded bg-secondary/70 text-foreground">
      {type}
      {val ? `: ${val}` : ""}
    </span>
  );
}

/** The account's rules as structured rows — name, approval/On-Off state, its
 *  "If → Then", with per-rule toggle + delete — instead of the raw "id=… | …"
 *  dump the tool emits (which is for the MODEL to reference, not for people). */
function RulesOverviewCard({
  event: e,
  accountId,
}: {
  event: ToolEvent;
  accountId?: string | null;
}) {
  const args = e.args as Record<string, unknown> | undefined;
  const acctId = (args?.account_id as string) || accountId || "";
  const [parsed] = useState(() => parseRulesOverview(e.result || ""));
  const [rules, setRules] = useState(parsed.rules);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState(false);

  // Unparseable output (error text, older format) → the plain-text card.
  if (parsed.rules.length === 0) return <InfoResultCard event={e} />;

  const toggle = async (row: ParsedRuleRow) => {
    if (!acctId || busy) return;
    setBusy(row.id);
    setError(false);
    try {
      const all = await listRules(acctId);
      const full = all.find((r) => r.id === row.id);
      if (!full) throw new Error("rule not found");
      await updateRule(row.id, { ...full, enabled: !row.enabled });
      setRules((rs) =>
        rs.map((r) => (r.id === row.id ? { ...r, enabled: !row.enabled } : r)),
      );
    } catch {
      setError(true);
    } finally {
      setBusy(null);
    }
  };

  const del = async (row: ParsedRuleRow) => {
    if (busy || !confirm(`Delete rule "${row.name}"?`)) return;
    setBusy(row.id);
    setError(false);
    try {
      await deleteRule(row.id);
      setRules((rs) => rs.filter((r) => r.id !== row.id));
    } catch {
      setError(true);
    } finally {
      setBusy(null);
    }
  };

  return (
    <ToolCardShell
      title={`Rules & settings (${rules.length})`}
      icon={<AppIcon name="Sparkles" size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div className="space-y-1 max-h-80 overflow-y-auto overflow-x-hidden scrollbar-thin pr-0.5">
        {rules.map((r) => (
          <div
            key={r.id}
            className="rounded-md border border-border/70 bg-background/40 px-2 py-1.5"
          >
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-[11px] font-medium text-foreground truncate flex-1 min-w-0">
                {r.name}
              </span>
              {!r.auto && (
                <span
                  className="text-[9px] px-1.5 py-0.5 rounded-full bg-warning/15 text-warning flex-shrink-0"
                  title="Proposes for approval (not auto-applied)"
                >
                  Approval
                </span>
              )}
              {busy === r.id ? (
                <AppIcon name="Loader2"
                  size={11}
                  className="animate-spin text-muted-foreground flex-shrink-0"
                />
              ) : (
                <>
                  <button
                    onClick={() => toggle(r)}
                    disabled={!acctId}
                    title={
                      acctId
                        ? r.enabled
                          ? "Turn this rule off"
                          : "Turn this rule on"
                        : undefined
                    }
                    className={`text-[9px] px-1.5 py-0.5 rounded-full flex-shrink-0 transition-colors ${
                      r.enabled
                        ? "bg-success/15 text-success hover:bg-success/25"
                        : "bg-secondary text-muted-foreground hover:text-foreground"
                    } ${acctId ? "" : "cursor-default"}`}
                  >
                    {r.enabled ? "On" : "Off"}
                  </button>
                  <button
                    onClick={() => del(r)}
                    title="Delete this rule"
                    aria-label="Delete this rule"
                    className="p-0.5 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 flex-shrink-0"
                  >
                    <AppIcon name="Trash2" size={11} />
                  </button>
                </>
              )}
            </div>
            {r.condition && (
              <div className="mt-0.5 text-[10px] text-muted-foreground leading-relaxed line-clamp-2">
                <span className="text-muted-foreground/70">If</span>{" "}
                {r.condition}
              </div>
            )}
            {r.actions.length > 0 && (
              <div className="mt-1 flex flex-wrap items-center gap-1 text-[10px]">
                <span className="text-muted-foreground/70">Then</span>
                {r.actions.map((a, i) => (
                  <ActionChip key={i} action={a} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      {parsed.settings && (
        <div className="mt-1.5 text-[10px] text-muted-foreground">
          {parsed.settings}
        </div>
      )}
      {error && (
        <div className="mt-1 text-[10px] text-destructive">
          Something went wrong — try again.
        </div>
      )}
    </ToolCardShell>
  );
}

// ── Labels card (list_labels) ─────────────────────────────────────────────────

/** The account's labels as their coloured chips instead of one comma-run line. */
function LabelsCard({ event: e }: { event: ToolEvent }) {
  const text = (e.result || "").trim();
  const m = text.match(/^Labels:\s*([\s\S]*)$/);
  const names = m
    ? m[1].split(",").map((s) => s.trim()).filter(Boolean)
    : [];
  // "No user labels…" or an unexpected format → the plain-text card.
  if (names.length === 0) return <InfoResultCard event={e} />;
  return (
    <ToolCardShell
      title={`Labels (${names.length})`}
      icon={<AppIcon name="Tag" size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div className="flex flex-wrap gap-1">
        {names.map((n) => (
          <LabelChip key={n} name={n} icon className="text-[10px] px-2 py-0.5" />
        ))}
      </div>
    </ToolCardShell>
  );
}

// ── Info / list card ──────────────────────────────────────────────────────────

/** Titled, scrollable, full-text card for the read-only list/overview tools
 *  (list_learned_patterns, list_senders, get_rules_and_settings, …). Shows the
 *  WHOLE result (no 160-char truncation) so the list is actually readable, with
 *  collapse + dismiss from ToolCardShell. Counts the bullet lines for the title.
 */
/** Label for the consolidated list_senders tool, chosen by its `view` arg. */
const SENDER_VIEW_META: Record<string, { icon: string; label: string }> = {
  top: { icon: "Mail", label: "Top senders" },
  categories: { icon: "Tag", label: "Sender categories" },
  unsubscribe: { icon: "Archive", label: "Unsubscribe candidates" },
  cold: { icon: "Archive", label: "Cold senders" },
};

function InfoResultCard({ event: e }: { event: ToolEvent }) {
  let meta = INFO_META[e.name] ?? { icon: "Wrench", label: e.name.replace(/_/g, " ") };
  if (e.name === "list_senders") {
    const view = String((e.args as Record<string, unknown> | undefined)?.view ?? "top");
    meta = SENDER_VIEW_META[view] ?? SENDER_VIEW_META.top;
  }
  const iconName = meta.icon;
  const text = (e.result || "").trim();
  const bullets = text.split("\n").filter((l) => l.trim().startsWith("•")).length;
  const title = bullets > 0 ? `${meta.label} (${bullets})` : meta.label;
  return (
    <ToolCardShell
      title={title}
      icon={<AppIcon name={iconName} size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div className="text-[11px] whitespace-pre-wrap break-words text-foreground/90 max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin">
        {text || "(no result)"}
      </div>
    </ToolCardShell>
  );
}

// ── Generic action card ───────────────────────────────────────────────────────

/** Confirmation card for any mutating tool — icon + label + result summary. */
function ActionResultCard({ event: e }: { event: ToolEvent }) {
  const meta = ACTION_META[e.name] ?? { icon: "Wrench", label: e.name.replace(/_/g, " ") };
  const failed = e.status === "error";
  const iconName = failed ? "X" : meta.icon;
  const result = (e.result || "").trim();
  const detail = result.length > 160 ? result.slice(0, 160) + "…" : result;

  return (
    <div
      className={`rounded-lg border px-2.5 py-2 ${
        failed
          ? "border-destructive/40 bg-destructive/5"
          : meta.danger
            ? "border-warning/40 bg-warning/5"
            : "border-sidebar-border bg-secondary/40"
      }`}
    >
      <div className="flex items-start gap-2">
        <span
          className={`mt-0.5 flex-shrink-0 ${
            failed ? "text-destructive" : meta.danger ? "text-warning" : "text-success"
          }`}
        >
          <AppIcon name={iconName} size={13} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium text-foreground">{meta.label}</div>
          {detail && (
            <div className="mt-0.5 text-[10px] text-muted-foreground whitespace-pre-wrap line-clamp-3">
              {detail}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
