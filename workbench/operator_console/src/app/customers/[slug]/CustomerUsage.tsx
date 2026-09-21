// What this customer SPENT — H-133.
//
// 🔴 **The page where the question gets asked, and it could not answer.** A
// customer writes in saying their credits went faster than they expected. The
// operator opens that customer and sees the balance, the lots and the ledger.
// The ledger says `usage -1.29` eight hundred times. It cannot say which tier,
// which app or which person, so the operator cannot answer and the exchange
// becomes a support thread.
//
// ⚠️ **The read already existed and nothing called it.** `GET
// /admin/usage/daily?org_slug=` serves this series, and `/usage` has computed
// calls, credits, cost, margin, runway and the silent flag per organization
// all along. This page read none of it.
//
// 🔴 **Every judgement here comes from `lib/usage.ts`.** Not one verdict is
// re-derived. The fleet board and this panel must never disagree about one
// row — `golive.ts` and `fallback.ts` both record what that costs — so a
// margin, a runway and a flag are decided in exactly one place, and this file
// only arranges them.
//
// ⚠️ **A server component.** It renders numbers the page already fetched with
// the caller's own session, and it writes nothing.

import Spark from "../../Spark";
import { formatCredits, formatUsd } from "@/lib/format";
import { chipClass } from "@/lib/tone";
import {
  customerUsageState,
  hasUnbilled,
  marginLabel,
  marginTone,
  orgFlags,
  rowRunwayLabel,
  runwayTone,
  type OrgUsageRow,
  type UsageDay,
} from "@/lib/usage";

export default function CustomerUsage({
  row,
  days,
  windowDays,
  error,
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
}) {
  // ⚠️ The judgement is `lib/usage.ts`'s, with its own test. This file only
  // arranges what it returns.
  const state = customerUsageState(row, days);

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>What they spent</h2>
        <p>
          The last {windowDays} days, the same numbers the fleet board reads.
          The ledger above says <i>when</i> credits left. This says what for.
        </p>
      </div>

      {error && <p className="result err">{error}</p>}

      {!error && state.kind === "quiet" && (
        // ⚠️ An empty state that NAMES what is absent (DESIGN.md §7 rule 3).
        // A blank panel here reads as a quiet month, and right now the true
        // reason is almost always that the Router is not serving yet.
        <div className="empty">
          <h3>No AI calls in the last {windowDays} days</h3>
          <p className="muted">
            Nothing was metered for this customer in the window. That is a
            quiet month if the Router is serving, and it is everything if it
            is not — <code>ROUTER_SERVING_ENABLED</code> gates that, and while
            it is off no call reaches the meter for anybody.
          </p>
        </div>
      )}

      {/* 🔴 H-76, said out loud instead of mis-reported. The fleet read is a
          capped page sorted by spend and takes no org filter, so a quiet
          customer with real traffic is simply not in it. Printing "no calls"
          here would put that truncation on their page as a fact. */}
      {!error && state.kind === "truncated" && (
        <div className="banner">
          <strong>Calls served, and no fleet row to judge them by.</strong> The
          per-organization read is a capped page ordered by spend, so this
          customer is below the cut. The daily series below is theirs and is
          complete. <a href="/usage">Open the fleet board →</a>
        </div>
      )}

      {!error && state.kind === "truncated" && days.length > 0 && (
        <Spark days={days} label="Credits per day" />
      )}

      {!error && state.kind === "full" && (
        <>
          {/* 🔴 The flags FIRST. Whether this customer hit a wall, went
              silent or ran calls we could not bill is the answer to "why did
              my credits go fast" more often than any number below it. */}
          {orgFlags(state.row).length > 0 && (
            <p className="rowline">
              {orgFlags(state.row).map((f) => (
                <span key={f.label} className={chipClass(f.tone)}>
                  {f.label}
                </span>
              ))}
            </p>
          )}

          <dl className="modelfacts">
            <div>
              <dt>Calls</dt>
              <dd>{state.row.calls.toLocaleString("en-IN")}</dd>
            </div>
            <div>
              <dt>Credits spent</dt>
              <dd>{formatCredits(state.row.credits)}</dd>
            </div>
            <div>
              {/* ⚠️ OUR cost, not theirs. Two numbers on one panel and
                  reading one as the other inverts a margin. */}
              <dt>Cost to us</dt>
              <dd>{formatUsd(state.row.costUsd)}</dd>
            </div>
            <div>
              <dt>Margin</dt>
              <dd>
                <span className={chipClass(marginTone(state.row.marginRatio))}>
                  {marginLabel(state.row.marginRatio)}
                </span>
              </dd>
            </div>
            <div>
              <dt>Runway</dt>
              <dd>
                <span className={chipClass(runwayTone(state.row.runwayDays))}>
                  {rowRunwayLabel(state.row)}
                </span>
              </dd>
            </div>
            <div>
              {/* 🔴 A refusal cost us nothing. An unbilled call cost us the
                  vendor's bill — we said yes and did not charge. The fleet
                  board keeps these in two columns for that reason. */}
              <dt>Refused</dt>
              {/* ⚠️ A tone CHIP, not a coloured `dd`. `.modelfacts dd` sets
                  `color: var(--text)` and out-specifies `.danger-t`, so the
                  alarm colour silently lost — the number rendered in plain
                  body text. Measured 2026-09-21. `tone.ts` is the one status
                  vocabulary (DESIGN.md §4) and a chip cannot be overridden
                  by the container it sits in. */}
              <dd>
                {state.row.refusals > 0 ? (
                  <span className={chipClass("warn")}>{state.row.refusals}</span>
                ) : (
                  state.row.refusals
                )}
              </dd>
            </div>
            <div>
              <dt>Served, not billed</dt>
              <dd
                title={
                  hasUnbilled(state.row)
                    ? `${state.row.unbilledCalls} served call(s) we could not meter, about ` +
                      `${state.row.unbilledTokens.toLocaleString("en-IN")} tokens. Absorbed, not charged.`
                    : undefined
                }
              >
                {/* 🔴 The loudest number on this panel when it is not zero.
                    A refusal cost us nothing. This is a call we said YES to
                    and did not charge for — they hold the completion and we
                    hold the vendor's bill. */}
                {hasUnbilled(state.row) ? (
                  <span className={chipClass("danger")}>
                    {state.row.unbilledCalls}
                  </span>
                ) : (
                  state.row.unbilledCalls
                )}
              </dd>
            </div>
          </dl>

          {/* ⚠️ No line for a series with no calls. `sparklinePath` draws a
              flat series through the MIDDLE on purpose — "steady" is the
              honest picture of steady — but a mid-height line over thirty
              days of nothing reads as steady USAGE. The numbers above already
              say zero. */}
          {days.some((d) => d.calls > 0) && (
            <Spark days={days} label="Credits per day" />
          )}

          <p className="note">
            Per-activity and per-person breakdowns are not on this page yet.{" "}
            <a href="/usage">Open the fleet board →</a>
          </p>
        </>
      )}
    </section>
  );
}
