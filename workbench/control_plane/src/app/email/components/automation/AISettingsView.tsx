"use client";

import AppIcon, { themedIcon } from "@/components/Icon";
import { useEffect, useState, useCallback } from "react";
import { getProcessPastStatus } from "../../lib/api";
import type { ProcessPastStatus } from "../../lib/api";
import { TestTab } from "./ai-settings/TestTab";
import { HistoryTab } from "./ai-settings/HistoryTab";
import { SettingsTab } from "./ai-settings/SettingsTab";
import { RulesTab, RuleEditorModalLoader } from "./ai-settings/RulesTab";

interface AISettingsViewProps {
  accountId: string | null;
  selectedEmailId: string | null;
  /** YYYY-MM-DD. The Rules tab opens "Process past emails" from this date (EM-T6d). */
  processPastFrom?: string | null;
}

type Tab = "rules" | "test" | "history" | "settings";

const TABS: { key: Tab; label: string; icon: React.ElementType }[] = [
  { key: "rules", label: "Rules", icon: themedIcon("Sparkles") },
  { key: "test", label: "Test", icon: themedIcon("FlaskConical") },
  { key: "history", label: "History", icon: themedIcon("History") },
  { key: "settings", label: "Advanced Settings", icon: themedIcon("Settings2") },
];

/**
 * Poll the live progress of the background "Process past emails" job. Polls once
 * on mount (catches a job already in flight if you navigate back), then every
 * ~1.5s while a job is running, and stops on the terminal status. `ping()`
 * restarts the loop immediately — call it right after launching a new run.
 */
