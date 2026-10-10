"use client";

/**
 * The deals kanban — the landing tab (specs/crm_app.md §5 surface 1).
 *
 * Lanes are `crm_deal_statuses` by position, colored; cards carry
 * name / company / amount / owner / stage-age; dragging a card issues
 * `PATCH /crm/deals/{id} {status_id}`; each lane header shows its count and
 * ₹ total for the WHOLE lane, not the page of cards returned.
 *
 * The geometry, the move plan and the optimistic re-tally are pure and live
 * in ../lib/board.ts — this file is the pixels and the HTML5 drag events.
 */

import Icon from "@/components/Icon";
import { useState } from "react";
import {
  needsLostReason,
  planMove,
  rotLevel,
  ROT_TONES,
  type BoardLane,
  type DealMove,
  type RotLevel,
} from "../lib/board";
import { compactMoney, money, shortEmail, stageAgeLabel } from "../lib/format";
import type { Deal, Status } from "../lib/types";

export default function KanbanBoard({
  lanes,
  loading,
  onMove,
  onOpen,
  onCreate,
}: {
  lanes: BoardLane[];
  loading: boolean;
  /** `lostReason` is resolved by the caller before the move is sent — the
   *  gateway refuses a lost move without one BEFORE writing anything. */
  onMove: (move: DealMove, target: Status) => void;
  onOpen: (dealId: string) => void;
  onCreate: () => void;
}) {
  const [dragging, setDragging] = useState<Deal | null>(null);
  const [over, setOver] = useState<string | null>(null);

  if (!loading && lanes.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-sm text-muted-foreground">
          No deal stages are configured yet.
        </p>
        <p className="text-xs text-muted-foreground max-w-sm">
          Stages are data, not code — the pipeline is reshaped without a deploy,
          and the Zoho import brings the team&apos;s real stage names with it.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-x-auto overflow-y-hidden p-4">
      <div className="flex h-full items-start gap-3">
        {lanes.map((lane) => {
          const isTarget = over === lane.status.id;
          return (
            <section
              key={lane.status.id}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(lane.status.id);
              }}
              onDragLeave={() => setOver((v) => (v === lane.status.id ? null : v))}
              onDrop={(e) => {
                e.preventDefault();
                setOver(null);
                if (!dragging) return;
                const move = planMove(dragging, lane.status.id);
                setDragging(null);
                // A drop back into the same lane is not a transition: sending
                // it would write a dwell of zero and a "Proposal → Proposal"
                // line onto the timeline.
                if (move) onMove(move, lane.status);
              }}
              className={`flex h-full w-[264px] shrink-0 flex-col rounded-xl border tech-transition ${
                isTarget
                  ? "border-primary bg-primary/5"
                  : "border-border bg-card/40"
              }`}
            >
              <header className="flex items-center gap-2 border-b border-border px-3 py-2">
                <span className={`w-2 h-2 rounded-full ${lane.tone}`} />
                <h2 className="flex-1 truncate text-sm font-semibold text-foreground">
                  {lane.status.name}
                </h2>
                <span className="text-[10px] text-muted-foreground">
                  {lane.count}
                </span>
              </header>
              <div className="flex items-center gap-1 border-b border-border px-3 py-1.5 text-[10px] text-muted-foreground">
                <Icon name="IndianRupee" className="w-3 h-3" />
                {/* The lane total covers the whole lane; the cards below are
                    one page of it. A header that counted only what it
                    returned would lie about the busy lane somebody is
                    actually looking at. */}
                <span>{compactMoney(lane.amount).replace("₹", "")}</span>
                {/* Weighted ₹ under the raw total (§5.1). Rendered only where
                    it means something — a won lane's revenue is closed and a
                    lost lane forecasts nothing, so the gateway sends 0 and
                    printing "0 weighted" there would read as a bug. */}
                {lane.weighted > 0 && (
                  <span className="opacity-70" title="Weighted by probability">
                    · {compactMoney(lane.weighted).replace("₹", "")} wtd
                  </span>
                )}
                {lane.rows.length < lane.count && (
                  <span className="ml-auto opacity-70">
                    showing {lane.rows.length}
                  </span>
                )}
              </div>
              <div className="flex-1 space-y-2 overflow-y-auto p-2">
                {lane.rows.map((deal) => (
                  <DealCard
                    key={deal.id}
                    deal={deal}
                    warnLost={needsLostReason(deal, lane.status)}
                    // WS-26h rot: presentation only. The card's EXISTING
                    // stage-age badge changes colour and nothing else moves,
                    // closes or hides — discipline by visibility, not locks.
                    rot={rotLevel(deal.status_changed_at, lane.status.max_dwell_days)}
                    maxDwellDays={lane.status.max_dwell_days ?? null}
                    onDragStart={(e) => {
                      // ⚠️ Firefox refuses to START a drag unless dragstart
                      // sets something on the dataTransfer — without this the
                      // board is inert there while working everywhere else.
                      e.dataTransfer.setData("text/plain", deal.id);
                      e.dataTransfer.effectAllowed = "move";
                      setDragging(deal);
                    }}
                    onDragEnd={() => setDragging(null)}
                    onOpen={() => onOpen(deal.id)}
                  />
                ))}
                {lane.rows.length === 0 && (
                  <p className="px-1 py-6 text-center text-[10px] text-muted-foreground">
                    Nothing here
                  </p>
                )}
              </div>
            </section>
          );
        })}
        <button
          onClick={onCreate}
          className="flex h-9 w-[180px] shrink-0 items-center justify-center gap-1.5 rounded-xl border border-dashed border-border text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground tech-transition"
        >
          <Icon name="Plus" className="w-3.5 h-3.5" />
          New deal
        </button>
      </div>
    </div>
  );
}

function DealCard({
  deal,
  warnLost,
  rot,
  maxDwellDays,
  onDragStart,
  onDragEnd,
  onOpen,
}: {
  deal: Deal;
  warnLost: boolean;
  rot: RotLevel;
  maxDwellDays: number | null;
  onDragStart: (e: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onOpen: () => void;
}) {
  return (
    <article
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onOpen}
      className="cursor-pointer rounded-lg border border-border bg-card p-2.5 hover:border-primary/40 hover:bg-secondary/30 tech-transition"
    >
      <h3 className="truncate text-xs font-medium text-foreground">{deal.name}</h3>
      {/* organization_name is projected by the gateway's LEFT JOIN — the
          browser never joins the org list, which is paged at 100. */}
      {deal.organization_name && (
        <p className="mt-1 flex items-center gap-1 truncate text-[10px] text-muted-foreground">
          <Icon name="Building2" className="w-3 h-3 shrink-0" />
          {deal.organization_name}
        </p>
      )}
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-foreground">
          {money(deal.amount, deal.currency)}
        </span>
        <span
          className={`text-[10px] ${ROT_TONES[rot]}`}
          title={
            maxDwellDays === null
              ? "Time in this stage"
              : `Time in this stage — this stage allows ${maxDwellDays} day(s)`
          }
        >
          {stageAgeLabel(deal.status_changed_at)}
        </span>
      </div>
      <div className="mt-1.5 flex items-center gap-1 text-[10px] text-muted-foreground">
        <Icon name="User" className="w-3 h-3 shrink-0" />
        <span className="truncate">{shortEmail(deal.owner_email)}</span>
        {warnLost && (
          <span className="ml-auto text-[10px] text-warning" title="Marking this lost will ask for a reason">
            needs reason
          </span>
        )}
      </div>
    </article>
  );
}
