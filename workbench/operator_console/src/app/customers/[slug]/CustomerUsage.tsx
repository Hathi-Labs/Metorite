// Money for one customer: what we charged, what their AI cost us, what is
// left. WS-50 slices 1 and 2, decision D94. It replaces H-133's "What they
// spent" panel.
//
// 🔴 **The answer comes first.** The owner opens a customer to learn two
// things: what we charge them, and what they cost us. The old panel showed
// credits beside dollars beside a "2.5× cost" ratio, and a reader had to know
// three units to compare them. Every figure here is in rupees, and each one
// has an ⓘ that shows its formula with this customer's own numbers.
//
// 🔴 **Every number comes from `lib/money.ts`, every judgement from
// `lib/usage.ts`.** This file only arranges them. The fleet page and this
// panel must never disagree about one customer.
//
// ⚠️ **A server component.** `Explain` is the only client part, and it
// receives finished strings.

import Spark from "../../Spark";
import Explain from "../../Explain";
import {
  daysLeftLabel,
  formatCr,
  formatInr,
  formatPct,
  formatUsdPlain,
  type CustomerMoney,
  type Price,
} from "@/lib/money";
import { chipClass } from "@/lib/tone";
import {
  customerUsageState,
  hasUnbilled,
  orgFlags,
  runwayTone,
  type OrgUsageRow,
  type UsageDay,
} from "@/lib/usage";

function Est({ on }: { on: boolean }) {
  return on ? <span className="est">estimated</span> : null;
}

