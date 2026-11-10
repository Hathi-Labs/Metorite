"use client";

// The customer roster's table (WS-31).
//
// 🔴 **Split out of `page.tsx` to make it searchable.** The page is a server
// component — it must be, because the read uses the caller's own session — and
// a server component cannot hold a text box's state. So the read stays there
// and the LOOKING happens here.
//
// ⚠️ Every judgement is imported from `@/lib/roster`, never written inline.
// This app's suite carries no React renderer, so logic in JSX is untested by
// construction; `roster.test.ts` is the fence for all of it.

import { useMemo, useState } from "react";

import Explain from "./Explain";
import { categoricalBox, providerGlyph } from "@/lib/categorical";
import { formatPaise, seatsTotals, statusHelp, type OrgRow } from "@/lib/format";
import { daysLeftLabel, formatCr, formatInr, type CustomerMoney } from "@/lib/money";
import { attentionFlags, filterRoster, sortRoster, type RosterFilter } from "@/lib/roster";
import { chipClass, lifecycleTone } from "@/lib/tone";
import { runwayTone } from "@/lib/usage";

/** A subscription state worth a word under the status chip. */
const SUBSCRIPTION_WORDS: Record<string, string> = {
  past_due: "payment overdue",
  unpaid: "unpaid",
  canceled: "subscription cancelled",
  incomplete: "payment not finished",
};

function SeatsCell({ org }: { org: OrgRow }) {
  const totals = seatsTotals(org.seats);
  if (!totals) return <span className="muted">—</span>;
  const pct =
    totals.purchased > 0
      ? Math.min(100, Math.round((totals.assigned / totals.purchased) * 100))
      : 0;
  return (
    <div className="seatcell">
      <div>
        {totals.assigned} of {totals.purchased} used
        {totals.oversubscribed && (
          <span className="warnbadge" title="More seats assigned than purchased">
            over
          </span>
        )}
      </div>
      <div className="bar" aria-hidden="true">
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

const FILTERS: { key: RosterFilter; label: string }[] = [
  { key: "attention", label: "Needs attention" },
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "trial", label: "Trial" },
  { key: "suspended", label: "Suspended" },
];

export default function CustomerTable({
  rows,
  money,
}: {
  rows: OrgRow[];
  /** WS-50: each customer's money for the last 30 days, by slug. A missing or
   *  null entry means the usage read did not include them: unknown, not zero. */
  money: Record<string, CustomerMoney | null>;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<RosterFilter>("all");

  // One `now` for the whole render. Recomputing it per row would let two rows
  // in the same table disagree about what "ends today" means.
  const now = useMemo(() => new Date(), []);

  const counts = useMemo(
    () =>
      Object.fromEntries(
        FILTERS.map((f) => [f.key, filterRoster(rows, "", f.key, now).length]),
      ) as Record<RosterFilter, number>,
    [rows, now],
  );

  const shown = useMemo(
    () => sortRoster(filterRoster(rows, query, filter, now), now),
    [rows, query, filter, now],
  );

  return (
    <>
      <div className="toolbar">
        <input
          type="search"
          className="search"
          placeholder="Search by name or slug…"
          aria-label="Search customers"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="segmented" role="group" aria-label="Filter customers">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
              <span className="count">{counts[f.key]}</span>
            </button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <p className="muted" style={{ marginTop: 16 }}>
          No customer matches {query ? `“${query}”` : "this filter"}.
        </p>
      ) : (
        <div className="tablewrap">
          {/* ⚠️ A wide table must scroll INSIDE its own box. Without this the
            table widens the document and the whole page scrolls
            sideways, which moves the nav and every other panel with
            it. Measured at 390px on 2026-09-20. */}
          <table>
            <thead>
              <tr>
                <th>Customer</th>
                <th>Status</th>
                <th>
                  Seats a month
                  <Explain term="seats" />
                </th>
                <th>
                  We charged, 30 d
                  <Explain term="weCharged" />
                </th>
                <th>
                  AI cost, 30 d
                  <Explain term="aiCost" />
                </th>
                <th>
                  Profit, 30 d
                  <Explain term="profit" />
                </th>
                <th>
                  Credits left
                  <Explain term="creditsLeft" />
                </th>
                <th>Seats used</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((o) => {
                const flags = attentionFlags(o, now);
                const m = money[o.slug] ?? null;
                const loss = m?.profit.value != null && m.profit.value < 0;
                const sub = o.subscription_status ? SUBSCRIPTION_WORDS[o.subscription_status] : undefined;
                return (
                  <tr key={o.slug}>
                    <td>
                      <div className="orgcell">
                        <span
                          className={categoricalBox(o.name)}
                          aria-hidden="true"
                        >
                          {providerGlyph(o.name)}
                        </span>
                        <div>
                          <a href={`/customers/${encodeURIComponent(o.slug)}`}>
                            {o.name}
                          </a>
                          <div className="muted small">{o.slug}</div>
                          {flags.length > 0 && (
                            <div className="cell-flags" style={{ marginTop: 5 }}>
                              {flags.map((f) => (
                                <span key={f.kind} className={chipClass(f.tone)}>
                                  {f.label}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td>
                      <span
                        className={chipClass(lifecycleTone(o.status))}
                        title={statusHelp(o.status)}
                      >
                        {o.status.replace("_", " ")}
                      </span>
                      {sub && <div className="warn-t small">{sub}</div>}
                    </td>
                    <td className="mono">{formatPaise(o.mrr_paise)}</td>
                    {m ? (
                      <>
                        <td className="mono">
                          {formatInr(m.charged.value)}
                          <Explain term="weCharged" detail={m.charged.how} notes={m.estimateNotes} />
                        </td>
                        <td className="mono">
                          {formatInr(m.aiCost.value)}
                          <Explain term="aiCost" detail={m.aiCost.how} />
                        </td>
                        <td className="mono">
                          {loss ? (
                            <span className={chipClass("danger")}>{formatInr(m.profit.value)}</span>
                          ) : (
                            formatInr(m.profit.value)
                          )}
                          {m.profit.estimated && <span className="est">est.</span>}
                        </td>
                        <td>
                          <span className="mono">{formatCr(m.creditsLeft)}</span>
                          <div className="small">
                            <span className={chipClass(runwayTone(m.daysLeft.days))}>
                              {daysLeftLabel(m.daysLeft)}
                            </span>
                            <Explain term="daysLeft" detail={m.daysLeft.how} />
                          </div>
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="muted" colSpan={3}>
                          not in the usage read
                        </td>
                        <td className="mono">{formatCr(Number(o.credit_balance) || 0)}</td>
                      </>
                    )}
                    <td>
                      <SeatsCell org={o} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
