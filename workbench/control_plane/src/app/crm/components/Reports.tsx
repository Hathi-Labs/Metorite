"use client";

/**
 * `?tab=reports` — forecast & funnel (WS-26g, §5.1 system 2).
 *
 * Four blocks over `/crm/reports/*`: the forecast by stage, the funnel, the
 * trailing-90d win/loss picture, and the owner leaderboard.
 *
 * ⚠️ **This file computes nothing.** Every figure is server-side in
 * `routes/crm/reports.py` — the visited-set union, the conversion-forward
 * share, the medians, the win rate, the weighted ₹ — and is rendered verbatim.
 * The bar widths and the wording come from `../lib/reports.ts`, which is pure
 * and unit-tested. A number derived here would be a second answer to a
 * question that already has one, and the two would disagree the first time
 * somebody changed a definition.
 *
 * Two pieces of honesty the surface owes, both of which look like clutter
 * until the day they are the whole story:
 *   · **Median dwell carries its sample size.** `dwell_seconds` is stamped
 *     only when a deal LEAVES a stage, so the median is over departures and
 *     can legitimately be one measurement.
 *   · **Orphaned stage names are shown.** Renaming a lane orphans its history
 *     from the funnel, and a total that silently shrank when somebody fixed a
 *     typo is worse than a total with a footnote.
 *
 * **The weekly digest** (WS-53 CRM-Q28 item 13). `DigestCard` reads the
 * newest digest of the org and shows its week, its text, its numbers as a
 * table and a line that says AI wrote the text from them. The server checked
 * each number of the text against that table, and this file draws both
 * verbatim. An org with no digest sees no card.
 */

import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import { listDigests } from "../lib/api";
import { stageAccent } from "../lib/board";
import { useCrmModeValue } from "../lib/modeContext";
import { money, shortDate } from "../lib/format";
import { useCompactMoney } from "../lib/useCompactMoney";
import {
  byWeight,
  DIGEST_AI_LINE,
  digestMissing,
  digestTableRows,
  digestValue,
  digestWeekLabel,
  dwellSummary,
  funnelWidths,
  hasFunnel,
  hasOwners,
  hasPipeline,
  hasWinLoss,
  newestDigest,
  ownerLabel,
  percentLabel,
  pipelineWidths,
  shareOfForecast,
  undatedNotice,
  windowLabel,
} from "../lib/reports";
import type { CrmDigest, CrmReports } from "../lib/types";

export default function Reports({
  reports,
  loading,
}: {
  reports: CrmReports | null;
  loading: boolean;
}) {
  if (!reports) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">
          {loading ? "Reading the pipeline…" : "No report yet — hit Refresh."}
        </p>
      </div>
    );
  }
  return (
    <div className="flex-1 space-y-6 overflow-y-auto p-4 sm:p-6">
      <DigestCard />
      <Forecast reports={reports} />
      <Funnel reports={reports} />
      <div className="grid gap-6 lg:grid-cols-2">
        <WinLoss reports={reports} />
        <Leaderboard reports={reports} />
      </div>
    </div>
  );
}

