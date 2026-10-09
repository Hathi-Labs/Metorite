"use client";

// Money across every customer — WS-50 slice 4, decision D94. It replaces the
// "AI usage" board.
//
// 🔴 **The owner's question, for the whole business.** What did every
// customer pay us, what did their AI cost us, and which ones lose money? The
// old board showed credits, dollars and a "× cost" ratio side by side, and
// none of them was money we keep. Every figure here is rupees, from
// `lib/fleet.ts`, and each one has an ⓘ.
//
// ⚠️ A client component only for the sort and the purged toggle. Every
// figure arrives finished from the server page.

import { useMemo, useState } from "react";

import Explain from "../Explain";
import Spark from "../Spark";
import { type Fleet, type FleetRow, type FleetSort, sortFleet, totalsHow } from "@/lib/fleet";
import { formatDate } from "@/lib/format";
import { aiMarginOf, daysLeftLabel, formatCr, formatInr, formatPct } from "@/lib/money";
import { chipClass, lifecycleTone } from "@/lib/tone";
import {
  type OrgUsageRow,
  type UsageDay,
  orgFlags,
  runwayTone,
} from "@/lib/usage";

function Est({ on }: { on: boolean }) {
  return on ? <span className="est">estimated</span> : null;
}

const SORTS: { key: FleetSort; label: string }[] = [
  { key: "charged", label: "Biggest first" },
  { key: "profit", label: "Least profit first" },
];

