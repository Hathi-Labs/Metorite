"use client";

/* Analytics — "am I on top of my mail, and what is wasting my time?"
 *
 * The old version of this screen answered "what is in my mailbox?", which the
 * sidebar already answers, and answered it under a heading that was false: the
 * caption read "last 30 days" while the Total/Unread/Top-senders figures had no
 * date filter at all. Every number here is now scoped to the range selector,
 * except the reply backlog — a standing level, not a flow — which says "right
 * now" in its own heading rather than borrowing the range's.
 *
 * The rule for what earns a panel: a user must be able to DO something about
 * it. Read-rate, starred count and per-folder totals were cut for failing that.
 * Every list here ends in a button.
 */

import AppIcon, { themedIcon } from "@/components/Icon";
import { useEffect, useMemo, useState } from "react";
import { getAnalyticsOverview, retryFailedRuleActions } from "../../lib/api";
import { AnalyticsOverview, AutomationFeature, NoisySender } from "../../lib/types";

interface AnalyticsViewProps {
  accountId: string | null;
  /** Jump to another automation surface — every finding here ends in an action,
   *  and an insight the user cannot act on from the same screen is a dead end. */
  onNavigate?: (feature: AutomationFeature) => void;
}

const RANGES = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
  { label: "1y", days: 365 },
];

/* Age bands share one urgency ramp: unremarkable → worth a look → overdue.
 * Keyed by the server's labels so both backlog sides bucket identically. */
const AGE_TONE: Record<string, string> = {
  today: "var(--muted-foreground)",
  "1-3d": "var(--primary)",
  "3-7d": "var(--primary)",
  "1-4w": "var(--accent)",
  "30d+": "var(--destructive)",
};