/** One report block: a titled card with a one-line reading instruction. */
function Block({
  title,
  blurb,
  children,
}: {
  title: string;
  blurb: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border bg-card p-4 sm:p-5">
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      <p className="mt-0.5 text-xs text-muted-foreground">{blurb}</p>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-center text-xs text-muted-foreground">{children}</p>;
}

/** A labelled figure. The label carries the unit so the number need not. */
function Figure({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-lg font-semibold text-foreground">{value}</p>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

// ── 1 · the forecast ──────────────────────────────────────────────────────

function Forecast({ reports }: { reports: CrmReports }) {
  const mode = useCrmModeValue();
  const compact = useCompactMoney();
  const { pipeline } = reports;
  const widths = pipelineWidths(pipeline);
  const ordered = pipeline.stages;
  return (
    <Block
      title="Forecast by stage"
      blurb="Open and ongoing lanes only. Weighted ₹ is each deal's amount at its own probability, falling back to its stage's default — won is booked revenue, lost is nothing, and a parked deal is nobody's forecast."
    >
      <div className="mb-4 grid grid-cols-3 gap-4">
        <Figure label="Open deals" value={String(pipeline.count)} />
        <Figure label="In the pipeline" value={money(pipeline.amount)} />
        <Figure label="Weighted" value={money(pipeline.weighted)} />
      </div>
      {!hasPipeline(pipeline) ? (
        // `hasPipeline` tests whether any open/ongoing STAGE exists, not
        // whether one holds a deal — an empty lane still renders a row, the
        // same courtesy the board gives its empty columns.
        <Empty>
          No open or ongoing stages are configured — the forecast reads those
          lanes only.
        </Empty>
      ) : (
        <ul className="space-y-2">
          {ordered.map((stage, index) => (
            <li key={stage.status.id} className="flex items-center gap-3">
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${stageAccent(stage.status, mode).dot}`}
                aria-hidden
              />
              <span className="w-36 shrink-0 truncate text-xs text-foreground">
                {stage.status.name}
              </span>
              <span className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
                <span
                  className="block h-full rounded-full bg-primary"
                  style={{ width: `${widths[index]}%` }}
                />
              </span>
              <span className="w-20 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                {stage.count} deals
              </span>
              <span className="w-24 shrink-0 text-right text-xs tabular-nums text-foreground">
                {compact(stage.weighted)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {hasPipeline(pipeline) && (
        <p className="mt-3 text-[11px] text-muted-foreground">
          Biggest first:{" "}
          {byWeight(pipeline)
            .slice(0, 3)
            .map(
              (stage) =>
                `${stage.status.name} ${shareOfForecast(stage, pipeline)}%`
            )
            .join(" · ")}
        </p>
      )}
    </Block>
  );
}

// ── 2 · the funnel ────────────────────────────────────────────────────────

function Funnel({ reports }: { reports: CrmReports }) {
  const mode = useCrmModeValue();
  const { funnel } = reports;
  const widths = funnelWidths(funnel);
  return (
    <Block
      title="Funnel"
      blurb="A deal counts in every stage it has visited — its current one included, which is the only one an imported deal has. Conversion is “ever got past this stage”, not “went to the next one”."
    >
      {!hasFunnel(funnel) ? (
        <Empty>No deals yet, so no funnel.</Empty>
      ) : (
        <>
          <ul className="space-y-3">
            {funnel.stages.map((stage, index) => (
              <li key={stage.status.id}>
                <div className="flex items-center gap-3">
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${stageAccent(stage.status, mode).dot}`}
                    aria-hidden
                  />
                  <span className="w-36 shrink-0 truncate text-xs text-foreground">
                    {stage.status.name}
                  </span>
                  <span className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
                    <span
                      className="block h-full rounded-full bg-primary"
                      style={{ width: `${widths[index]}%` }}
                    />
                  </span>
                  <span className="w-24 shrink-0 text-right text-xs tabular-nums text-foreground">
                    {stage.entered} entered
                  </span>
                  <span className="w-24 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                    {percentLabel(stage.conversion_forward)} on
                  </span>
                </div>
                <p className="ml-[3.75rem] mt-0.5 text-[11px] text-muted-foreground">
                  {/* Median-over-DEPARTURES, with its sample size, because a
                      deal still sitting in a stage contributes nothing to it. */}
                  Median time in stage: {dwellSummary(stage)}
                </p>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[11px] text-muted-foreground">
            {funnel.deals} deals · {funnel.transitions} recorded moves. A deal
            that has never moved is counted through the stage it is in.
          </p>
          {funnel.unmatched.length > 0 && (
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 p-2.5">
              <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 text-warning" />
              <p className="text-[11px] text-foreground">
                History under stage names that no longer exist — renamed or
                deleted lanes. Counted here rather than dropped:{" "}
                {funnel.unmatched
                  .map((orphan) => `${orphan.name} (${orphan.deals} deals)`)
                  .join(", ")}
                .
              </p>
            </div>
          )}
        </>
      )}
    </Block>
  );
}

// ── 3 · win / loss ────────────────────────────────────────────────────────

function WinLoss({ reports }: { reports: CrmReports }) {
  const { winLoss } = reports;
  const compact = useCompactMoney();
  const notice = undatedNotice(winLoss);
  return (
    <Block
      title="Win / loss"
      blurb={`Closed deals ${windowLabel(winLoss.window_days)} — since ${shortDate(winLoss.since)}.`}
    >
      {!hasWinLoss(winLoss) ? (
        <Empty>Nothing has closed in the window.</Empty>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Figure label="Win rate" value={percentLabel(winLoss.win_rate)} />
            <Figure
              label="Won"
              value={String(winLoss.won)}
              hint={compact(winLoss.won_amount)}
            />
            <Figure
              label="Lost"
              value={String(winLoss.lost)}
              hint={compact(winLoss.lost_amount)}
            />
            <Figure
              label="Average cycle"
              value={
                winLoss.average_cycle_days === null
                  ? "—"
                  : `${winLoss.average_cycle_days} days`
              }
              hint="created to closed"
            />
          </div>
          {winLoss.lost_reasons.length > 0 && (
            <ul className="mt-4 space-y-1.5">
              {winLoss.lost_reasons.map((reason) => (
                <li
                  key={reason.label}
                  className="flex items-center justify-between gap-3 text-xs"
                >
                  <span
                    className={
                      reason.attributed
                        ? "truncate text-foreground"
                        : "truncate italic text-muted-foreground"
                    }
                  >
                    {reason.label}
                  </span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {reason.deals} · {compact(reason.amount)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {notice && (
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-border bg-secondary p-2.5">
          <Icon name="Info" className="mt-0.5 h-3.5 w-3.5 text-muted-foreground" />
          <p className="text-[11px] text-muted-foreground">{notice}</p>
        </div>
      )}
    </Block>
  );
}

// ── 4 · owner leaderboard ─────────────────────────────────────────────────

function Leaderboard({ reports }: { reports: CrmReports }) {
  const { owners } = reports;
  const compact = useCompactMoney();
  return (
    <Block
      title="By owner"
      blurb={`Who is carrying what. Won ₹ is ${windowLabel(owners.window_days)}, the same window as win / loss.`}
    >
      {!hasOwners(owners) ? (
        <Empty>No deals are assigned yet.</Empty>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground">
              <th className="pb-2 text-left font-normal">Owner</th>
              <th className="pb-2 text-right font-normal">Open</th>
              <th className="pb-2 text-right font-normal">Weighted</th>
              <th className="pb-2 text-right font-normal">Won</th>
            </tr>
          </thead>
          <tbody>
            {owners.owners.map((row) => (
              <tr key={row.owner_email ?? "__unassigned__"} className="border-t border-border">
                <td className="max-w-[10rem] truncate py-1.5 text-foreground">
                  {ownerLabel(row.owner_email)}
                </td>
                <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                  {row.open_deals}
                </td>
                <td className="py-1.5 text-right tabular-nums text-foreground">
                  {compact(row.weighted)}
                </td>
                <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                  {compact(row.won_amount)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {owners.omitted > 0 && (
        <p className="mt-3 text-[11px] text-muted-foreground">
          {owners.omitted} more owner(s) not shown — the leaderboard is capped,
          and says so rather than looking complete.
        </p>
      )}
    </Block>
  );
}

// ── 5 · the weekly digest (WS-53 CRM-Q28) ─────────────────────────────────

/**
 * Loads the newest digest. A failed read and an empty list draw nothing:
 * the digest is extra, and the four blocks below must not wait for it.
 */
function DigestCard() {
  const [digest, setDigest] = useState<CrmDigest | null>(null);
  useEffect(() => {
    let live = true;
    listDigests(4)
      .then((digests) => {
        if (live) setDigest(newestDigest(digests));
      })
      .catch(() => {
        if (live) setDigest(null);
      });
    return () => {
      live = false;
    };
  }, []);
  return digest ? <DigestCardView digest={digest} /> : null;
}

/** The newest digest: its week, its text, its numbers and the AI line. */
export function DigestCardView({ digest }: { digest: CrmDigest }) {
  const rows = digestTableRows(digest.numbers);
  const missing = digestMissing(digest.numbers);
  return (
    <Block title="Weekly digest" blurb={digestWeekLabel(digest)}>
      <p className="whitespace-pre-line text-sm text-foreground">{digest.text}</p>
      <div className="mt-3 flex items-start gap-2 rounded-lg border border-border bg-secondary p-2.5">
        <Icon name="Sparkles" className="mt-0.5 h-3.5 w-3.5 text-muted-foreground" />
        <p className="text-[11px] text-muted-foreground" data-digest-ai-line>
          {DIGEST_AI_LINE}
        </p>
      </div>
      {rows.length > 0 && (
        <table className="mt-4 w-full text-xs">
          <thead>
            <tr className="text-muted-foreground">
              <th className="pb-2 text-left font-normal">Part</th>
              <th className="pb-2 text-left font-normal">Group</th>
              <th className="pb-2 text-right font-normal">Number</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.part}-${row.label}-${index}`} className="border-t border-border">
                <td className="max-w-[14rem] truncate py-1.5 text-muted-foreground">{row.part}</td>
                <td className="max-w-[12rem] truncate py-1.5 text-foreground">{row.label}</td>
                <td className="py-1.5 text-right tabular-nums text-foreground">
                  {digestValue(row)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {missing.length > 0 && (
        <p className="mt-3 text-[11px] text-muted-foreground">
          Not counted this week: {missing.join(", ")}.
        </p>
      )}
    </Block>
  );
}
