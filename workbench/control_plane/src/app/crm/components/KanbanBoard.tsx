"use client";

/**
 * The kanban — the deals board is the landing view (specs/crm_app.md §5
 * surface 1), and CRM-Q3 draws the Leads board through the same component.
 * `entity` picks the card and the lane header. The drop, the lane and the
 * Mirror-mode rule are shared, so the two boards cannot disagree about them.
 *
 * Deal lanes are the chosen pipeline's stages in `orderLanes` order, colored.
 * The page may hide the empty ones (`lib/emptyStages.ts`). Lead lanes
 * are the Lead Status values. Cards carry name / company / amount / owner /
 * stage-age, and the next activity (CRM-X3, `lib/nextActivity.ts`). Dragging a card issues a `PATCH` of `status_id`. Each deal lane
 * header shows its count and ₹ total for the WHOLE lane, not the page of cards
 * returned.
 *
 * The geometry, the move plan and the optimistic re-tally are pure and live
 * in ../lib/board.ts — this file is the pixels and the HTML5 drag events.
 */

import Icon from "@/components/Icon";
import { TaskMeta } from "@/components/TaskMeta";
import { accentForHue } from "@/lib/statusAccent";
import type { MetaChip } from "@/lib/taskCard";
import { useState } from "react";
import {
  needsLostReason,
  planMove,
  rotLevel,
  ROT_TONES,
  type BoardLane,
  type DealMove,
  type LeadLane,
  type RotLevel,
} from "../lib/board";
import { isMirrored } from "../lib/connect";
import { compactMoney, money, shortEmail, stageAgeLabel } from "../lib/format";
import { nextActivityChip } from "../lib/nextActivity";
import type { Deal, Lead, Status } from "../lib/types";

type Card = Deal | Lead;

