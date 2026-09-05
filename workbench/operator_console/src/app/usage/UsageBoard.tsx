"use client";

// The AI usage board — WS-31 §5, and §6's A1/A2/A3/A6.
//
// ⚠️ Every judgement is imported from `@/lib/usage`, never written inline. This
// app's suite carries no React renderer, so logic in JSX is untested by
// construction and `usage.test.ts` is the fence for all of it.
//
// ⚠️ **Credits and costs render as the STRINGS the Console sent.** They are
// money, the ledger stores NUMERIC(14,4), and re-formatting a parsed float is
// how a total stops matching the sum of its rows. `Number()` appears here only
// to compare and to draw.

import { useMemo, useState } from "react";

import { chipClass } from "@/lib/tone";
import {
  type OrgUsageRow,
  type UsageDay,
  marginLabel,
  marginTone,
  orgFlags,
  runwayLabel,
  runwayTone,
  sparklinePath,
  unbilledTotals,
  usageHeadline,
} from "@/lib/usage";

const TOMBSTONE = /-purged-[0-9a-f]{6}$/;

function Spark({ days }: { days: UsageDay[] }) {
  const d = useMemo(
    () => sparklinePath(days.map((x) => Number(x.credits) || 0), 560, 44),
    [days],
  );
  if (!d) return null;
  return (
    <svg className="spark" viewBox="0 0 560 44" preserveAspectRatio="none"
      role="img" aria-label="Credits per day">
      <path d={d} />
    </svg>
  );
}

