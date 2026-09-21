"use client";

/**
 * Where the credits went — D66 (a) and (b), wired at last. **H-134.**
 *
 * 🔴 **Both endpoints were built, tested and reachable, and nothing called
 * them.** `settings/billing` read only `/me/billing`, so a customer saw a
 * balance, a burn figure and a runway, and could not see what any of it went
 * on. They could only ask us, which made every credit question a support
 * conversation.
 *
 * ⚠️ **It leads with CALLS while the rate card is unpriced**, which is the
 * shipped state (H-42). `billed_credits` is 0 on every row until somebody
 * prices the card, and a table ranked and drawn by credits would show a busy
 * month as a column of zeros — reading as broken, on exactly the customer
 * with the most to look at. `spendIsMeasured` decides, and the panel says so
 * out loud rather than printing zeros without comment.
 *
 * ⚠️ **No cap column, ever.** `/my/usage/members` says so in its own
 * docstring: showing a cap beside a spend implies the cap is enforced, and
 * `member_ai_cap` is not enforced while the member identity still comes from
 * an inbound header (H-73). A number that looks like a control and is not one
 * is worse than an absent number.
 *
 * ⚠️ Every judgement is imported from `lib/spend.ts`, which has its own tests.
 */

import { useCallback, useEffect, useState } from "react";

import { formatCredits } from "./lib/billing";
import {
  activityLabel,
  sortSpend,
  spendIsMeasured,
  spendShare,
  spendTotals,
  type ActivityRow,
  type MemberSpendRow,
  type SpendPayload,
} from "./lib/spend";

/** One row of the bar table, shared by both breakdowns. */
function SpendRow({
  name,
  calls,
  credits,
  share,
  measured,
  mono,
}: {
  name: string;
  calls: number;
  credits: string;
  share: number;
  measured: boolean;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span
          className={`truncate text-sm text-foreground ${mono ? "font-mono text-xs" : ""}`}
          title={name}
        >
          {name}
        </span>
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
          {calls.toLocaleString("en-IN")} call{calls === 1 ? "" : "s"}
          {measured ? ` · ${formatCredits(Number(credits) || 0)}` : ""}
        </span>
      </div>
      {/* The bar is a proportion, never a number. `spendShare` returns 0
          rather than NaN when there is nothing to divide by — a NaN width
          renders as nothing, which reads as "no usage". */}
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-secondary"
        role="presentation"
      >
        <div
          className="h-full rounded-full bg-primary"
          style={{ width: `${Math.round(share * 100)}%` }}
        />
      </div>
    </div>
  );
}

function Panel({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      <div className="rounded-xl border border-border p-4">{children}</div>
    </section>
  );
}

export default function SpendBreakdown() {
  const [activity, setActivity] = useState<SpendPayload<ActivityRow> | null>(null);
  const [members, setMembers] = useState<SpendPayload<MemberSpendRow> | null>(null);

  // ⚠️ Each read degrades to ABSENT rather than erroring the page — the same
  // rule the seats and roster blocks follow. A Console deployed nowhere 503s,
  // and billing must still render.
  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/billing/usage/activity", { cache: "no-store" });
      setActivity(r.ok ? await r.json() : null);
    } catch {
      setActivity(null);
    }
    try {
      const r = await fetch("/api/billing/usage/members", { cache: "no-store" });
      // A 403 here is ORDINARY: per-person spend is admin-only, and this
      // component renders for anybody the page admits.
      setMembers(r.ok ? await r.json() : null);
    } catch {
      setMembers(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const aRows = activity?.rows ?? [];
  const mRows = members?.rows ?? [];
  if (aRows.length === 0 && mRows.length === 0) return null;

  const aMeasured = spendIsMeasured(aRows);
  const aTotals = spendTotals(aRows);

  return (
    <div className="flex flex-col gap-6">
      {aRows.length > 0 && (
        <Panel
          title="What you used it on"
          hint={`Last ${activity?.windowDays ?? 30} days`}
        >
          {/* 🔴 Said once, at the top, and only when it is true. A customer
              reading a column of zeros deserves the reason, and the reason is
              ours, not theirs. */}
          {!aMeasured && (
            <p className="mb-3 rounded-lg border border-border bg-secondary p-3 text-xs text-muted-foreground">
              Your calls are counted from the first one. The credit figures
              read zero because AI pricing is not switched on for this account
              yet — nothing here has been charged.
            </p>
          )}
          <div className="divide-y divide-border">
            {sortSpend(aRows).map((r) => (
              <SpendRow
                key={r.activity}
                name={activityLabel(r.activity)}
                calls={r.calls}
                credits={r.credits}
                share={spendShare(aRows, r)}
                measured={aMeasured}
              />
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            {aTotals.calls.toLocaleString("en-IN")} call
            {aTotals.calls === 1 ? "" : "s"} in total
            {aMeasured ? `, ${formatCredits(aTotals.credits)}` : ""}.
          </p>
        </Panel>
      )}

      {mRows.length > 0 && (
        <Panel
          title="Who used it"
          hint={`Last ${members?.windowDays ?? 30} days`}
        >
          <div className="divide-y divide-border">
            {sortSpend(mRows).map((r) => (
              <SpendRow
                key={r.member}
                name={r.member}
                calls={r.calls}
                credits={r.credits}
                share={spendShare(mRows, r)}
                measured={spendIsMeasured(mRows)}
                mono
              />
            ))}
          </div>
          {/* ⚠️ NOT a cap, and it says so. `member_ai_cap` exists and is not
              enforced (H-73); a limit shown beside a spend would read as one
              that bites. */}
          <p className="mt-3 text-xs text-muted-foreground">
            Per-person usage, for visibility. There is no per-person limit on
            this account.
          </p>
        </Panel>
      )}
    </div>
  );
}
