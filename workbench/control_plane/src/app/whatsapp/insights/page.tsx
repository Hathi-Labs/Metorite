"use client";

// WhatsApp Pulse — the founder's "am I keeping up?" view (W7). Read-only insight
// over the classified store: reply speed, who's waited longest, inbound load by
// intent, and the busiest chats. Calm and honest — no vanity unread counts.

import Icon from "@/components/Icon";
import { useCallback, useEffect, useState } from "react";
import { fetchAccounts, fetchPulse, pickDefaultAccount } from "../lib/api";
import type { WaPulse } from "../lib/types";

const EMPTY: WaPulse = {
  window_days: 7,
  inbound: 0,
  outbound: 0,
  active_chats: 0,
  response: { replied: 0, median_minutes: null, p90_minutes: null },
  waiting_longest: [],
  by_intent: [],
  busiest: [],
};

function fmtMinutes(min: number | null): string {
  if (min === null || Number.isNaN(min)) return "—";
  if (min < 60) return `${Math.round(min)}m`;
  const hrs = min / 60;
  if (hrs < 24) {
    const h = Math.floor(hrs);
    const m = Math.round(min - h * 60);
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  const days = Math.floor(hrs / 24);
  const remH = Math.round(hrs - days * 24);
  return remH ? `${days}d ${remH}h` : `${days}d`;
}

function fmtWaited(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

export default function InsightsPage() {
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [pulse, setPulse] = useState<WaPulse>(EMPTY);

  useEffect(() => {
    (async () => {
      const def = pickDefaultAccount(await fetchAccounts());
      if (def) setAccountId(def.id);
      setLoading(false);
    })();
  }, []);

  const load = useCallback(async () => {
    if (!accountId) return;
    setPulse(await fetchPulse(accountId, days));
  }, [accountId, days]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Icon name="Loader2" className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  const maxIntent = Math.max(1, ...pulse.by_intent.map((i) => i.count));

  return (
    <div className="mx-auto h-full max-w-3xl overflow-y-auto p-4 text-foreground md:p-6">
      <div className="mb-5 flex items-center gap-3">
        <Icon name="Activity" className="h-4 w-4 text-primary" />
        <h1 className="text-[15px] font-semibold">Pulse</h1>
        <div className="ml-auto flex items-center gap-1 rounded-lg border border-border p-0.5 text-[11px]">
          {[7, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-md px-2 py-1 ${
                days === d
                  ? "bg-muted font-semibold text-foreground"
                  : "text-muted-foreground"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* headline tiles */}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label="Typical reply" value={fmtMinutes(pulse.response.median_minutes)}>
          <span className="text-[10px] text-muted-foreground">
            p90 {fmtMinutes(pulse.response.p90_minutes)}
          </span>
        </Tile>
        <Tile label="Replied" value={String(pulse.response.replied)} />
        <Tile label="They sent" value={String(pulse.inbound)} />
        <Tile label="You sent" value={String(pulse.outbound)} />
      </div>

      {/* waiting longest */}
      <Section title="Waited longest on you">
        {pulse.waiting_longest.length === 0 ? (
          <Empty>Nobody is waiting — inbox zero on WhatsApp. 🙏</Empty>
        ) : (
          <ul className="divide-y divide-border">
            {pulse.waiting_longest.map((w) => (
              <li key={w.chat_id} className="flex items-center gap-3 px-3 py-2.5">
                <Icon name="Clock" className="h-3.5 w-3.5 shrink-0 text-warning" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-semibold">
                    {w.name}
                  </div>
                  <div className="truncate text-[11px] text-muted-foreground">
                    {w.snippet || "…"}
                  </div>
                </div>
                <span className="shrink-0 text-[11px] font-semibold tabular-nums text-warning">
                  {fmtWaited(w.waited_hours)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <div className="grid gap-4 sm:grid-cols-2">
        {/* inbound by intent */}
        <Section title="Inbound by intent">
          {pulse.by_intent.length === 0 ? (
            <Empty>No classified inbound in this window.</Empty>
          ) : (
            <div className="space-y-2 p-3">
              {pulse.by_intent.map((i) => (
                <div key={i.key} className="text-[11.5px]">
                  <div className="mb-0.5 flex justify-between">
                    <span className="truncate">{i.key}</span>
                    <span className="tabular-nums text-muted-foreground">
                      {i.count}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${(i.count / maxIntent) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Section>

        {/* busiest chats */}
        <Section title="Busiest chats">
          {pulse.busiest.length === 0 ? (
            <Empty>No activity in this window.</Empty>
          ) : (
            <ul className="divide-y divide-border">
              {pulse.busiest.map((b) => (
                <li
                  key={b.chat_id}
                  className="flex items-center justify-between px-3 py-2 text-[12px]"
                >
                  <span className="truncate">{b.name}</span>
                  <span className="tabular-nums text-muted-foreground">
                    {b.count}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      <p className="mt-5 text-[11px] text-muted-foreground/70">
        “Typical reply” is the median time from a customer message to your next
        reply, over the last {pulse.window_days} days. Snoozed chats are excluded
        from “waited longest”.
      </p>
    </div>
  );
}

function Tile({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground/70">
        {label}
      </div>
      <div className="mt-1 text-[20px] font-bold tabular-nums">{value}</div>
      {children}
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-4">
      <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground/70">
        {title}
      </div>
      <div className="overflow-hidden rounded-lg border border-border">
        {children}
      </div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="p-5 text-center text-[12px] text-muted-foreground">
      {children}
    </div>
  );
}