export default function MoneyBoard({
  fleet,
  purgedRows,
  usageRows,
  days,
  spikes,
  seriesError,
  silentSlugs,
  unbilled,
}: {
  /** The live customers. Every total comes from these rows only. */
  fleet: Fleet;
  /** Deleted customers, shown on request and never in a total. */
  purgedRows: FleetRow[];
  /** Days whose AI spend was over 5 times the days before. */
  spikes: string[];
  seriesError: string | null;
  /** The usage rows, for the activity flags (silent, walled, below zero). */
  usageRows: OrgUsageRow[];
  days: UsageDay[];
  silentSlugs: string[];
  unbilled: { orgs: number; calls: number; tokens: number };
}) {
  const [sort, setSort] = useState<FleetSort>("charged");
  const [showPurged, setShowPurged] = useState(false);
  const { totals, price } = fleet;

  const purgedCount = purgedRows.length;
  const shown = useMemo(
    () => sortFleet(showPurged ? [...fleet.rows, ...purgedRows] : fleet.rows, sort),
    [fleet.rows, purgedRows, showPurged, sort],
  );
  const usageBySlug = useMemo(
    () => new Map(usageRows.map((r) => [r.slug, r])),
    [usageRows],
  );
  const silentNames = silentSlugs
    .filter((s) => !purgedRows.some((r) => r.org.slug === s))
    .map((s) => fleet.rows.find((r) => r.org.slug === s)?.org.name ?? s);
  const how = totalsHow(totals, fleet.rows.length);

  return (
    <>
      {!price && fleet.priceReported && (
        <div className="banner">
          <strong>No credit price is saved,</strong> so credits and the
          vendors&apos; dollar bills cannot be shown in rupees.{" "}
          <a href="/pricing">Set the credit price →</a>
        </div>
      )}
      {!price && !fleet.priceReported && (
        <div className="banner">
          <strong>This Console build does not send the credit price yet,</strong>{" "}
          so the figures below cannot be shown in rupees. They appear after the
          next Console deploy.
        </div>
      )}

      {/* Revenue we have already lost: the customer has the answer, we paid
          the vendor, and we charged nothing. Louder than the silent banner. */}
      {unbilled.calls > 0 && (
        <div className="banner danger">
          <strong>
            {unbilled.calls} {unbilled.calls === 1 ? "AI call was" : "AI calls were"}{" "}
            answered and not charged
          </strong>{" "}
          across {unbilled.orgs} {unbilled.orgs === 1 ? "customer" : "customers"}
          {unbilled.tokens > 0 && <> (about {unbilled.tokens.toLocaleString("en-IN")} tokens)</>}.
          The meter could not read the vendor&apos;s reply, so we paid the
          vendor and charged nothing.
        </div>
      )}

      {spikes.length > 0 && (
        <div className="banner">
          <strong>Unusual AI spend</strong> on {spikes.map((d) => formatDate(d)).join(", ")}: a
          day that cost more than 5 times the days before it. Open the customers
          below to find which one.
        </div>
      )}

      {silentNames.length > 0 && (
        <div className="banner">
          <strong>
            {silentNames.length} {silentNames.length === 1 ? "customer holds" : "customers hold"}{" "}
            credits and made no AI call in two weeks:
          </strong>{" "}
          {silentNames.slice(0, 8).join(", ")}
          {silentNames.length > 8 ? "…" : ""}. Ask why before they leave.
        </div>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>The whole business — last {fleet.windowDays} days</h2>
          <p>
            What every customer paid us, what their AI cost us, and what is
            left. All in rupees. Select ⓘ beside a figure to see how it is
            worked out.
          </p>
        </div>

        <div className="stats">
          <div className="stat">
            <div className="lbl">
              We charged
              <Explain term="weCharged" detail={how} scope="Across customers" />
              <Est on={totals.estimated} />
            </div>
            <div className="num">{formatInr(totals.charged)}</div>
          </div>
          <div className="stat">
            <div className="lbl">
              AI cost
              <Explain term="aiCost" detail={how} scope="Across customers" />
            </div>
            <div className="num">{formatInr(totals.aiCost)}</div>
            {totals.givenAway !== null && totals.givenAway > 0 && (
              <div className="sub">
                given away {formatInr(totals.givenAway)}
                <Explain term="givenAway" />
              </div>
            )}
          </div>
          <div className={`stat${totals.profit !== null && totals.profit < 0 ? " loss" : ""}`}>
            <div className="lbl">
              Profit
              <Explain term="profit" detail={how} scope="Across customers" />
              <Est on={totals.estimated} />
            </div>
            <div className="num">{formatInr(totals.profit)}</div>
            <div className="sub">
              Margin {formatPct(totals.margin)}
              <Explain term="margin" />
              {" · "}AI margin {formatPct(totals.aiMargin)}
              <Explain term="aiMargin" />
            </div>
          </div>
          <div className={`stat${totals.losing > 0 ? " loss" : ""}`}>
            <div className="lbl">Losing money</div>
            <div className="num">{totals.losing}</div>
            <div className="sub">
              {totals.losing === 1 ? "customer" : "customers"} with profit below zero
            </div>
          </div>
          {totals.owed > 0 && (
            <div className="stat loss">
              <div className="lbl">
                Owed
                <Explain term="owed" />
              </div>
              <div className="num">{formatInr(totals.owed)}</div>
              <div className="sub">credits spent below zero</div>
            </div>
          )}
        </div>

        {totals.missing > 0 && (
          <p className="field-hint warn">
            {totals.missing} {totals.missing === 1 ? "customer is" : "customers are"} not in
            the usage read (it lists the biggest spenders first and stops at a page
            limit), so the totals leave them out.
          </p>
        )}

        {seriesError && <p className="field-hint warn">{seriesError}</p>}
        {days.some((d) => d.calls > 0) && <Spark days={days} label="Credits per day, all customers" />}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>By customer</h2>
          <p>
            Each customer&apos;s money for the same {fleet.windowDays} days. Open a
            customer for the split by app and by person.
          </p>
        </div>

        <div className="toolbar">
          <div className="segmented" role="group" aria-label="Sort customers">
            {SORTS.map((s) => (
              <button
                key={s.key}
                type="button"
                aria-pressed={sort === s.key}
                onClick={() => setSort(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <div className="tablewrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Customer</th>
                <th>
                  We charged
                  <Explain term="weCharged" />
                </th>
                <th>
                  AI cost
                  <Explain term="aiCost" />
                </th>
                <th>
                  Profit
                  <Explain term="profit" />
                </th>
                <th>
                  Margin
                  <Explain term="margin" />
                </th>
                <th>
                  AI margin
                  <Explain term="aiMargin" />
                </th>
                <th>
                  Credits left
                  <Explain term="creditsLeft" />
                </th>
                <th>
                  Days left
                  <Explain term="daysLeft" />
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map(({ org, money }) => {
                const usage = usageBySlug.get(org.slug);
                const flags = usage ? orgFlags(usage, money?.profit.value ?? null) : [];
                const loss = money?.profit.value !== null && (money?.profit.value ?? 0) < 0;
                return (
                  <tr key={org.slug}>
                    <td>
                      <a href={`/customers/${encodeURIComponent(org.slug)}`}>{org.name}</a>
                      <div className="rowline" style={{ marginTop: 4 }}>
                        <span className={chipClass(lifecycleTone(org.status))}>
                          {org.status.replace("_", " ")}
                        </span>
                        {flags.map((f) => (
                          <span key={f.label} className={chipClass(f.tone)}>
                            {f.label}
                          </span>
                        ))}
                      </div>
                    </td>
                    {money ? (
                      <>
                        <td className="mono">
                          {formatInr(money.charged.value)}
                          <Explain term="weCharged" detail={money.charged.how} notes={money.estimateNotes} />
                        </td>
                        <td className="mono">
                          {formatInr(money.aiCost.value)}
                          <Explain term="aiCost" detail={money.aiCost.how} />
                        </td>
                        <td className="mono">
                          {loss ? (
                            <span className={chipClass("danger")}>{formatInr(money.profit.value)}</span>
                          ) : (
                            formatInr(money.profit.value)
                          )}
                          {money.profit.estimated && <span className="est">est.</span>}
                        </td>
                        <td className="mono">{formatPct(money.margin.value)}</td>
                        <td className="mono">{formatPct(aiMarginOf(money))}</td>
                        <td className="mono">{formatCr(money.creditsLeft)}</td>
                        <td>
                          <span className={chipClass(runwayTone(money.daysLeft.days))}>
                            {daysLeftLabel(money.daysLeft)}
                          </span>
                          <Explain term="daysLeft" detail={money.daysLeft.how} />
                        </td>
                      </>
                    ) : (
                      <td colSpan={7} className="muted">
                        Not in the usage read, so its money is unknown here. Open the
                        customer to see it.
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {purgedCount > 0 && (
          <p className="note">
            <button type="button" className="linklike" onClick={() => setShowPurged((v) => !v)}>
              {showPurged ? "Hide" : "Show"} {purgedCount} deleted{" "}
              {purgedCount === 1 ? "customer" : "customers"}
            </button>
          </p>
        )}
      </section>
    </>
  );
}