export default function UsageBoard({
  rows, days, spikes, total, silentSlugs = [],
  unbilledOrgs, unbilledCallsTotal, unbilledTokensTotal,
}: {
  rows: OrgUsageRow[];
  days: UsageDay[];
  spikes: string[];
  /** How many organizations EXIST. Differs from `rows.length` once the page
   *  is capped, and the ones missing are the quiet ones. */
  total: number;
  /** 🔴 Silent customers judged over EVERY organization (H-76), so the ones
   *  the spend-sorted cap pushed off the page still reach a reader. */
  silentSlugs?: string[];
  /** 🔴 Served-and-unbilled judged over EVERY organization, for the same
   *  reason `silentSlugs` is — except sharper here, because a leak bills zero
   *  and a zero-credit row ALWAYS sorts off a spend-ordered page. Optional:
   *  this app and the Console deploy apart (R6), and an older Console sends
   *  none of the three. */
  unbilledOrgs?: number;
  unbilledCallsTotal?: number;
  unbilledTokensTotal?: number;
}) {
  // ⚠️ Purged organizations arrive from the Console on purpose — their usage
  // rows survive the purge as billing history. The roster already owns the
  // rule that they are not customers, so this hides them by default and says
  // how many rather than dropping them silently.
  const [showPurged, setShowPurged] = useState(false);
  const purged = rows.filter((r) => TOMBSTONE.test(r.slug));
  const live = rows.filter((r) => !TOMBSTONE.test(r.slug));
  const shown = showPurged ? rows : live;

  // The silent customers the CAP hid: judged over every organization on
  // the server, minus the ones already visible as rows (their own silent
  // chip covers those).
  const hiddenSilent = silentSlugs.filter(
    (slug) => !rows.some((r) => r.slug === slug) && !TOMBSTONE.test(slug),
  );

  const totals = useMemo(() => {
    const calls = live.reduce((n, r) => n + r.calls, 0);
    const credits = live.reduce((n, r) => n + (Number(r.credits) || 0), 0);
    const cost = live.reduce((n, r) => n + (Number(r.costUsd) || 0), 0);
    return { calls, credits, cost };
  }, [live]);

  // 🔴 **The SERVER's uncapped figure, never the page's sum** (022, 025).
  //
  // ⚠️ A total computed from `rows` would read zero exactly when it mattered.
  // An unbilled call bills zero by definition, the page sorts by credits
  // descending, so a leaking organization sorts LAST and falls off the cap —
  // the worse the leak, the more certainly it hides. Measured 2026-09-05: the
  // page summed to 0 while the fleet held 2 leaking organizations.
  //
  // `unbilledTotals(live)` stays for the rows the page DOES carry, so a
  // visible leak is attributable to a customer as well as counted.
  const onPage = useMemo(() => unbilledTotals(live), [live]);
  const unbilled = {
    orgs: unbilledOrgs ?? onPage.orgs,
    calls: unbilledCallsTotal ?? onPage.calls,
    tokens: unbilledTokensTotal ?? onPage.tokens,
  };

  return (
    <>
      {/* 🔴 **FIRST on the page, and deliberately louder than the silent
          banner below it.** This is revenue we have already lost: the customer
          holds their completion, we hold the vendor's bill, and nothing will
          reconcile the two later. The silent-customer banner is a prompt to
          make a call; this one is a defect that is still running. */}
      {unbilled.calls > 0 && (
        <div className="banner danger">
          <strong>
            {unbilled.calls}{" "}
            {unbilled.calls === 1 ? "call was" : "calls were"} served and NOT
            billed
          </strong>{" "}
          across {unbilled.orgs}{" "}
          {unbilled.orgs === 1 ? "customer" : "customers"}
          {unbilled.tokens > 0 && (
            <> — about {unbilled.tokens.toLocaleString("en-IN")} tokens</>
          )}
          . The customer received the AI. We paid the vendor. We charged
          nothing, because the meter could not read the usage and this console
          refuses to invoice a number it cannot defend.{" "}
          <strong>This is not built yet</strong> — billing an unmeterable call
          correctly needs the vendor-convention data these rows are collecting.
          Until then every one of them is absorbed cost.
        </div>
      )}

      {hiddenSilent.length > 0 && (
        <div className="banner">
          {hiddenSilent.length} funded{" "}
          {hiddenSilent.length === 1 ? "customer is" : "customers are"} silent
          and below this page&apos;s cap: {hiddenSilent.slice(0, 8).join(", ")}
          {hiddenSilent.length > 8 ? "…" : ""}. They hold credits and have not
          called in two weeks — ask why before they leave.
        </div>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>Across every customer</h2>
          <p>{usageHeadline(live)}</p>
        </div>

        <div className="stats">
          <div className="stat">
            <div className="num">{totals.calls}</div>
            <div className="lbl">Calls</div>
          </div>
          <div className="stat">
            {/* toFixed: a float SUM of "0.0300"-style strings carries binary
                noise, and 12.100000000000001 is not a money figure. */}
            <div className="num">{totals.credits.toFixed(2)}</div>
            <div className="lbl">Credits billed</div>
          </div>
          <div className="stat">
            <div className="num">${totals.cost.toFixed(2)}</div>
            <div className="lbl">Provider cost</div>
          </div>
          <div className={`stat ${spikes.length > 0 ? "caution" : ""}`}>
            <div className="num">{spikes.length}</div>
            <div className="lbl">Cost spikes</div>
          </div>
          {/* ⚠️ Always rendered, never hidden at zero. A tile that appears
              only on failure teaches nobody where to look, and its absence
              then reads as "not measured" rather than "none". */}
          <div className={`stat ${unbilled.calls > 0 ? "alarm" : ""}`}>
            <div className="num">{unbilled.calls}</div>
            <div className="lbl">Served, unbilled</div>
          </div>
        </div>

        <Spark days={days} />
        {spikes.length > 0 && (
          <p className="note">
            Unusual spend on {spikes.join(", ")} — each is more than five times
            the days before it.
          </p>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>By organization</h2>
          <p>
            Margin is <strong>credits billed per dollar of provider cost</strong>,
            not money. A credit has no price yet (H-42), so the two columns are
            different units and cannot be subtracted.
          </p>
        </div>

        {shown.length === 0 ? (
          <div className="empty">
            <h2>No organizations</h2>
            <p className="muted">
              The Console returned no rows. That is a configuration problem, not
              a quiet week.
            </p>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Organization</th>
                <th>Calls</th>
                {/* A5 (§8.1). Beside Calls on purpose — "0 calls, 41 refused"
                    is one sentence, and it is the sentence support needs. */}
                <th>Refused</th>
                {/* Deliberately NOT "Unbilled" alone. "Unbilled" reads as an
                    invoice that has not gone out yet, which is recoverable.
                    This is not — it is service already given away. */}
                <th title="Served and not charged — absorbed cost">
                  Given away
                </th>
                <th>Credits</th>
                <th>Our cost</th>
                <th>Margin</th>
                <th>Balance</th>
                <th>Runway</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const flags = orgFlags(r);
                return (
                  <tr key={r.slug}>
                    <td>
                      <a href={`/customers/${encodeURIComponent(r.slug)}`}>
                        {r.name}
                      </a>
                      <div className="muted small">
                        {r.slug} · {r.members} member{r.members === 1 ? "" : "s"}
                      </div>
                      {flags.length > 0 && (
                        <div className="cell-flags" style={{ marginTop: 5 }}>
                          {flags.map((f) => (
                            <span key={f.label} className={chipClass(f.tone)}>
                              {f.label}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td>{r.calls}</td>
                    <td className={r.refusals > 0 ? "caution" : "muted"}>
                      {r.refusals}
                    </td>
                    {/* 🔴 Money we did not collect, beside the count of times
                        we said no. Reading the two the same way is the trap:
                        a refusal cost us nothing, this cost us the vendor's
                        bill. */}
                    <td
                      className={r.unbilledCalls > 0 ? "danger-t" : "muted"}
                      title={
                        r.unbilledCalls > 0
                          ? `${r.unbilledCalls} served call(s) we could not meter, about ${r.unbilledTokens.toLocaleString("en-IN")} tokens. Absorbed, not charged.`
                          : undefined
                      }
                    >
                      {r.unbilledCalls}
                    </td>
                    <td>{r.credits}</td>
                    <td>${r.costUsd}</td>
                    <td>
                      <span className={chipClass(marginTone(r.marginRatio))}>
                        {marginLabel(r.marginRatio)}
                      </span>
                    </td>
                    <td>{r.balance}</td>
                    <td>
                      <span className={chipClass(runwayTone(r.runwayDays))}>
                        {runwayLabel(r.runwayDays)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {total > rows.length && (
          <p className="note">
            Showing {rows.length} of {total} organizations, ordered by credits
            billed. The rest spent least — which means a customer who bought
            credits and used none is at the end, not on this page.
          </p>
        )}

        {purged.length > 0 && (
          <p className="note">
            <button
              type="button"
              className="linklike"
              onClick={() => setShowPurged((v) => !v)}
            >
              {showPurged ? "Hide" : "Show"} {purged.length} purged
            </button>{" "}
            — data destroyed, usage kept for billing history.
          </p>
        )}
      </section>
    </>
  );
}