function usePastJobStatus(accountId: string | null) {
  const [status, setStatus] = useState<ProcessPastStatus | null>(null);
  const [watchKey, setWatchKey] = useState(0);
  const ping = useCallback(() => setWatchKey((k) => k + 1), []);

  useEffect(() => {
    if (!accountId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // A single transient failure used to kill the loop outright, freezing the
    // banner mid-progress with no dismiss button (it's hidden while `running`)
    // for the rest of the session. Retry with backoff, and give up loudly —
    // as an `error` status, which IS dismissible — rather than silently.
    let failures = 0;
    const tick = async () => {
      try {
        const s = await getProcessPastStatus(accountId);
        if (cancelled) return;
        failures = 0;
        setStatus(s);
        if (s.status === "running") timer = setTimeout(tick, 1500);
      } catch {
        if (cancelled) return;
        failures += 1;
        if (failures <= 5) {
          timer = setTimeout(tick, 1500 * 2 ** (failures - 1));
        } else {
          setStatus((prev) =>
            prev
              ? { ...prev, status: "error", error: "lost contact with the server" }
              : prev,
          );
        }
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [accountId, watchKey]);

  return { status, ping };
}

/** Slim banner above the tabs: live "Processing N of M…" while the past-emails
 *  job runs, then a one-line outcome the user can dismiss. */
function PastJobBanner({
  job,
  onViewHistory,
  onDismiss,
}: {
  job: ProcessPastStatus;
  onViewHistory: () => void;
  onDismiss: () => void;
}) {
  const running = job.status === "running";
  // Pre-apply phase: the range is still being fetched from the provider, so the
  // total isn't known yet — show an indeterminate bar + "Downloading…".
  const downloading = running && job.phase === "downloading";
  const total = job.total ?? 0;
  const processed = job.processed ?? 0;
  const matched = job.applied ?? 0;
  // Excluded up front because the rules had already run over them — NOT part of
  // `total`. Without this the correct outcome of a repeat run ("Processed 0
  // emails") is indistinguishable from the feature failing to find anything.
  const alreadyDone = job.already_processed ?? 0;
  const pct =
    total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 100;
  const verb = job.dry_run ? "would match a rule" : "matched a rule";
  const doneText =
    total === 0 && alreadyDone > 0
      ? `Nothing new to process — ${alreadyDone} email${
          alreadyDone === 1 ? " was" : "s were"
        } already done.`
      : `Processed ${total} email${total === 1 ? "" : "s"} — ${matched} ${verb}.` +
        (alreadyDone > 0 ? ` ${alreadyDone} already done, skipped.` : "");

  return (
    <div className="border-b border-border bg-secondary/30 flex-shrink-0">
      <div className="max-w-3xl mx-auto w-full px-3 sm:px-5 py-2">
        <div className="flex items-center gap-2">
          {running ? (
            <AppIcon name="Loader2" className="animate-spin text-primary flex-shrink-0" size={14} />
          ) : job.status === "error" ? (
            <AppIcon name="AlertTriangle" className="text-destructive flex-shrink-0" size={14} />
          ) : (
            <AppIcon name="Check" className="text-emerald-500 flex-shrink-0" size={14} />
          )}
          <span className="text-xs text-foreground min-w-0 truncate">
            {running
              ? downloading
                ? "Downloading emails from your mailbox…"
                : `Processing past emails — ${processed} of ${total}…`
              : job.status === "error"
                ? `Processing failed: ${job.error || "unknown error"}`
                : doneText}
          </span>
          <button
            onClick={onViewHistory}
            className="ml-auto text-[11px] text-primary hover:opacity-80 whitespace-nowrap"
          >
            View in History
          </button>
          {!running && (
            <button
              onClick={onDismiss}
              className="text-muted-foreground hover:text-foreground flex-shrink-0"
              title="Dismiss"
            >
              <AppIcon name="X" size={13} />
            </button>
          )}
        </div>
        {running && (
          <div className="mt-1.5 h-1 w-full rounded-full bg-border overflow-hidden">
            <div
              className={`h-full bg-primary ${
                downloading ? "w-full animate-pulse" : "transition-all"
              }`}
              style={downloading ? undefined : { width: `${pct}%` }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export function AISettingsView({ accountId, processPastFrom = null }: AISettingsViewProps) {
  const [tab, setTab] = useState<Tab>("rules");
  // When the user picks "See history" from a rule's ⋯ menu, jump to the History
  // tab pre-filtered to that rule.
  const [historyRuleFilter, setHistoryRuleFilter] = useState("all");
  const seeHistory = (ruleName: string) => {
    setHistoryRuleFilter(ruleName);
    setTab("history");
  };
  // "View matching rule" from a History popover opens that rule's editor as a
  // modal OVER the current tab (RuleEditor is itself a Modal) — the user stays
  // on the History tab (inbox-zero's "View matching rule").
  const [openRuleId, setOpenRuleId] = useState<string | null>(null);
  const viewRule = (ruleId: string) => {
    setOpenRuleId(ruleId);
  };

  // Live progress of the "Process past emails" background job, surfaced as a
  // banner here so it persists across tab switches and after the dialog closes.
  const { status: pastJob, ping: pingPastJob } = usePastJobStatus(accountId);
  const [dismissedJobAt, setDismissedJobAt] = useState<string | null>(null);
  const pastRunning = pastJob?.status === "running";
  // Auto-dismiss the finished summary after a few seconds (running never auto-hides).
  useEffect(() => {
    if (pastJob && (pastJob.status === "done" || pastJob.status === "error")) {
      const fin = pastJob.finished_at ?? "";
      const t = setTimeout(() => setDismissedJobAt(fin), 8000);
      return () => clearTimeout(t);
    }
  }, [pastJob]);
  const showBanner =
    !!accountId &&
    !!pastJob &&
    pastJob.status !== "idle" &&
    !(!pastRunning && dismissedJobAt === (pastJob.finished_at ?? ""));

  return (
    <div className="h-full flex flex-col">
      {showBanner && pastJob && (
        <PastJobBanner
          job={pastJob}
          onViewHistory={() => setTab("history")}
          onDismiss={() => setDismissedJobAt(pastJob.finished_at ?? "")}
        />
      )}
      {/* Sub-tabs — centered to match the content column on wide screens. */}
      <div className="border-b border-border flex-shrink-0">
        <div className="max-w-3xl mx-auto w-full flex items-center gap-1 px-3 sm:px-5 py-2 overflow-x-auto scrollbar-hide">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors flex-shrink-0 whitespace-nowrap ${
                tab === key
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary"
              }`}
            >
              <Icon size={13} /> {label}
            </button>
          ))}
        </div>
      </div>

      {/* Content is centered in a readable column (inbox-zero parity) so the
          tabs don't stretch edge-to-edge on widescreen monitors. */}
      <div className="flex-1 overflow-hidden">
        <div className="h-full max-w-3xl mx-auto w-full">
          {tab === "rules" && (
            <RulesTab
              accountId={accountId}
              onSeeHistory={seeHistory}
              onPastJobStarted={pingPastJob}
              processPastFrom={processPastFrom}
            />
          )}
          {tab === "test" && <TestTab accountId={accountId} />}
          {tab === "history" && (
            <HistoryTab
              accountId={accountId}
              initialRuleFilter={historyRuleFilter}
              live={pastRunning}
              onViewRule={viewRule}
            />
          )}
          {tab === "settings" && <SettingsTab accountId={accountId} />}
        </div>
      </div>
      {/* "View matching rule" opens the rule's editor as a modal over any tab. */}
      {openRuleId && accountId && (
        <RuleEditorModalLoader
          accountId={accountId}
          ruleId={openRuleId}
          onClose={() => setOpenRuleId(null)}
        />
      )}
    </div>
  );
}