export default function KanbanBoard({
  entity = "deals",
  lanes,
  loading,
  readOnly = false,
  onMove,
  onOpen,
  onCreate,
  hiddenEmpty = 0,
}: {
  /** Which record the cards are. Deals unless stated. */
  entity?: "deals" | "leads";
  lanes: BoardLane[] | LeadLane[];
  loading: boolean;
  /** Mirror mode: Zoho owns every record, so no card drags (CRM-Z7). */
  readOnly?: boolean;
  /** `lostReason` is resolved by the caller before the move is sent — the
   *  gateway refuses a lost move without one BEFORE writing anything. */
  onMove: (move: DealMove, target: Status) => void;
  onOpen: (id: string) => void;
  /** Omitted in Mirror mode, where a record is made in Zoho (CRM-Z7). */
  onCreate?: () => void;
  /** How many empty stages the page hid (`lib/emptyStages.ts`). */
  hiddenEmpty?: number;
}) {
  const [dragging, setDragging] = useState<Card | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const isDeals = entity === "deals";

  if (!loading && lanes.length === 0 && hiddenEmpty > 0) {
    // Every stage is empty and hidden. Say so, and do not claim that no
    // stage exists.
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-sm text-muted-foreground">No deals match this view.</p>
        <p className="max-w-sm text-xs text-muted-foreground">
          {hiddenEmpty} empty {hiddenEmpty === 1 ? "stage is" : "stages are"} hidden. Use
          &quot;Empty stages&quot; in the bar to show them.
        </p>
      </div>
    );
  }

  if (!loading && lanes.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-sm text-muted-foreground">
          {isDeals ? "No deal stages are configured yet." : "No lead statuses are set yet."}
        </p>
        <p className="max-w-sm text-xs text-muted-foreground">
          {isDeals
            ? "Stages are data, not code. Change them in CRM settings, without a deploy."
            : "Lead statuses are data, not code. Change them in CRM settings."}
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-x-auto overflow-y-hidden p-4">
      <div className="flex h-full items-start gap-3">
        {(lanes as Array<BoardLane | LeadLane>).map((lane) => {
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
                if (!dragging || readOnly) return;
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
              {isDeals && (
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
              )}
              <div className="flex-1 space-y-2 overflow-y-auto p-2">
                {(lane.rows as Card[]).map((card) => (
                  <RecordCard
                    key={card.id}
                    entity={entity}
                    card={card}
                    locked={readOnly}
                    warnLost={needsLostReason(card, lane.status)}
                    // WS-26h rot: presentation only. The card's EXISTING
                    // stage-age badge changes colour and nothing else moves,
                    // closes or hides — discipline by visibility, not locks.
                    rot={rotLevel(
                      (card as Deal).status_changed_at,
                      isDeals ? lane.status.max_dwell_days : null,
                    )}
                    maxDwellDays={isDeals ? (lane.status.max_dwell_days ?? null) : null}
                    // CRM-X3: overdue, today, later or none. A won or lost
                    // lane gets no chip.
                    next={nextActivityChip(card.next_activity, lane.status.type)}
                    onDragStart={(e) => {
                      // ⚠️ Firefox refuses to START a drag unless dragstart
                      // sets something on the dataTransfer — without this the
                      // board is inert there while working everywhere else.
                      e.dataTransfer.setData("text/plain", card.id);
                      e.dataTransfer.effectAllowed = "move";
                      setDragging(card);
                    }}
                    onDragEnd={() => setDragging(null)}
                    onOpen={() => onOpen(card.id)}
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
        {onCreate && (
          <button
            onClick={onCreate}
            className="flex h-9 w-[180px] shrink-0 items-center justify-center gap-1.5 rounded-xl border border-dashed border-border text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground tech-transition"
          >
            <Icon name="Plus" className="w-3.5 h-3.5" />
            {isDeals ? "New deal" : "New lead"}
          </button>
        )}
      </div>
    </div>
  );
}

function RecordCard({
  entity,
  card,
  locked,
  warnLost,
  rot,
  maxDwellDays,
  next,
  onDragStart,
  onDragEnd,
  onOpen,
}: {
  entity: "deals" | "leads";
  card: Card;
  /** Mirror mode: the card never drags. */
  locked: boolean;
  warnLost: boolean;
  rot: RotLevel;
  maxDwellDays: number | null;
  /** The next-activity chip, or null for none (`lib/nextActivity.ts`). */
  next: MetaChip | null;
  onDragStart: (e: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onOpen: () => void;
}) {
  const deal = entity === "deals" ? (card as Deal) : null;
  const lead = entity === "leads" ? (card as Lead) : null;
  const title = deal ? deal.name : (lead?.lead_name ?? "");
  const detail = deal ? deal.organization_name : (lead?.organization_name ?? lead?.email);
  const overdue = next?.tone === "danger";
  return (
    <article
      // A record that Zoho owns does not move here (CRM-Z7): the write would
      // be refused, and a card that snaps back reads as a bug. In Mirror mode
      // no card moves at all.
      draggable={!locked && !isMirrored(card as unknown as Record<string, unknown>)}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onOpen}
      // An overdue card wears a red left edge, from the shared status colours.
      className={`cursor-pointer rounded-lg border border-border bg-card p-2.5 hover:border-primary/40 hover:bg-secondary/30 tech-transition ${
        // The hover border recolours all four sides, so the red edge repeats
        // it. A literal class, because Tailwind cannot see a built one.
        overdue ? `border-l-2 ${accentForHue("red").bar} hover:border-l-destructive` : ""
      }`}
    >
      <h3 className="truncate text-xs font-medium text-foreground">{title}</h3>
      {/* organization_name is projected by the gateway's LEFT JOIN — the
          browser never joins the org list, which is paged at 100. */}
      {detail && (
        <p className="mt-1 flex items-center gap-1 truncate text-[10px] text-muted-foreground">
          <Icon name="Building2" className="w-3 h-3 shrink-0" />
          {detail}
        </p>
      )}
      {next && <TaskMeta chips={[next]} className="mt-1.5" />}
      {deal ? (
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
      ) : (
        lead?.lead_source && (
          <p className="mt-2 truncate text-[10px] text-muted-foreground">{lead.lead_source}</p>
        )
      )}
      <div className="mt-1.5 flex items-center gap-1 text-[10px] text-muted-foreground">
        <Icon name="User" className="w-3 h-3 shrink-0" />
        <span className="truncate">{shortEmail(card.owner_email)}</span>
        {warnLost && (
          <span className="ml-auto text-[10px] text-warning" title="Marking this lost will ask for a reason">
            needs reason
          </span>
        )}
      </div>
    </article>
  );
}
