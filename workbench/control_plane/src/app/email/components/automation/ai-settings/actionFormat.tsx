"use client";

// Action tables + formatting helpers shared across the Assistant tabs (Rules
// list, Rule editor, Test/History result popovers, Add-rule examples). Extracted
// from AISettingsView.tsx so the same action → {label, icon, color, text} mapping
// is defined ONCE and every surface renders actions identically.

import AppIcon from "@/components/Icon";
import type { RuleAction, RuleActionType, RuleConditions } from "../../../lib/types";

export const ACTION_TYPES: RuleActionType[] = [
  "ARCHIVE", "LABEL", "MARK_READ", "STAR", "MARK_SPAM", "TRASH",
  "MOVE_FOLDER", "REPLY", "FORWARD", "DRAFT_EMAIL", "CALL_WEBHOOK",
];

/**
 * Human-friendly name + one-line explanation for each rule action, so the UI
 * never shows raw enum values like DRAFT_EMAIL / CALL_WEBHOOK. The description
 * is surfaced as a tooltip and under the action editor.
 */
export const ACTION_META: Record<RuleActionType, { label: string; description: string }> = {
  ARCHIVE: { label: "Archive", description: "Remove the email from the inbox." },
  LABEL: { label: "Categorize", description: "Apply a label / category to the email." },
  MARK_READ: { label: "Mark as read", description: "Mark the email as read." },
  STAR: { label: "Star", description: "Star / flag the email." },
  MARK_SPAM: { label: "Mark as spam", description: "Move the email to the spam folder." },
  TRASH: { label: "Move to trash", description: "Send the email to trash." },
  MOVE_FOLDER: { label: "Move to folder", description: "Move the email to a specific folder." },
  REPLY: { label: "Reply", description: "Create a reply draft for your review — never auto-sent." },
  FORWARD: { label: "Forward", description: "Create a forward draft to someone — never auto-sent." },
  DRAFT_EMAIL: { label: "Draft reply", description: "Let the AI write a reply draft for your review — never auto-sent." },
  CALL_WEBHOOK: { label: "Call webhook", description: "POST the email to a URL you specify." },
};

/**
 * Per-action lucide icon, so each action reads at a glance wherever it appears —
 * the action chips under a rule, the Test/History result popovers, the action
 * editor card, and the inferred icon on each Add-rule example.
 */
export const ACTION_ICON: Record<RuleActionType, string> = {
  ARCHIVE: "Archive",
  LABEL: "Tag",
  MARK_READ: "MailOpen",
  STAR: "Star",
  MARK_SPAM: "ShieldAlert",
  TRASH: "Trash2",
  MOVE_FOLDER: "FolderInput",
  REPLY: "Reply",
  FORWARD: "Forward",
  DRAFT_EMAIL: "PenLine",
  CALL_WEBHOOK: "Webhook",
};

export function ActionIcon({ type, size = 11 }: { type: string; size?: number }) {
  const name = ACTION_ICON[type as RuleActionType] ?? "Zap";
  return <AppIcon name={name} size={size} className="flex-shrink-0" />;
}

/**
 * A single colored action chip (icon + human label) shared by the Rules list and
 * the Test/History result popovers, so actions look identical everywhere.
 */
