"use client";

// The Assistant "History" tab: a log of what the rules engine did — actions it
// applied and the emails where nothing matched — grouped by day, filterable by
// rule, with per-row result pill + Fix. Can live-poll while a "Process past
// emails" job runs. Extracted from AISettingsView.tsx.

import Icon from "@/components/Icon";
import { useCallback, useEffect, useState } from "react";
import { getRulesHistory } from "../../../lib/api";
import type { ExecutedRule, RuleAction } from "../../../lib/types";
import { EmailPreviewModal } from "../../EmailPreviewModal";
import { ActionChip } from "./actionFormat";
import { INPUT_CLS, RuleResultPill, Spinner } from "./common";
import { FixButton } from "./fixDialog";

export function HistoryTab({
  accountId,
  initialRuleFilter = "all",
  live = false,
  onViewRule,
}: {
  accountId: string | null;
  initialRuleFilter?: string;
  live?: boolean;
  onViewRule?: (ruleId: string) => void;
}) {
  const [history, setHistory] = useState<ExecutedRule[]>([]);
  const [loading, setLoading] = useState(true);
  // "all" | "skipped" (No match) | a rule name.
  const [ruleFilter, setRuleFilter] = useState(initialRuleFilter);
  const [previewId, setPreviewId] = useState<string | null>(null);

  // `silent` re-loads (the live poll) keep the list mounted instead of flashing
  // the spinner, so rows appear to stream in as the job applies them.
  const load = useCallback(
    (silent = false) => {
      if (!silent) setLoading(true);
      getRulesHistory(accountId ?? undefined, 200)
        .then(setHistory)
        .catch(() => setHistory([]))
        .finally(() => setLoading(false));
    },
    [accountId]
  );

  useEffect(() => load(), [load]);

  // While a "Process past emails" job runs, poll so applied actions stream in.
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => load(true), 2500);
    return () => clearInterval(id);
  }, [live, load]);

  if (loading) return <Spinner label="Loading history…" />;

  // History is a LOG of what the assistant did — actions it applied, the emails
  // where nothing matched (inbox-zero has no approval queue), and the ones where
  // it tried and the mail server refused. FAILED has to be here: it was
  // introduced precisely because those runs used to be logged as APPLIED, and
  // filtering it out now would hide them a second way.
  const log = history.filter(
    (h) => h.status === "APPLIED" || h.status === "SKIPPED"
      || h.status === "FAILED"
  );
  const ruleNames = Array.from(
    new Set(log.map((h) => h.rule_name).filter((n): n is string => !!n))
  ).sort();
  const filtered = log.filter((h) =>
    ruleFilter === "all"
      ? true
      : ruleFilter === "skipped"
        ? h.status === "SKIPPED"
        : h.rule_name === ruleFilter
  );
  // Newest mail first (by the email's received date, falling back to when the
  // rule ran). The backend orders this way too; sorting here keeps it robust.
  const ts = (h: ExecutedRule) =>
    new Date(h.received_at ?? h.created_at ?? 0).getTime();
  const sorted = [...filtered].sort((a, b) => ts(b) - ts(a));
  const groups = groupHistoryByDate(sorted);

  return (
    <div className="h-full overflow-y-auto px-3 sm:px-5 py-3">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <select
          value={ruleFilter}
          onChange={(e) => setRuleFilter(e.target.value)}
          className={`${INPUT_CLS} w-auto py-1`}
        >
          <option value="all">All rules</option>
          <option value="skipped">No match</option>
          {ruleNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        {live && (
          <span className="flex items-center gap-1.5 text-[11px] text-primary">
            <Icon name="Loader2" className="animate-spin" size={12} /> Processing past emails…
          </span>
        )}
        <button
          onClick={() => load()}
          className="ml-auto text-xs text-primary hover:opacity-80"
        >
          Refresh
        </button>
      </div>

      {filtered.length === 0 ? (
        <div className="text-center text-sm text-muted-foreground py-10">
          {log.length === 0
            ? "No emails have been processed yet."
            : "No entries match this filter."}
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map((g) => (
            <div key={g.key}>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1.5 px-0.5">
                {g.label}
              </div>
              <div className="space-y-1.5">
                {g.items.map((h) => (
                  <HistoryRow
                    key={h.id}
                    h={h}
                    accountId={accountId}
                    onViewRule={onViewRule}
                    onPreview={(id) => setPreviewId(id)}
                    onReran={() => load(true)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {previewId && (
        <EmailPreviewModal
          messageId={previewId}
          onClose={() => setPreviewId(null)}
        />
      )}
    </div>
  );
}

function groupHistoryByDate(items: ExecutedRule[]) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yest = new Date(today);
  yest.setDate(yest.getDate() - 1);
  const groups: { key: string; label: string; items: ExecutedRule[] }[] = [];
  const byKey: Record<string, (typeof groups)[number]> = {};
  for (const h of items) {
    const stamp = h.received_at ?? h.created_at;
    const d = stamp ? new Date(stamp) : null;
    let key = "unknown";
    let label = "Earlier";
    if (d && !isNaN(d.getTime())) {
      const day = new Date(d);
      day.setHours(0, 0, 0, 0);
      key = day.toISOString().slice(0, 10);
      if (day.getTime() === today.getTime()) label = "Today";
      else if (day.getTime() === yest.getTime()) label = "Yesterday";
      else
        label = day.toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
          year: "numeric",
        });
    }
    if (!byKey[key]) {
      byKey[key] = { key, label, items: [] };
      groups.push(byKey[key]);
    }
    byKey[key].items.push(h);
  }
  return groups;
}

function HistoryRow({
  h,
  accountId,
  onViewRule,
  onPreview,
  onReran,
}: {
  h: ExecutedRule;
  accountId: string | null;
  onViewRule?: (ruleId: string) => void;
  onPreview?: (messageId: string) => void;
  onReran?: () => void;
}) {
  const matched = h.status !== "SKIPPED";
  // Show ALL actions the rule applied (label, move to folder, draft, …), not
  // just the label — prefer the matched rule's specs (with args), fall back to
  // the action types actually taken.
  const actionChips: RuleAction[] =
    (h.rule_actions && h.rule_actions.length > 0
      ? h.rule_actions
      : (h.actions || []).map((t) => ({ type: t } as RuleAction)));
  return (
    <div className="flex items-start gap-3 bg-card border border-border rounded-lg px-3 py-2">
      <button
        type="button"
        onClick={() => h.message_id && onPreview?.(h.message_id)}
        disabled={!h.message_id}
        title={h.message_id ? "Preview email" : undefined}
        className="flex-1 min-w-0 text-left cursor-pointer hover:opacity-80 transition-opacity disabled:cursor-default disabled:hover:opacity-100"
      >
        <div className="text-xs text-foreground truncate">
          {h.subject || "(no subject)"}
        </div>
        <div className="text-[11px] text-muted-foreground truncate">{h.from}</div>
        {h.snippet && (
          <div className="text-[10px] text-muted-foreground/70 line-clamp-1 mt-0.5">
            {h.snippet}
          </div>
        )}
        {actionChips.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {actionChips.map((a, i) => (
              <ActionChip key={i} action={a} />
            ))}
          </div>
        )}
        {!h.automated && (
          <span className="inline-block mt-1 text-[9px] px-1.5 py-0.5 rounded-full bg-warning/15 text-warning">
            Applied manually
          </span>
        )}
      </button>
      <div className="flex items-center gap-1.5 flex-shrink-0">
        <RuleResultPill
          matched={matched}
          ruleName={h.rule_name}
          reason={h.reason}
          conditions={h.conditions ?? undefined}
          actionSpecs={h.rule_actions}
          takenTypes={h.actions}
          status={h.status}
          ruleId={h.rule_id ?? undefined}
          onViewRule={onViewRule}
          matchSource={h.match_source}
          actionErrors={h.action_errors}
          draftPreview={h.draft_preview}
        />
        {accountId && (
          <FixButton
            accountId={accountId}
            email={{ subject: h.subject || "", from: h.from || "" }}
            current={{ matched, ruleName: h.rule_name, ruleId: h.rule_id ?? null }}
            messageId={h.message_id ?? null}
            onReran={onReran}
          />
        )}
      </div>
    </div>
  );
}
