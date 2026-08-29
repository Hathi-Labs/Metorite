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

import { formatDate, formatPaise, seatsTotals, statusHelp, trialHint, type OrgRow } from "@/lib/format";
import { attentionFlags, filterRoster, sortRoster, type RosterFilter } from "@/lib/roster";
import { chipClass, lifecycleTone } from "@/lib/tone";

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

export default function CustomerTable({ rows }: { rows: OrgRow[] }) {
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
        <table>
          <thead>
            <tr>
              <th>Customer</th>
              <th>Status</th>
              <th>Subscription</th>
              <th>MRR</th>
              <th>Seats</th>
              <th>AI credits</th>
              <th>Trial</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((o) => {
              const flags = attentionFlags(o, now);
              return (
                <tr key={o.slug}>
                  <td>
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
                  </td>
                  <td>
                    <span
                      className={chipClass(lifecycleTone(o.status))}
                      title={statusHelp(o.status)}
                    >
                      {o.status.replace("_", " ")}
                    </span>
                  </td>
                  <td>
                    {o.subscription_status ?? <span className="muted">none</span>}
                  </td>
                  <td>{formatPaise(o.mrr_paise)}</td>
                  <td>
                    <SeatsCell org={o} />
                  </td>
                  <td>{o.credit_balance}</td>
                  <td>
                    {formatDate(o.trial_ends_at)}
                    {o.status === "trial" && trialHint(o.trial_ends_at, now) && (
                      <div className="muted small">
                        {trialHint(o.trial_ends_at, now)}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
