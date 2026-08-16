"use client";

/**
 * The mailbox DASHBOARD (formerly "Digest") — the mission-control view of every
 * open loop: what you owe (needs reply), what's owed to you (waiting on them),
 * what you promised (commitments), and the day's traffic. Every row navigates
 * to its thread; the closing actions (Done / Snooze) live on the row so triage
 * happens HERE instead of in a static summary.
 *
 * Same computation as the scheduled email digest (one aggregate, two
 * projections) — the email is the snapshot, this is the live ledger.
 */

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon } from "@/components/Icon";
import { useEffect, useState, useCallback } from "react";
import { getDigest, resolveThread, sendDigest, snoozeEmail } from "../../lib/api";
import { DigestData, DigestThread } from "../../lib/types";
import { DigestSettingsDialog } from "./DigestSettingsDialog";

interface DashboardViewProps {
  accountId: string | null;
  /** Open a message in the mailbox reading pane (closes the dashboard). */
  onOpenEmail?: (messageId: string) => void;
  /** Filter the mailbox by a category label (category chip click-through). */
  onFilterLabel?: (label: string) => void;
  /** Filter the mailbox by a sender address (noisy-sender click-through). */
  onFilterSender?: (email: string) => void;
  /** Open a thread and start an AI-drafted reply (the ✍️ row action). */
  onDraftReply?: (messageId: string) => void;
  /** Open a waiting-on-them thread and start an AI-drafted follow-up nudge. */
  onNudge?: (messageId: string) => void;
}