export default function CustomerUsage({
  row,
  days,
  windowDays,
  error,
  money,
  price,
}: {
  /** This organization's row from the fleet read. ⚠️ `null` can mean EITHER
   *  "no traffic" or "the capped fleet page did not include them" — H-76.
   *  `customerUsageState` tells the two apart; this file never guesses. */
  row: OrgUsageRow | null;
  days: UsageDay[];
  windowDays: number;
  /** The Console refused or did not answer. Say so — an empty panel and a
   *  failed read must not look alike. */
  error: string | null;
  /** `lib/money.ts`'s figures for this row, or null when there is no row. */
  money: CustomerMoney | null;
  price: Price | null;
}) {
  const state = customerUsageState(row, days);

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Money — last {windowDays} days</h2>
        <p>
          What we charged this customer, what their AI cost us, and what is
          left. All figures are in rupees. Select ⓘ beside a figure to see how
          it is worked out.
        </p>
      </div>

      {error && <p className="result err">{error}</p>}

      {!error && !price && (
        <div className="banner">
          <strong>No credit price is saved,</strong> so credits and the
          vendor&apos;s dollar bill cannot be shown in rupees.{" "}
          <a href="/pricing">Set the credit price →</a>
        </div>
      )}

      {!error && state.kind === "quiet" && (
        <div className="empty">
          <h3>No AI use in the last {windowDays} days</h3>
          <p className="muted">
            This customer made no AI calls in this period, so there is no AI
            cost and no credit use to show. Seat revenue, if any, is on the
            Subscription figure above.
          </p>
        </div>
      )}

      {/* H-76: the fleet read is a capped page sorted by spend, so a quiet
          customer with real traffic can be missing from it. Say that, and
          never print "no calls" over a truncation. */}
      {!error && state.kind === "truncated" && (
        <div className="banner">
          <strong>This customer made AI calls, but the money figures did not
          load.</strong> The fleet read lists the biggest spenders first and
          stops at a page limit, and this customer is below it. The daily
          chart below is complete. <a href="/usage">Open the fleet view →</a>
        </div>
      )}

      {!error && state.kind === "truncated" && days.length > 0 && (
        <Spark days={days} label="Credits per day" />
      )}

      {!error && state.kind === "full" && money && (
        <>
          {orgFlags(state.row).length > 0 && (
            <p className="rowline">
              {orgFlags(state.row).map((f) => (
                <span key={f.label} className={chipClass(f.tone)}>
                  {f.label}
                </span>
              ))}
            </p>
          )}

          <div className="stats">
            <div className="stat">
              <div className="lbl">
                We charged
                <Explain term="weCharged" detail={money.charged.how} notes={money.estimateNotes} />
                <Est on={money.charged.estimated} />
              </div>
              <div className="num">{formatInr(money.charged.value)}</div>
              <div className="sub">
                Seats {formatInr(money.seats.value)} · Credits{" "}
                {formatInr(money.paidCredits.value)}
                <Explain term="paidCredits" detail={money.paidCredits.how} />
              </div>
            </div>

            <div className="stat">
              <div className="lbl">
                AI cost
                <Explain term="aiCost" detail={money.aiCost.how} />
              </div>
              <div className="num">{formatInr(money.aiCost.value)}</div>
              <div className="sub">
                {formatUsdPlain(Number(state.row.costUsd) || 0)} billed by vendors
                {money.givenAway.value !== null && money.givenAway.value > 0 && (
                  <>
                    {" · "}given away {formatInr(money.givenAway.value)}
                    <Explain term="givenAway" detail={money.givenAway.how} />
                  </>
                )}
              </div>
            </div>

            <div
              className={`stat${
                money.profit.value !== null && money.profit.value < 0 ? " loss" : ""
              }`}
            >
              <div className="lbl">
                Profit
                <Explain term="profit" detail={money.profit.how} notes={money.estimateNotes} />
                <Est on={money.profit.estimated} />
              </div>
              <div className="num">{formatInr(money.profit.value)}</div>
              <div className="sub">
                Margin {formatPct(money.margin.value)}
                <Explain term="margin" detail={money.margin.how} />
              </div>
            </div>

            <div className={`stat${money.creditsLeft < 0 ? " loss" : ""}`}>
              <div className="lbl">
                Credits left
                <Explain term="creditsLeft" />
              </div>
              <div className="num">{formatCr(money.creditsLeft)}</div>
              <div className="sub">
                {money.owed.value !== null && money.owed.value > 0 ? (
                  <>
                    Owed {formatInr(money.owed.value)}
                    <Explain term="owed" detail={money.owed.how} />
                  </>
                ) : price ? (
                  <>worth {formatInr(money.creditsLeft * price.inrPerCredit)}</>
                ) : (
                  "credits"
                )}
              </div>
            </div>

            <div className="stat">
              <div className="lbl">
                Days left
                <Explain term="daysLeft" detail={money.daysLeft.how} />
              </div>
              <div className="num">
                <span className={chipClass(runwayTone(money.daysLeft.days))}>
                  {daysLeftLabel(money.daysLeft)}
                </span>
              </div>
              <div className="sub">
                {money.daysLeft.perDay !== null
                  ? `using ${formatCr(money.daysLeft.perDay)} credits a day`
                  : "at the last 7 days' rate"}
              </div>
            </div>
          </div>

          {money.estimateNotes.length > 0 && (
            <ul className="moneynotes">
              {money.estimateNotes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}

          <dl className="modelfacts">
            <div>
              <dt>
                AI calls
                <Explain term="calls" />
              </dt>
              <dd>{state.row.calls.toLocaleString("en-IN")}</dd>
            </div>
            <div>
              <dt>
                Credits used
                <Explain term="creditsUsed" />
              </dt>
              <dd>{formatCr(money.creditsUsed)}</dd>
            </div>
            <div>
              <dt>
                Refused
                <Explain term="refused" />
              </dt>
              {/* A tone CHIP, not a coloured `dd`: `.modelfacts dd` sets the
                  colour and out-specifies a text tone (measured 2026-09-21). */}
              <dd>
                {state.row.refusals > 0 ? (
                  <span className={chipClass("warn")}>{state.row.refusals}</span>
                ) : (
                  state.row.refusals
                )}
              </dd>
            </div>
            <div>
              <dt>
                Served, not billed
                <Explain
                  term="servedNotBilled"
                  detail={
                    hasUnbilled(state.row)
                      ? `${state.row.unbilledCalls} answered calls, about ` +
                        `${state.row.unbilledTokens.toLocaleString("en-IN")} tokens, ` +
                        "that we paid the vendor for and did not charge."
                      : null
                  }
                />
              </dt>
              <dd>
                {hasUnbilled(state.row) ? (
                  <span className={chipClass("danger")}>{state.row.unbilledCalls}</span>
                ) : (
                  state.row.unbilledCalls
                )}
              </dd>
            </div>
          </dl>

          {/* No line for a series with no calls: a flat line through the
              middle reads as steady use. */}
          {days.some((d) => d.calls > 0) && <Spark days={days} label="Credits per day" />}
        </>
      )}
    </section>
  );
}