/** Hours → the coarsest unit that still reads precisely. 0.4 → "24m". */
function humanHours(h: number | null): string {
  if (h === null || Number.isNaN(h)) return "—";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`;
  if (h < 48) return `${h < 10 ? h.toFixed(1) : Math.round(h)}h`;
  return `${(h / 24).toFixed(h / 24 < 10 ? 1 : 0)}d`;
}

function humanDays(d: number | null): string {
  if (d === null) return "—";
  if (d < 1) return "today";
  if (d < 60) return `${Math.round(d)}d`;
  return `${Math.round(d / 30)}mo`;
}

export function AnalyticsView({ accountId, onNavigate }: AnalyticsViewProps) {
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);
  // Bumped by "Try again" to force a refetch. setDays(d => d) is a no-op — React
  // bails on an unchanged value, so the effect never re-ran and the button did
  // nothing after an error.
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getAnalyticsOverview(accountId ?? undefined, days)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message || "Failed to load analytics"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [accountId, days, refresh]);

  const rangeBar = (
    <div className="flex items-center gap-1 bg-secondary rounded-lg p-0.5 flex-shrink-0">
      {RANGES.map((r) => (
        <button
          key={r.days}
          onClick={() => setDays(r.days)}
          aria-pressed={days === r.days}
          className={`px-2.5 py-1 rounded-md text-xs transition-colors duration-150
            focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
            days === r.days
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground hover:bg-background/60"
          }`}
        >
          {r.label}
        </button>
      ))}
    </div>
  );

  if (error) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 px-6 text-center">
        <AppIcon name="TriangleAlert" size={20} className="text-destructive" />
        <p className="text-sm text-foreground">{error}</p>
        <button
          onClick={() => setRefresh((n) => n + 1)}
          className="text-xs text-primary hover:underline"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 sm:px-6 py-5 space-y-8">
        <header className="flex items-start justify-between gap-4">
          <p className="text-xs text-muted-foreground leading-relaxed max-w-md">
            {`Mail flow over the last ${days === 365 ? "year" : `${days} days`}, `}
            {"compared with the "}
            {days === 365 ? "year" : `${days} days`}
            {" before it. The reply backlog is current, not windowed."}
          </p>
          {rangeBar}
        </header>

        {loading || !data ? (
          <Skeleton />
        ) : (
          <>
            <DataHealth data={data} />
            <KeepingUp data={data} />
            <Backlog data={data} onNavigate={onNavigate} />
            <Volume data={data} days={days} />
            <NeverReplied data={data} days={days} onNavigate={onNavigate} />
            <Arrivals data={data} />
            <Assistant
              data={data}
              accountId={accountId}
              onNavigate={onNavigate}
            />
          </>
        )}
      </div>
    </div>
  );
}

/* ── Data-health alarm ────────────────────────────────────────────────────── */

// The one-classification-per-conversation invariant (#110) made visible: shown
// only when it's been violated, so a healthy mailbox sees nothing. Non-zero
// means the cleaner/runner re-damaged statused conversations and the repair
// path needs to run.
function DataHealth({ data }: { data: AnalyticsOverview }) {
  const damaged = data.data_health?.damaged_threads ?? 0;
  if (damaged <= 0) return null;
  return (
    <div className="rounded-xl border border-warning/30 bg-warning/10 px-4 py-3">
      <div className="text-[10px] uppercase tracking-wide text-warning">
        Conversation health
      </div>
      <p className="mt-1 text-sm text-foreground">
        {damaged === 1
          ? "1 conversation is showing a stale label or a misfiled message."
          : `${damaged} conversations are showing a stale label or a misfiled message.`}
      </p>
      <p className="mt-0.5 text-[11px] text-muted-foreground">
        These heal automatically on the next message in the thread; a full
        repair can be run manually if they persist.
      </p>
    </div>
  );
}

/* ── Section 1 · keeping up ───────────────────────────────────────────────── */

function KeepingUp({ data }: { data: AnalyticsOverview }) {
  const r = data.responsiveness;
  const f = data.flow;
  return (
    <section>
      <SectionHead
        icon={themedIcon("Clock")}
        title="Keeping up"
        note="Measured per conversation, not per message — five emails in one thread are one thing to answer."
      />
      {/* One divided panel rather than four cards: these read as a single
          sentence about the period, and card-per-number breaks that up. The
          hairlines are a 1px grid gap showing the border colour through —
          per-cell borders can't stay correct across both breakpoints. */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-px bg-border border border-border rounded-xl overflow-hidden">
        <Figure
          label="Arrived"
          value={f.received.toLocaleString()}
          prev={f.received_prev}
          now={f.received}
          better="down"
          kind="count"
          sub={`${f.sent.toLocaleString()} sent`}
        />
        <Figure
          label="First reply, typically"
          value={humanHours(r.median_hours)}
          prev={r.median_hours_prev}
          now={r.median_hours}
          better="down"
          sub={r.p90_hours !== null ? `slowest 10% over ${humanHours(r.p90_hours)}` : "no replies yet"}
        />
        <Figure
          label="Threads answered"
          value={`${Math.round(r.reply_rate * 100)}%`}
          prev={r.reply_rate_prev}
          now={r.reply_rate}
          better="up"
          sub={`${r.replied_threads} of ${r.inbound_threads}`}
        />
        <Figure
          label="Handled for you"
          value={f.auto_handled.toLocaleString()}
          sub={`${Math.round(f.auto_handled_rate * 100)}% of arrivals`}
        />
      </div>
      {r.unreplied_threads > 0 && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {`Timings cover the ${r.replied_threads} threads that did get a reply. `}
          {`${r.unreplied_threads} more arrived and never did — they are in the backlog below, not in the average.`}
        </p>
      )}
    </section>
  );
}

/** A figure with its trend. `better` says which direction is good, so the
 *  colour means something instead of just "changed". */
function Figure({
  label, value, sub, now, prev, better, kind,
}: {
  label: string;
  value: string;
  sub?: string;
  now?: number | null;
  prev?: number | null;
  better?: Direction;
  kind?: "count" | "scalar";
}) {
  return (
    <div className="bg-card px-4 py-3.5">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-xl font-semibold text-foreground tabular-nums">
          {value}
        </span>
        <Delta now={now} prev={prev} better={better} kind={kind} />
      </div>
      {sub && (
        <div className="mt-0.5 text-[11px] text-muted-foreground truncate" title={sub}>
          {sub}
        </div>
      )}
    </div>
  );
}

/** Which way is good. "neutral" shows the movement without judging it — more
 *  Cold Email is not a success and not a failure, it is just a fact. */
type Direction = "up" | "down" | "neutral";

/* Below this baseline a percentage stops carrying information: three
 * newsletters last month and sixty-two this month is a real and interesting
 * change, but rendering it as "+1967%" makes it look like a measurement rather
 * than the rounding artefact of dividing by three. Under the floor we show the
 * absolute movement instead, which is both smaller and truer.
 *
 * This applies to COUNTS only. A median of 5.8 hours or a rate of 0.125 is
 * already normalised — comparing it against a floor of ten would send every
 * duration and every percentage down the absolute-difference path, which is
 * how "2.3h, down 60%" first rendered as the meaningless "2.3h, down 4". */
const PCT_MIN_BASE = 10;

function Delta({
  now, prev, better, kind = "scalar",
}: {
  now?: number | null;
  prev?: number | null;
  better?: Direction;
  kind?: "count" | "scalar";
}) {
  if (now == null || prev == null || !better) return null;
  // No baseline at all is not the same as no change; "0%" against an empty
  // previous period would invent a trend out of a first-ever measurement.
  if (prev === 0) return null;
  if (now === prev) {
    return (
      <span className="flex items-center gap-0.5 text-[11px] text-muted-foreground">
        <AppIcon name="Minus" size={11} /> flat
      </span>
    );
  }
  const up = now > prev;
  const arrow = up ? "ArrowUpRight" : "ArrowDownRight";
  const color =
    better === "neutral"
      ? "var(--muted-foreground)"
      : better === (up ? "up" : "down")
        ? "var(--success)"
        : "var(--accent)";
  const pct = Math.round(((now - prev) / Math.abs(prev)) * 100);
  const shown =
    kind === "count" && Math.abs(prev) < PCT_MIN_BASE
      ? Math.round(Math.abs(now - prev)).toLocaleString()
      : `${Math.abs(pct)}%`;
  return (
    <span
      className="flex items-center gap-0.5 text-[11px] tabular-nums"
      style={{ color }}
      title={`${up ? "Up" : "Down"} from ${prev.toLocaleString()} in the previous period`}
    >
      <AppIcon name={arrow} size={11} />
      {shown}
    </span>
  );
}

/* ── Section 2 · backlog ──────────────────────────────────────────────────── */

function Backlog({
  data, onNavigate,
}: {
  data: AnalyticsOverview;
  onNavigate?: (f: AutomationFeature) => void;
}) {
  const { needs_reply, awaiting, coverage } = data.backlog;
  const blind = coverage.rate < 0.95 && coverage.total > 0;
  return (
    <section>
      <SectionHead
        icon={themedIcon("Users")}
        title="Open threads right now"
        note="Not affected by the range above — a conversation left hanging in March is the whole point."
      />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <BacklogSide
          title="They're waiting on you"
          side={needs_reply}
          emphasis
        />
        <BacklogSide title="You're waiting on them" side={awaiting} />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
        {blind ? (
          <>
            <AppIcon name="TriangleAlert" size={11} className="text-accent flex-shrink-0" />
            <span className="text-foreground/80">
              {`Reply Zero has read ${coverage.classified.toLocaleString()} of ${coverage.total.toLocaleString()} conversations (${Math.round(coverage.rate * 100)}%).`}
            </span>
            <span className="text-muted-foreground">
              The counts above only cover what it has seen.
            </span>
          </>
        ) : (
          <>
            <AppIcon name="CheckCheck" size={11} className="text-success flex-shrink-0" />
            <span className="text-muted-foreground">
              {`Reply Zero has classified all ${coverage.total.toLocaleString()} conversations, so these counts are complete.`}
            </span>
          </>
        )}
        {onNavigate && (
          <button
            onClick={() => onNavigate("ai-settings")}
            className="ml-auto text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            AI Settings
          </button>
        )}
      </div>
    </section>
  );
}

function BacklogSide({
  title, side, emphasis,
}: {
  title: string;
  side: AnalyticsOverview["backlog"]["needs_reply"];
  emphasis?: boolean;
}) {
  const total = side.threads;
  const overdue = side.buckets.find((b) => b.label === "30d+")?.count ?? 0;
  return (
    <div className="border border-border rounded-xl bg-card p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="text-xs font-medium text-foreground">{title}</h4>
        <span className="text-[11px] text-muted-foreground">
          {total > 0 ? `oldest ${humanDays(side.oldest_days)}` : ""}
        </span>
      </div>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span
          className="text-2xl font-semibold tabular-nums"
          style={{ color: emphasis && total > 0 ? "var(--accent)" : "var(--foreground)" }}
        >
          {total}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {total === 1 ? "conversation" : "conversations"}
        </span>
      </div>

      {total === 0 ? (
        <p className="mt-3 text-[11px] text-muted-foreground">
          Nothing outstanding. This side of the ledger is clear.
        </p>
      ) : (
        <>
          <div className="mt-3 flex h-2 rounded-full overflow-hidden bg-secondary">
            {side.buckets.map((b) =>
              b.count === 0 ? null : (
                <div
                  key={b.label}
                  className="h-full transition-[width] duration-200"
                  style={{
                    width: `${(b.count / total) * 100}%`,
                    background: AGE_TONE[b.label] ?? "var(--primary)",
                  }}
                  title={`${b.count} waiting ${b.label}`}
                />
              )
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
            {side.buckets.map((b) =>
              b.count === 0 ? null : (
                <span
                  key={b.label}
                  className="flex items-center gap-1 text-[10px] text-muted-foreground tabular-nums"
                >
                  <span
                    className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                    style={{ background: AGE_TONE[b.label] ?? "var(--primary)" }}
                  />
                  {`${b.label} · ${b.count}`}
                </span>
              )
            )}
          </div>
          {overdue > 0 && (
            <p className="mt-2.5 text-[11px] text-foreground/80">
              {overdue === 1
                ? "1 has been sitting for over a month."
                : `${overdue} have been sitting for over a month.`}
            </p>
          )}
        </>
      )}
    </div>
  );
}

/* ── Section 3 · volume ───────────────────────────────────────────────────── */

function Volume({ data, days }: { data: AnalyticsOverview; days: number }) {
  // 365 daily bars in a 900px panel is a 2px sliver each — unreadable and
  // meaningless. Past a quarter, roll up to weeks so a bar stays a bar.
  const { bars, unit } = useMemo(() => {
    const rows = data.volume;
    if (days <= 90) return { bars: rows, unit: "day" as const };
    const weeks = new Map<string, { day: string; received: number; sent: number }>();
    for (const r of rows) {
      const d = new Date(`${r.day}T00:00:00Z`);
      d.setUTCDate(d.getUTCDate() - d.getUTCDay());
      const key = d.toISOString().slice(0, 10);
      const w = weeks.get(key) ?? { day: key, received: 0, sent: 0 };
      w.received += r.received;
      w.sent += r.sent;
      weeks.set(key, w);
    }
    return { bars: [...weeks.values()], unit: "week" as const };
  }, [data.volume, days]);

  const max = Math.max(1, ...bars.map((v) => v.received + v.sent));

  return (
    <section>
      <SectionHead icon={themedIcon("BarChart3")} title="Mail in and out" />
      {bars.length === 0 ? (
        <Empty>No mail arrived in this period.</Empty>
      ) : (
        <div className="border border-border rounded-xl bg-card p-4">
          {/* items-stretch, NOT items-end: with align-items:flex-end each
              column shrinks to its content height, so the bars' percentage
              heights resolve against `auto` and collapse to nothing. */}
          <div className="flex items-stretch gap-[2px] h-32" role="img"
               aria-label={`Mail volume by ${unit}`}>
            {bars.map((v) => {
              const total = v.received + v.sent;
              return (
                <div
                  key={v.day}
                  className="flex-1 min-w-[2px] h-full flex flex-col justify-end group"
                  title={`${v.day} · ${v.received} in, ${v.sent} out`}
                >
                  {/* Sent sits on top of received as one solid stack. Only the
                      topmost present segment gets rounded corners, or the
                      radius lands in the middle of the bar. */}
                  <div
                    className="w-full transition-opacity duration-150 group-hover:opacity-80 rounded-t-sm"
                    style={{
                      height: `${(v.sent / max) * 100}%`,
                      background: "var(--muted-foreground)",
                    }}
                  />
                  <div
                    className={`w-full transition-opacity duration-150 group-hover:opacity-80 ${
                      v.sent === 0 ? "rounded-t-sm" : ""
                    }`}
                    style={{
                      height: `${(v.received / max) * 100}%`,
                      minHeight: total > 0 ? 2 : 0,
                      background: "var(--primary)",
                    }}
                  />
                </div>
              );
            })}
          </div>
          <div className="flex items-center justify-between mt-3">
            <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-sm" style={{ background: "var(--primary)" }} />
                Received
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-sm" style={{ background: "var(--muted-foreground)" }} />
                Sent
              </span>
            </div>
            <span className="text-[10px] text-muted-foreground">
              {unit === "week" ? "one bar per week" : "one bar per day"}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

/* ── Section 4 · never replied ────────────────────────────────────────────── */

function NeverReplied({
  data, days, onNavigate,
}: {
  data: AnalyticsOverview;
  days: number;
  onNavigate?: (f: AutomationFeature) => void;
}) {
  const senders = data.noisy_senders;
  const projected = senders.reduce((n, s) => n + s.projected_yearly, 0);
  const max = Math.max(1, ...senders.map((s) => s.messages));

  return (
    <section>
      <SectionHead
        icon={themedIcon("MailMinus")}
        title="You have never replied to these"
        note="Ranked by unread, not volume — the loudest sender is usually a colleague, and that is not a problem you can fix."
        action={
          senders.length > 0 && onNavigate ? (
            <button
              onClick={() => onNavigate("unsubscribe")}
              className="flex items-center gap-1 text-xs text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
            >
              Clean these up <AppIcon name="ArrowRight" size={12} />
            </button>
          ) : null
        }
      />
      {senders.length === 0 ? (
        <Empty>
          Every sender in this period has had a reply from you at some point.
          Nothing here to silence.
        </Empty>
      ) : (
        <div className="border border-border rounded-xl bg-card divide-y divide-border overflow-hidden">
          {senders.map((s) => (
            <SenderRow key={s.email} s={s} max={max} />
          ))}
          <div className="px-4 py-2.5 text-[11px] text-muted-foreground bg-secondary/40">
            {`At this rate these senders will deliver about ${projected.toLocaleString()} emails over the next year.`}
          </div>
        </div>
      )}
    </section>
  );
}

function SenderRow({ s, max }: { s: NoisySender; max: number }) {
  const readPct = s.messages > 0 ? s.read / s.messages : 0;
  return (
    <div className="px-4 py-2.5 hover:bg-secondary/40 transition-colors duration-150">
      <div className="flex items-baseline gap-2">
        <span className="text-xs text-foreground truncate min-w-0" title={s.email}>
          {s.name || s.email}
        </span>
        {s.has_unsubscribe && (
          <span className="text-[9px] px-1.5 py-px rounded-full bg-secondary text-muted-foreground flex-shrink-0">
            unsubscribable
          </span>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground tabular-nums flex-shrink-0">
          {`~${s.projected_yearly.toLocaleString()}/yr`}
        </span>
      </div>
      {s.name && (
        <div className="text-[10px] text-muted-foreground truncate" title={s.email}>
          {s.email}
        </div>
      )}
      <div className="mt-1.5 flex items-center gap-2">
        <div className="flex-1 h-1.5 rounded-full bg-secondary overflow-hidden">
          <div
            className="h-full rounded-full"
            style={{
              width: `${(s.messages / max) * 100}%`,
              background: readPct < 0.2 ? "var(--accent)" : "var(--primary)",
            }}
          />
        </div>
        <span className="text-[10px] text-muted-foreground tabular-nums flex-shrink-0 w-32 text-right">
          {`${s.messages} emails · ${s.unread} unread`}
        </span>
      </div>
    </div>
  );
}

/* ── Section 5 · arrivals ─────────────────────────────────────────────────── */

function Arrivals({ data }: { data: AnalyticsOverview }) {
  const cats = data.categories;
  const max = Math.max(1, ...cats.map((c) => c.count));
  const unclassified = cats.find((c) => c.category === "(uncategorized)");

  return (
    <section>
      <SectionHead icon={themedIcon("Inbox")} title="What arrived" />
      {cats.length === 0 ? (
        <Empty>No mail arrived in this period.</Empty>
      ) : (
        <div className="border border-border rounded-xl bg-card p-4 space-y-2">
          {cats.map((c) => (
            <div key={c.category} className="flex items-center gap-3">
              <span
                className="text-xs w-32 sm:w-40 flex-shrink-0 truncate"
                style={{
                  color: c.category === "(uncategorized)"
                    ? "var(--muted-foreground)" : "var(--foreground)",
                }}
                title={c.category}
              >
                {c.category}
              </span>
              <div className="flex-1 h-2 rounded-full bg-secondary overflow-hidden">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${(c.count / max) * 100}%`,
                    background: c.category === "(uncategorized)"
                      ? "var(--muted-foreground)" : "var(--primary)",
                  }}
                />
              </div>
              <span className="text-[11px] text-muted-foreground tabular-nums w-10 text-right flex-shrink-0">
                {c.count}
              </span>
              <span className="w-12 flex-shrink-0 flex justify-end">
                <Delta
                  now={c.count}
                  prev={c.prev_count}
                  better="neutral"
                  kind="count"
                />
              </span>
            </div>
          ))}
          {unclassified && unclassified.count > 0 && (
            <p className="pt-2 text-[11px] text-muted-foreground border-t border-border">
              {`${unclassified.count.toLocaleString()} of these carry no category yet. Run "Process past emails" in AI Settings to classify them.`}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

/* ── Section 6 · assistant ────────────────────────────────────────────────── */

function Assistant({
  data, accountId, onNavigate,
}: {
  data: AnalyticsOverview;
  accountId: string | null;
  onNavigate?: (f: AutomationFeature) => void;
}) {
  const rs = data.rule_stats;
  const actions = data.action_stats ?? [];
  if (!rs) return null;
  const t = rs.trust;

  return (
    <section className="pb-4">
      <SectionHead
        icon={themedIcon("Sparkles")}
        title="The assistant"
        note="Not just how much it did — whether it is doing it correctly."
        action={
          onNavigate ? (
            <button
              onClick={() => onNavigate("ai-settings")}
              className="flex items-center gap-1 text-xs text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
            >
              AI Settings <AppIcon name="ArrowRight" size={12} />
            </button>
          ) : null
        }
      />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="border border-border rounded-xl bg-card p-4 md:col-span-2">
          <h4 className="text-xs font-medium text-foreground mb-3">
            Emails handled, by rule
          </h4>
          {rs.by_rule.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              No rules have run in this period.
            </p>
          ) : (
            <div className="space-y-1.5">
              {rs.by_rule.map((r) => (
                <div key={r.rule_name} className="flex items-center gap-3">
                  <span className="text-xs text-foreground truncate flex-1" title={r.rule_name}>
                    {r.rule_name}
                  </span>
                  <div className="w-24 sm:w-40 h-1.5 rounded-full bg-secondary overflow-hidden flex-shrink-0">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${(r.count / Math.max(1, rs.by_rule[0].count)) * 100}%`,
                        background: "var(--primary)",
                      }}
                    />
                  </div>
                  <span className="text-[11px] text-muted-foreground tabular-nums w-10 text-right flex-shrink-0">
                    {r.count}
                  </span>
                </div>
              ))}
            </div>
          )}
          {actions.length > 0 && (
            <div className="mt-3 pt-3 border-t border-border flex flex-wrap gap-x-3 gap-y-1">
              {actions.map((a) => (
                <span key={a.action} className="text-[10px] text-muted-foreground tabular-nums">
                  {`${a.action.toLowerCase().replace(/_/g, " ")} ${a.count}`}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="border border-border rounded-xl bg-card p-4">
          <h4 className="text-xs font-medium text-foreground mb-3">
            Can you trust it?
          </h4>
          <dl className="space-y-2.5">
            <TrustRow
              term="You overruled it"
              value={t.decided > 0 ? `${Math.round(t.rejection_rate * 100)}%` : "—"}
              detail={t.decided > 0 ? `${t.rejected} of ${t.decided} decisions` : "nothing decided yet"}
              alarming={t.rejection_rate > 0.1}
            />
            <TrustRow
              term="Actions to repair"
              value={t.repairable.toLocaleString()}
              detail={
                t.repairable > 0
                  ? "the rule matched, but the mail server refused — retryable"
                  : t.permanent_failures > 0
                    ? `${t.permanent_failures.toLocaleString()} failed for good ` +
                      `(the message was moved or deleted)`
                    : "every action reached the mail server"
              }
              alarming={t.repairable > 0}
              action={
                t.repairable > 0 && accountId ? (
                  <RetryFailed accountId={accountId} />
                ) : null
              }
            />
            <TrustRow
              term="Patterns awaiting review"
              value={t.unreviewed_patterns.toLocaleString()}
              detail={
                t.unreviewed_patterns > 0
                  ? "the Email Cleaner ignores these until you approve them"
                  : "nothing queued"
              }
              alarming={t.unreviewed_patterns > 0}
            />
          </dl>
        </div>
      </div>
    </section>
  );
}

function TrustRow({
  term, value, detail, alarming, action,
}: {
  term: string;
  value: string;
  detail: string;
  alarming?: boolean;
  action?: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <dt className="text-[11px] text-muted-foreground">{term}</dt>
        <dd
          className="text-sm font-semibold tabular-nums"
          style={{ color: alarming ? "var(--accent)" : "var(--foreground)" }}
        >
          {value}
        </dd>
      </div>
      <p className="text-[10px] text-muted-foreground leading-snug">{detail}</p>
      {action}
    </div>
  );
}

/** Re-apply the refused actions. Deliberately states what it will NOT do:
 *  "retry" on a mail assistant could plausibly mean re-sending something, and a
 *  button whose blast radius is ambiguous does not get pressed. */
function RetryFailed({ accountId }: { accountId: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setResult(null);
    try {
      const r = await retryFailedRuleActions(accountId);
      setResult(
        r.repaired === 0 && r.still_failing === 0
          ? "Nothing left to repair."
          : `Repaired ${r.repaired}.` +
            (r.still_failing > 0
              ? ` ${r.still_failing} could not be — those messages are gone from the mailbox.`
              : "")
      );
    } catch (e) {
      setResult((e as Error).message || "Retry failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-1.5">
      <button
        onClick={run}
        disabled={busy}
        title="Re-applies the label and folder move only — never drafts, sends or forwards"
        className="flex items-center gap-1 text-[10px] text-primary hover:underline disabled:opacity-60 disabled:cursor-default focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
      >
        {busy ? <AppIcon name="Loader2" className="animate-spin" size={10} /> : <AppIcon name="RotateCcw" size={10} />}
        {busy ? "Re-applying…" : "Re-apply them (no drafts or sends)"}
      </button>
      {result && (
        <p className="mt-1 text-[10px] text-muted-foreground">{result}</p>
      )}
    </div>
  );
}

/* ── shared ───────────────────────────────────────────────────────────────── */

function SectionHead({
  icon: Icon, title, note, action,
}: {
  icon: React.ElementType;
  title: string;
  note?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-2.5 flex items-start justify-between gap-4">
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-foreground flex items-center gap-1.5">
          <Icon size={13} className="text-primary flex-shrink-0" />
          {title}
        </h3>
        {note && (
          <p className="mt-0.5 text-[11px] text-muted-foreground leading-snug max-w-xl">
            {note}
          </p>
        )}
      </div>
      {action && <div className="flex-shrink-0 pt-0.5">{action}</div>}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="border border-dashed border-border rounded-xl px-4 py-6 text-center">
      <p className="text-[11px] text-muted-foreground max-w-sm mx-auto leading-relaxed">
        {children}
      </p>
    </div>
  );
}

/** Skeleton rather than a centred spinner: the page keeps its shape while it
 *  loads, so nothing jumps when the numbers land. */
function Skeleton() {
  return (
    <div className="space-y-8" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading analytics</span>
      <div className="h-[92px] rounded-xl border border-border bg-card animate-pulse" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="h-[150px] rounded-xl border border-border bg-card animate-pulse" />
        <div className="h-[150px] rounded-xl border border-border bg-card animate-pulse" />
      </div>
      <div className="h-[190px] rounded-xl border border-border bg-card animate-pulse" />
      <div className="flex items-center justify-center gap-2 text-[11px] text-muted-foreground">
        <AppIcon name="Loader2" className="animate-spin" size={12} />
        Crunching your mailbox…
      </div>
    </div>
  );
}