export function DashboardView({
  accountId, onOpenEmail, onFilterLabel, onFilterSender, onDraftReply, onNudge,
}: DashboardViewProps) {
  const [period, setPeriod] = useState<"day" | "week">("day");
  const [data, setData] = useState<DigestData | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showConfig, setShowConfig] = useState(false);

  const load = useCallback((quiet = false) => {
    if (!accountId) {
      setLoading(false);
      return;
    }
    if (!quiet) setLoading(true);
    setError(null);
    getDigest(accountId, period)
      .then(setData)
      .catch((e) => setError(e.message || "Failed to build dashboard"))
      .finally(() => setLoading(false));
  }, [accountId, period]);

  useEffect(() => load(), [load]);

  const send = async () => {
    if (!accountId) return;
    setSending(true);
    setSentTo(null);
    try {
      const res = await sendDigest(accountId, period);
      setSentTo(res.to);
    } catch (e) {
      setError((e as Error).message || "Failed to send digest");
    } finally {
      setSending(false);
    }
  };

  /** Optimistically drop a thread from a list, run the action, then re-sync. */
  const dropThread = (
    key: "backlog" | "awaiting", threadId: string, action: Promise<unknown>,
  ) => {
    setData((prev) =>
      prev
        ? {
            ...prev,
            [key]: (prev[key] ?? []).filter((t) => t.thread_id !== threadId),
            totals: {
              ...prev.totals,
              ...(key === "backlog"
                ? { needs_reply: Math.max(0, prev.totals.needs_reply - 1) }
                : { awaiting: Math.max(0, prev.totals.awaiting - 1) }),
            },
          }
        : prev,
    );
    action.then(() => load(true)).catch(() => load(true));
  };

  // Closing actions work on BOTH sides of the ledger: a needs-reply loop can
  // be done/dismissed, and so can a waiting-on-them loop the user no longer
  // cares about (it otherwise sits in the list — and the count — forever).
  const markDone = (key: "backlog" | "awaiting", t: DigestThread) => {
    if (!accountId) return;
    dropThread(key, t.thread_id, resolveThread(accountId, t.thread_id));
  };

  const dismiss = (key: "backlog" | "awaiting", t: DigestThread) => {
    if (!accountId) return;
    dropThread(key, t.thread_id,
      resolveThread(accountId, t.thread_id, { dismiss: true }));
  };

  const snoozeDay = (t: DigestThread) => {
    if (!t.message_id) return;
    const until = new Date();
    until.setDate(until.getDate() + 1);
    until.setHours(8, 0, 0, 0);
    dropThread("backlog", t.thread_id,
      snoozeEmail(t.message_id, until.toISOString()));
  };

  if (!accountId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Select an account first.
      </div>
    );
  }

  const t = data?.totals;

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 sm:px-5 py-3 border-b border-border flex-shrink-0">
        <div className="flex items-center gap-1 bg-secondary rounded-lg p-0.5">
          {(["day", "week"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-2.5 py-1 rounded-md text-xs transition-colors ${
                period === p
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {p === "day" ? "Last day" : "Last week"}
            </button>
          ))}
        </div>
        <button
          onClick={() => setShowConfig(true)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors ml-auto"
        >
          <AppIcon name="Settings2" size={13} /> Configure
        </button>
        <Button layout="flex items-center" onClick={send} disabled={sending || loading}>
          {sending ? <AppIcon name="Loader2" className="animate-spin" size={13} /> : <AppIcon name="Send" size={13} />}
          Send to my inbox
        </Button>
      </div>

      {showConfig && accountId && (
        <DigestSettingsDialog
          accountId={accountId}
          onClose={() => setShowConfig(false)}
        />
      )}

      {sentTo && (
        <div className="px-3 sm:px-5 py-2 text-xs text-emerald-400 bg-emerald-500/10 border-b border-border flex items-center gap-1.5">
          <AppIcon name="Check" size={12} /> Digest sent to {sentTo}
        </div>
      )}
      {error && (
        <div className="px-3 sm:px-5 py-2 text-xs text-destructive bg-destructive/10 border-b border-border">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 sm:px-5 py-4 space-y-5">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-muted-foreground gap-2 text-sm">
            <AppIcon name="Loader2" className="animate-spin" size={16} /> Building dashboard…
          </div>
        ) : !data ? null : (
          <>
            {/* Opt-in morning brief: one LLM sentence orienting the day. Only
                present when the setting is on (empty string otherwise). */}
            {data.brief && (
              <div className="flex items-start gap-2 rounded-xl border border-primary/30 bg-primary/5 px-4 py-3">
                <AppIcon name="Sparkles" size={15} className="text-primary flex-shrink-0 mt-0.5" />
                <p className="text-sm text-foreground leading-snug">{data.brief}</p>
              </div>
            )}

            {/* Stat row. Two different clocks live here: the traffic tiles are
                WINDOWED by the day/week toggle, while the two open-loop tiles
                are ALL open threads regardless of period — each tile says which,
                so "Waiting on them 104" under a "Last day" toggle stops reading
                as one day's mail. */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Stat icon={themedIcon("Mail")} label="In inbox" value={t!.inbox}
                sub={period === "day" ? "last day" : "last week"} />
              <Stat icon={themedIcon("MailOpen")} label="Unread" value={t!.unread}
                sub={period === "day" ? "last day" : "last week"} />
              <Stat icon={themedIcon("Reply")} label="Needs reply" value={t!.needs_reply}
                sub="all open" accent />
              <Stat icon={themedIcon("Hourglass")} label="Waiting on them"
                value={t!.awaiting ?? 0} sub="all open" />
              <Stat icon={themedIcon("Paperclip")} label="Attachments" value={t!.attachments}
                sub={period === "day" ? "last day" : "last week"} />
            </div>

            {/* The two sides of the ledger: what YOU owe, and what's owed to
                you. Every row opens its thread; closing actions live here. */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="bg-card border border-border rounded-xl p-4">
                <h3 className="text-xs font-semibold text-foreground mb-3 flex items-center gap-1.5">
                  <AppIcon name="Reply" size={13} className="text-primary" /> Needs your reply
                  <span
                    className="text-[10px] font-normal text-muted-foreground"
                    title="Ranked by urgency — importance and unread lift a thread above older ones"
                  >
                    · by priority
                  </span>
                  <CountBadge n={t!.needs_reply} />
                </h3>
                {!data.backlog?.length ? (
                  <p className="text-xs text-muted-foreground">
                    Nothing waiting on you. Inbox zero. 🎉
                  </p>
                ) : (
                  <div className="max-h-72 overflow-y-auto pr-1 space-y-0.5">
                    {t!.needs_reply > data.backlog.length && (
                      <p className="text-[10px] text-muted-foreground pb-1">
                        Showing the top {data.backlog.length} of {t!.needs_reply}.
                      </p>
                    )}
                    {data.backlog.map((b) => (
                      <ThreadRow
                        key={b.thread_id}
                        row={b}
                        onOpen={onOpenEmail}
                        actions={
                          <>
                            {b.message_id && onDraftReply && (
                              <RowBtn
                                title="Draft a reply with AI — opens the thread with a draft ready"
                                onClick={() => onDraftReply(b.message_id!)}
                              >
                                <AppIcon name="PenLine" size={12} />
                              </RowBtn>
                            )}
                            <RowBtn
                              title="Mark done — this loop is closed"
                              onClick={() => markDone("backlog", b)}
                            >
                              <AppIcon name="CheckCheck" size={12} />
                            </RowBtn>
                            {b.message_id && (
                              <RowBtn
                                title="Snooze until tomorrow 8:00"
                                onClick={() => snoozeDay(b)}
                              >
                                <AppIcon name="Clock" size={12} />
                              </RowBtn>
                            )}
                            <RowBtn
                              title="Dismiss — never mind this thread (files it as FYI without claiming it's done)"
                              onClick={() => dismiss("backlog", b)}
                            >
                              <AppIcon name="XCircle" size={12} />
                            </RowBtn>
                          </>
                        }
                      />
                    ))}
                  </div>
                )}
              </div>

              <div className="bg-card border border-border rounded-xl p-4">
                <h3 className="text-xs font-semibold text-foreground mb-3 flex items-center gap-1.5">
                  <AppIcon name="Hourglass" size={13} className="text-primary" /> Waiting on them
                  <CountBadge n={t!.awaiting ?? 0} />
                </h3>
                {!data.awaiting?.length ? (
                  <p className="text-xs text-muted-foreground">
                    Nobody owes you a reply right now.
                  </p>
                ) : (
                  <div className="max-h-72 overflow-y-auto pr-1 space-y-0.5">
                    {(t!.awaiting ?? 0) > data.awaiting.length && (
                      <p className="text-[10px] text-muted-foreground pb-1">
                        Showing the longest-waiting {data.awaiting.length} of{" "}
                        {t!.awaiting}.
                      </p>
                    )}
                    {data.awaiting.map((b) => (
                      <ThreadRow
                        key={b.thread_id}
                        row={b}
                        onOpen={onOpenEmail}
                        actions={
                          <>
                            {b.message_id && onNudge && (
                              <RowBtn
                                title="Nudge — open the thread with an AI follow-up draft ready"
                                onClick={() => onNudge(b.message_id!)}
                              >
                                <AppIcon name="BellRing" size={12} />
                              </RowBtn>
                            )}
                            <RowBtn
                              title="Mark done — no longer waiting on this; closes the loop"
                              onClick={() => markDone("awaiting", b)}
                            >
                              <AppIcon name="CheckCheck" size={12} />
                            </RowBtn>
                            <RowBtn
                              title="Dismiss — stop tracking this thread (files it as FYI)"
                              onClick={() => dismiss("awaiting", b)}
                            >
                              <AppIcon name="XCircle" size={12} />
                            </RowBtn>
                          </>
                        }
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Commitments — promises captured from sent replies. */}
            {data.commitments && data.commitments.length > 0 && (
              <div className="bg-card border border-border rounded-xl p-4">
                <h3 className="text-xs font-semibold text-foreground mb-3 flex items-center gap-1.5">
                  <AppIcon name="Check" size={13} className="text-primary" /> Commitments
                  <CountBadge n={data.commitments.length} />
                </h3>
                <div className="space-y-1.5">
                  {data.commitments.map((c, i) => {
                    // Click-through to the email the promise was made in — the
                    // captured title alone rarely carries enough context.
                    const openable = Boolean(onOpenEmail && c.message_id);
                    return (
                      <button
                        key={c.task_id ?? i}
                        onClick={() => openable && onOpenEmail!(c.message_id!)}
                        disabled={!openable}
                        title={
                          openable
                            ? "Open the email this commitment came from"
                            : undefined
                        }
                        className={`group flex items-center justify-between text-xs gap-2 w-full rounded px-1.5 py-0.5 -mx-1.5 ${
                          openable
                            ? "hover:bg-secondary cursor-pointer"
                            : "cursor-default"
                        }`}
                      >
                        <span className="text-foreground truncate flex items-center gap-1 min-w-0">
                          <span className="truncate">{c.title}</span>
                          {openable && (
                            <AppIcon name="ChevronRight"
                              size={11}
                              className="text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
                            />
                          )}
                        </span>
                        <span
                          className={`tabular-nums flex-shrink-0 ${
                            c.overdue && c.due
                              ? "text-destructive"
                              : "text-muted-foreground"
                          }`}
                        >
                          {!c.due
                            ? "no due date"
                            : `${c.overdue ? "overdue" : "due"} ${c.due}`}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* By category */}
              <div className="bg-card border border-border rounded-xl p-4">
                <h3 className="text-xs font-semibold text-foreground mb-3 flex items-center gap-1.5">
                  <AppIcon name="Newspaper" size={13} className="text-primary" /> By category
                </h3>
                <div className="space-y-1.5">
                  {data.by_category.length === 0 && (
                    <p className="text-xs text-muted-foreground">No mail in this period.</p>
                  )}
                  {data.by_category.map((c) => (
                    <button
                      key={c.category}
                      onClick={() => onFilterLabel?.(c.category)}
                      disabled={!onFilterLabel}
                      title={
                        onFilterLabel
                          ? `Show ${c.category} mail in the inbox`
                          : undefined
                      }
                      className={`group flex items-center justify-between text-xs w-full rounded px-1.5 py-0.5 -mx-1.5 ${
                        onFilterLabel
                          ? "hover:bg-secondary cursor-pointer"
                          : "cursor-default"
                      }`}
                    >
                      <span className="text-foreground flex items-center gap-1">
                        {c.category}
                        {onFilterLabel && (
                          <AppIcon name="ChevronRight"
                            size={11}
                            className="text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity"
                          />
                        )}
                      </span>
                      <span className="text-muted-foreground tabular-nums">
                        {c.count}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Noisy senders */}
              <div className="bg-card border border-border rounded-xl p-4">
                <h3 className="text-xs font-semibold text-foreground mb-3 flex items-center gap-1.5">
                  <AppIcon name="Mail" size={13} className="text-primary" /> Noisy senders you never answer
                </h3>
                <div className="space-y-1.5">
                  {data.top_senders.length === 0 && (
                    <p className="text-xs text-muted-foreground">No senders.</p>
                  )}
                  {data.top_senders.map((s) => (
                    <button
                      key={s.email}
                      onClick={() => onFilterSender?.(s.email)}
                      disabled={!onFilterSender}
                      title={
                        onFilterSender
                          ? `Show mail from ${s.name || s.email}`
                          : undefined
                      }
                      className={`group flex items-center justify-between text-xs gap-2 w-full rounded px-1.5 py-0.5 -mx-1.5 ${
                        onFilterSender
                          ? "hover:bg-secondary cursor-pointer"
                          : "cursor-default"
                      }`}
                    >
                      <span className="text-foreground truncate flex items-center gap-1 min-w-0">
                        <span className="truncate">{s.name || s.email}</span>
                        {onFilterSender && (
                          <AppIcon name="ChevronRight"
                            size={11}
                            className="text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
                          />
                        )}
                      </span>
                      <span className="text-muted-foreground tabular-nums flex-shrink-0">
                        {s.count}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function _agePhrase(days: number): string {
  if (days <= 0) return "today";
  if (days === 1) return "1 day";
  return `${days} days`;
}

/** One open-loop row: click anywhere → open the thread; actions on the right. */
function ThreadRow({
  row,
  onOpen,
  actions,
}: {
  row: DigestThread;
  onOpen?: (messageId: string) => void;
  actions?: React.ReactNode;
}) {
  const openable = Boolean(onOpen && row.message_id);
  return (
    <div
      className={`group flex items-center gap-2 text-xs rounded-md px-1.5 py-1 -mx-1.5 ${
        openable ? "cursor-pointer hover:bg-secondary" : ""
      }`}
      onClick={() => openable && onOpen!(row.message_id!)}
      role={openable ? "button" : undefined}
      title={openable ? "Open this conversation" : undefined}
    >
      {row.important && (
        <AppIcon name="AlertTriangle"
          size={11}
          className="text-amber-500 flex-shrink-0"
          aria-label="High importance"
        />
      )}
      {row.unread && !row.important && (
        <span
          className="w-1.5 h-1.5 rounded-full bg-primary flex-shrink-0"
          aria-label="Unread"
        />
      )}
      <span
        className={`truncate flex-1 min-w-0 ${
          row.unread ? "text-foreground font-medium" : "text-foreground"
        }`}
      >
        {row.subject}
        {row.who && (
          <span className="text-muted-foreground font-normal"> — {row.who}</span>
        )}
      </span>
      <span
        className={`tabular-nums flex-shrink-0 ${
          row.age_days > 14 ? "text-amber-500" : "text-muted-foreground"
        }`}
      >
        {_agePhrase(row.age_days)}
      </span>
      {/* Always visible on touch screens (no hover to reveal them); hover-only
          on sm+ where a pointer exists. */}
      <span
        className="flex items-center gap-0.5 flex-shrink-0 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity"
        onClick={(e) => e.stopPropagation()}
      >
        {actions}
        {openable && (
          <RowBtn title="Open" onClick={() => onOpen!(row.message_id!)}>
            <AppIcon name="ExternalLink" size={12} />
          </RowBtn>
        )}
      </span>
    </div>
  );
}

function RowBtn({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-border transition-colors"
    >
      {children}
    </button>
  );
}

function CountBadge({ n }: { n: number }) {
  return (
    <span className="ml-auto text-[10px] font-normal text-muted-foreground bg-secondary rounded-full px-1.5 py-0.5 tabular-nums">
      {n}
    </span>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  sub,
  accent,
}: {
  icon: React.ElementType;
  label: string;
  value: number;
  /** Which clock the number is on: the period window ("last day") or the
   *  all-time open-loop ledger ("all open"). */
  sub?: string;
  accent?: boolean;
}) {
  return (
    <div className="bg-card border border-border rounded-xl p-3">
      <div className="flex items-center gap-1.5 text-muted-foreground mb-1">
        <Icon size={12} />
        <span className="text-[10px] uppercase tracking-wide">{label}</span>
        {sub && (
          <span className="ml-auto text-[9px] text-muted-foreground/60 whitespace-nowrap">
            {sub}
          </span>
        )}
      </div>
      <div
        className={`text-xl font-semibold tabular-nums ${
          accent ? "text-primary" : "text-foreground"
        }`}
      >
        {value}
      </div>
    </div>
  );
}
