"use client";

// Small presentational primitives + the shared rule-result pill, used across the
// Assistant tabs (Rules / Test / History / Settings). Extracted from
// AISettingsView.tsx so a Spinner / Empty / Field / result-pill looks identical
// everywhere and there is a single place to change them.

import Icon from "@/components/Icon";
import { accentForHue } from "@/lib/statusAccent";
import type { RuleAction, RuleConditions } from "../../../lib/types";
import { HoverPopover } from "../ui";
import {
  ActionChip,
  ActionIcon,
  actionColor,
  actionDescription,
  actionLabel,
  conditionTypeSummary,
  matchSourceLabel,
  PrettyConditions,
  statusMeta,
  GONE_MAIL_LABEL,
  isGoneMailSkip,
  reasonPrefix,
  showActionErrors,
} from "./actionFormat";

// Base field styling WITHOUT a width — compose with `w-full` (INPUT_CLS) or a
// fixed width (the action/condition type selects). Keeping `w-full` out of the
// base avoids the Tailwind `w-full`+`w-40` conflict that made the selects span
// the whole row and shove the config card into horizontal overflow.
export const INPUT_BASE =
  "bg-secondary border border-border rounded-lg px-2.5 py-2 text-xs " +
  "text-foreground outline-none focus:border-primary transition-colors";
export const INPUT_CLS = `w-full ${INPUT_BASE}`;

/** One-line summary of a free-text value, trimmed to 90 chars, with a fallback. */
export function summary(value: string | undefined | null, fallback: string): string {
  const v = (value || "").trim().replace(/\s+/g, " ");
  if (!v) return fallback;
  return v.length > 90 ? v.slice(0, 90) + "…" : v;
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs text-muted-foreground mb-1 block">{label}</span>
      {children}
    </label>
  );
}

export function IconAction({
  children,
  onClick,
  title,
  className = "",
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`p-1.5 rounded-md text-muted-foreground hover:bg-secondary transition-colors ${className}`}
    >
      {children}
    </button>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center h-full text-muted-foreground gap-2 text-sm">
      <Icon name="Loader2" className="animate-spin" size={16} /> {label}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
      {children}
    </div>
  );
}

/**
 * A green (matched) / red (no match) rule pill that reveals the rule's
 * conditions, the actions taken, and the AI's reasoning on hover/tap.
 */
export function RuleResultPill({
  matched,
  ruleName,
  reason,
  conditions,
  actionSpecs,
  takenTypes,
  status,
  ruleId,
  onViewRule,
  matchSource,
  actionErrors,
  draftPreview,
  mailGone,
}: {
  matched: boolean;
  ruleName: string | null;
  reason?: string | null;
  conditions?: RuleConditions | null;
  actionSpecs?: RuleAction[];
  takenTypes?: string[];
  status?: string;
  ruleId?: string;
  onViewRule?: (ruleId: string) => void;
  matchSource?: string | null;
  actionErrors?: { type: string; error: string }[] | null;
  draftPreview?: string | null;
  /** The server says this SKIPPED run is a gone-mail skip. */
  mailGone?: boolean;
}) {
  // A rule that matched but whose every action the mail server refused is not a
  // success, and showing it in the same green as a real one is how 138 no-op
  // runs stayed invisible for a month. It is not "No match" either — the
  // assistant decided correctly and the mailbox simply never received it.
  const failed = (status || "").toUpperCase() === "FAILED";
  const gone = isGoneMailSkip({ mail_gone: mailGone });
  const pill = (
    <span
      className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-md whitespace-nowrap ${
        gone
          ? accentForHue("gray").chip
          : failed
          ? "bg-rose-500/15 text-rose-400"
          : matched
            ? "bg-emerald-500/15 text-emerald-400"
            : "bg-red-500/15 text-red-400"
      }`}
    >
      {gone
        ? GONE_MAIL_LABEL
        : failed
        ? `${ruleName || "Matched"} · didn't apply`
        : matched
          ? ruleName || "Matched"
          : "No match found"}
      <Icon name="Eye" size={11} className="opacity-70" />
    </span>
  );
  const specs = actionSpecs ?? [];
  const types = takenTypes ?? [];
  const typeSummary = conditionTypeSummary(conditions);
  const sm = statusMeta(status, mailGone);
  const srcLabel = matchSourceLabel(matchSource);
  const errs = actionErrors ?? [];
  const draft = (draftPreview || "").trim();
  return (
    <HoverPopover trigger={pill}>
      <div className="space-y-2">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs font-medium text-foreground">
            {gone
              ? ruleName || GONE_MAIL_LABEL
              : matched
                ? ruleName || "Matched rule"
                : "No rule matched"}
          </span>
          {typeSummary && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-md bg-secondary text-muted-foreground border border-border">
              {typeSummary}
            </span>
          )}
          {sm && (
            <span className={`text-[9px] px-1.5 py-0.5 rounded-md ${sm.cls}`}>
              {sm.label}
            </span>
          )}
        </div>
        {srcLabel && (
          <div className="text-[10px] text-muted-foreground">
            <span className="text-muted-foreground/60">Matched via: </span>
            {srcLabel}
          </div>
        )}
        {conditions && <PrettyConditions c={conditions} />}
        {(specs.length > 0 || types.length > 0) && (
          <div>
            <div className="text-[9px] uppercase tracking-wide text-muted-foreground/70 mb-1">
              Actions
            </div>
            <div className="flex flex-wrap gap-1">
              {specs.length > 0
                ? specs.map((a, i) => <ActionChip key={i} action={a} />)
                : types.map((t, i) => (
                    <span
                      key={i}
                      title={actionDescription(t)}
                      className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-md border ${actionColor(t)}`}
                    >
                      <ActionIcon type={t} size={10} />
                      {actionLabel(t)}
                    </span>
                  ))}
            </div>
          </div>
        )}
        {draft && (
          <div>
            <div className="text-[9px] uppercase tracking-wide text-muted-foreground/70 mb-1 flex items-center gap-1">
              <Icon name="PenLine" size={10} /> Drafted reply
            </div>
            <div className="text-[10px] text-foreground/90 bg-secondary/40 rounded-md px-2 py-1.5 max-h-40 overflow-y-auto whitespace-pre-wrap break-words">
              {draft}
            </div>
          </div>
        )}
        {reason && (
          <div className="text-[10px] text-muted-foreground/80 bg-secondary/40 rounded-md px-2 py-1.5 italic">
            <span className="not-italic text-muted-foreground/60">
              {reasonPrefix(gone)}{" "}
            </span>
            {reason}
          </div>
        )}
        {showActionErrors(gone, errs.length) && (
          <div className="text-[10px] text-destructive bg-destructive/10 rounded-md px-2 py-1.5">
            <div className="font-medium mb-0.5 flex items-center gap-1">
              <Icon name="AlertTriangle" size={10} /> Action issues
            </div>
            {errs.map((e, i) => (
              <div key={i} className="text-destructive/90">
                {actionLabel(e.type)}: {e.error}
              </div>
            ))}
          </div>
        )}
        {ruleId && onViewRule && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onViewRule(ruleId);
            }}
            className="flex items-center gap-1 text-[11px] text-primary hover:opacity-80 pt-0.5"
          >
            <Icon name="Pencil" size={11} /> View matching rule
          </button>
        )}
      </div>
    </HoverPopover>
  );
}