export function ActionChip({ action }: { action: RuleAction }) {
  return (
    <span
      title={actionDescription(action.type)}
      className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-md border ${actionColor(action.type)}`}
    >
      <ActionIcon type={action.type} size={10} />
      {actionText(action)}
    </span>
  );
}

/**
 * Per-action badge colors (bg / text / border) so actions read at a glance in
 * the Rules list, History popovers and Test results — mirrors inbox-zero's
 * colored action chips. Uses Tailwind palette tints that work on light & dark.
 */
export const ACTION_COLOR: Record<RuleActionType, string> = {
  ARCHIVE: "bg-slate-500/10 text-slate-600 dark:text-slate-300 border-slate-500/20",
  LABEL: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  MARK_READ: "bg-slate-500/10 text-slate-600 dark:text-slate-300 border-slate-500/20",
  STAR: "bg-warning/10 text-warning border-warning/20",
  MARK_SPAM: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  TRASH: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
  MOVE_FOLDER: "bg-teal-500/10 text-teal-600 dark:text-teal-400 border-teal-500/20",
  REPLY: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  FORWARD: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/20",
  DRAFT_EMAIL: "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20",
  CALL_WEBHOOK: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
};

export function actionColor(type: string): string {
  return (
    ACTION_COLOR[type as RuleActionType] ??
    "bg-secondary text-muted-foreground border-border"
  );
}

/** Just the `text-…` portion of an action's palette, for standalone icons. */
export function actionIconColor(type: string): string {
  return (
    actionColor(type)
      .split(" ")
      .find((c) => c.startsWith("text-")) ?? "text-muted-foreground"
  );
}

export function actionLabel(type: string): string {
  return ACTION_META[type as RuleActionType]?.label ?? type;
}

/** Friendly description for an action type string. */
export function actionDescription(type: string): string {
  return ACTION_META[type as RuleActionType]?.description ?? "";
}

/** Human label for an action spec (e.g. `Label as "Receipt"`), with the action's
 *  args inline — mirrors inbox-zero's per-action detail in the history popover. */
export function actionText(a: RuleAction): string {
  if (a.type === "LABEL" && a.label) return `Label as "${a.label}"`;
  if (a.type === "MOVE_FOLDER" && a.label) return `Move to "${a.label}"`;
  if (a.type === "FORWARD" && a.to_address) return `Forward to ${a.to_address}`;
  if (a.type === "REPLY" && a.to_address) return `Reply to ${a.to_address}`;
  if (a.type === "CALL_WEBHOOK" && a.url) return "Call webhook";
  if ((a.type === "DRAFT_EMAIL" || a.type === "REPLY") && a.content) {
    const snip = a.content.replace(/\s+/g, " ").trim().slice(0, 40);
    return `${actionLabel(a.type)}: "${snip}${a.content.length > 40 ? "…" : ""}"`;
  }
  return actionLabel(a.type);
}

/** "From + AI" style summary of which condition types a rule uses (inbox-zero
 *  shows this as a badge on the matched rule in the history popover). */
export function conditionTypeSummary(c?: RuleConditions | null): string {
  if (!c) return "";
  const t: string[] = [];
  if (c.instructions) t.push("AI");
  if (c.from_pattern) t.push("From");
  if (c.to_pattern) t.push("To");
  if (c.subject_pattern) t.push("Subject");
  if (c.body_pattern) t.push("Body");
  return t.join(" + ");
}

/** Which condition type fired (inbox-zero's "matched via" / matchMetadata). */
export function matchSourceLabel(src?: string | null): string | null {
  switch ((src || "").toLowerCase()) {
    case "ai":
      return "AI instructions";
    case "pattern":
      return "a learned pattern";
    case "static":
      return "static conditions (from/subject/…)";
    default:
      return null;
  }
}

/** Friendly label + tint for an executed-rule status (History popover). */
export function statusMeta(status?: string): { label: string; cls: string } | null {
  switch ((status || "").toUpperCase()) {
    case "APPLIED":
      return { label: "Applied", cls: "bg-emerald-500/15 text-emerald-400" };
    case "SKIPPED":
      return { label: "No match", cls: "bg-red-500/15 text-red-400" };
    case "PENDING":
      return { label: "Preview", cls: "bg-warning/15 text-warning" };
    case "FAILED":
      // The rule matched and every one of its actions was refused by the mail
      // server, so the mailbox is unchanged. Distinct from "No match", which
      // means the assistant correctly decided to do nothing.
      return { label: "Didn't apply", cls: "bg-rose-500/15 text-rose-400" };
    case "UNDONE":
      return { label: "Undone", cls: "bg-slate-500/15 text-slate-400" };
    case "ERROR":
      return { label: "Error", cls: "bg-rose-500/15 text-rose-400" };
    default:
      return null;
  }
}

/**
 * Infer the headline action an example sentence performs, so each Add-rule
 * example can show a matching action icon. Communication/cleanup verbs win over
 * the near-ubiquitous "label" so the icons read with variety; defaults to LABEL.
 */
export function exampleActionType(text: string): RuleActionType {
  const t = text.toLowerCase();
  if (/\bforward(s|ed|ing)?\b/.test(t)) return "FORWARD";
  if (/\b(draft|repl(y|ies)|respond)\b/.test(t)) return "DRAFT_EMAIL";
  if (/\bspam\b/.test(t)) return "MARK_SPAM";
  if (/\bstar(red)?\b|\bflag(ged)?\b/.test(t)) return "STAR";
  if (/\b(trash|delete)\b/.test(t)) return "TRASH";
  if (/\bfolder\b/.test(t)) return "MOVE_FOLDER";
  if (/\barchive(s|d)?\b/.test(t)) return "ARCHIVE";
  return "LABEL";
}

/** Render a rule's conditions the way inbox-zero's popover does. */
export function PrettyConditions({ c }: { c: RuleConditions }) {
  const parts: { k: string; v: string }[] = [];
  if (c.instructions) parts.push({ k: "AI", v: c.instructions });
  if (c.from_pattern) parts.push({ k: "From", v: c.from_pattern });
  if (c.to_pattern) parts.push({ k: "To", v: c.to_pattern });
  if (c.subject_pattern) parts.push({ k: "Subject", v: c.subject_pattern });
  if (c.body_pattern) parts.push({ k: "Body", v: c.body_pattern });
  if (parts.length === 0) return null;
  const op = c.conditional_operator === "OR" ? "ANY" : "ALL";
  return (
    <div className="text-[11px] text-muted-foreground space-y-0.5">
      {parts.length > 1 && (
        <div className="text-[9px] uppercase tracking-wide text-muted-foreground/70">
          Match {op}:
        </div>
      )}
      {parts.map((p, i) => (
        <div key={i}>
          <span className="text-foreground/80">{p.k}:</span> {p.v}
        </div>
      ))}
    </div>
  );
}
