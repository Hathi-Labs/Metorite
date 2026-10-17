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
 *
 * WS-53 CRM-C2: a lane is the shared `BoardColumn`, and a card is the shared
 * `TaskCardShell`. So this board has the shape of the Projects and My Tasks
 * boards. The lane colour is `stageAccent`, in the inset chip of the header.
 */

import { Board } from "@/components/board/Board";
import { BoardColumn } from "@/components/board/BoardColumn";
import Icon from "@/components/Icon";
import { TaskCardShell, TaskCardTitle } from "@/components/TaskCardShell";
import { TaskMeta } from "@/components/TaskMeta";
import type { MetaChip } from "@/lib/taskCard";
import { useState } from "react";
import {
  needsLostReason,
  planMove,
  rotLevel,
  ROT_TONES,
  stageAccent,
  stageIcon,
  type BoardLane,
  type DealMove,
  type LeadLane,
  type RotLevel,
} from "../lib/board";
import { cardLabel } from "../lib/cardLabel";
import { isMirrored, type CrmMode } from "../lib/connect";
import { isRetired } from "../lib/emptyStages";
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
  mode = null,
  cursorId = null,
  emptyNote,
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
  /** The CRM mode. In Mirror mode a lane colour skips the stored `gray` (CRM-C2). */
  mode?: CrmMode;
  /** The record that the sheet shows. Its card wears the cursor ring (CRM-X4). */
  cursorId?: string | null;
  /**
   * CRM-Q17b item 4: the caller gives it when no lane holds a record. It says
   * why: a filter, a module that Zoho turned off, or no record yet.
   */
  emptyNote?: string;
}) {
  const [dragging, setDragging] = useState<Card | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const isDeals = entity === "deals";

  if (!loading && lanes.length === 0 && hiddenEmpty > 0) {
    // Every stage is empty and hidden. Say so, and do not claim that no
    // stage exists.
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-sm text-muted-foreground">{emptyNote ?? "No deals match this view."}</p>
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
    <>
      {!loading && emptyNote ? (
        // The lanes stay, so a stage and its + stay in reach (CRM-Q17b item 4).
        <p role="status" className="shrink-0 border-b border-border px-4 py-2 text-xs text-muted-foreground">
          {emptyNote}
        </p>
      ) : null}
      <Board className="flex-1">
        {(lanes as Array<BoardLane | LeadLane>).map((lane) => {
          const isTarget = over === lane.status.id;
          return (
            <BoardColumn
              key={lane.status.id}
              header={{
                accent: stageAccent(lane.status, mode),
                title: lane.status.name,
                count: lane.count,
                icon: stageIcon(lane.status.type),
              }}
              dropTarget={isTarget && !readOnly}
              // CRM-C3: a hidden stage, or one that Zoho no longer lists, shows
              // only with records, and says so.
              hidden={isRetired(lane.status)}
              empty={lane.rows.length === 0 ? "Nothing here" : undefined}
              totals={isDeals ? <LaneTotals lane={lane} /> : undefined}
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
            >
              {(lane.rows as Card[]).map((card) => (
                <RecordCard
                  key={card.id}
                  entity={entity}
                  card={card}
                  locked={readOnly}
                  atCursor={card.id === cursorId}
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
            </BoardColumn>
          );
        })}
        {onCreate && (
          <button
            onClick={onCreate}
            className="flex h-9 w-[180px] shrink-0 items-center justify-center gap-1.5 self-start rounded-lg border border-dashed border-border text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground tech-transition"
          >
            <Icon name="Plus" className="w-3.5 h-3.5" />
            {isDeals ? "New deal" : "New lead"}
          </button>
        )}
      </Board>
    </>
  );
}

/** The money line under a deal lane's chip. */
function LaneTotals({ lane }: { lane: BoardLane | LeadLane }) {
  return (
    <span className="flex items-center gap-1">
      <Icon name="IndianRupee" className="w-3 h-3" />
      {/* The lane total covers the whole lane; the cards below are one page
          of it. A header that counted only what it returned would lie about
          the busy lane somebody is actually looking at. */}
      <span>{compactMoney(lane.amount).replace("₹", "")}</span>
      {/* Weighted ₹ under the raw total (§5.1). Rendered only where it means
          something — a won lane's revenue is closed and a lost lane forecasts
          nothing, so the gateway sends 0 and printing "0 weighted" there would
          read as a bug. */}
      {lane.weighted > 0 && (
        <span title="Weighted by probability">
          · {compactMoney(lane.weighted).replace("₹", "")} wtd
        </span>
      )}
      {lane.rows.length < lane.count && (
        <span className="ml-auto">showing {lane.rows.length}</span>
      )}
    </span>
  );
}

function RecordCard({
  entity,
  card,
  locked,
  atCursor,
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
  /** The sheet shows this record. `j` and `k` move it (CRM-X4). */
  atCursor: boolean;
  warnLost: boolean;
  rot: RotLevel;
  maxDwellDays: number | null;
  /** The next-activity chip, or null for none (`lib/nextActivity.ts`). */
  next: MetaChip | null;
  onDragStart: (e: React.DragEvent) => void;
  onDragEnd: () => void;
  onOpen: () => void;
}) {
  const deal = entity === "deals" ? (card as Deal) : null;
  const lead = entity === "leads" ? (card as Lead) : null;
  const title = deal ? deal.name : (lead?.lead_name ?? "");
  const detail = deal ? deal.organization_name : (lead?.organization_name ?? lead?.email);
  const overdue = next?.tone === "danger";
  return (
    <TaskCardShell
      // A record that Zoho owns does not move here (CRM-Z7): the write would
      // be refused, and a card that snaps back reads as a bug. In Mirror mode
      // no card moves at all.
      draggable={!locked && !isMirrored(card as unknown as Record<string, unknown>)}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onActivate={() => onOpen()}
      // A `role="button"` is read by its name only, so the name carries the
      // card: the money or the company, the next step and the warnings.
      ariaLabel={cardLabel({
        title,
        amount: deal ? money(deal.amount, deal.currency) : null,
        detail,
        next,
        overdue,
        stageAge: deal ? stageAgeLabel(deal.status_changed_at) : null,
        rot: deal ? rot : undefined,
        needsReason: warnLost,
      })}
      atCursor={atCursor}
      // A won or a lost deal is closed, not done. The lane chip says so, so
      // the card is not dimmed and not struck through.
      completed={false}
      // CRM-X3: an overdue card wears a red left edge. The shell draws it,
      // from the shared status colours.
      overdue={overdue}
    >
      <TaskCardTitle>{title}</TaskCardTitle>
      {/* organization_name is projected by the gateway's LEFT JOIN — the
          browser never joins the org list, which is paged at 100. */}
      {detail && (
        <p className="flex items-center gap-1 truncate text-[10px] text-muted-foreground">
          <Icon name="Building2" className="w-3 h-3 shrink-0" />
          {detail}
        </p>
      )}
      {next && <TaskMeta chips={[next]} />}
      {deal ? (
        <div className="flex items-center justify-between gap-2">
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
          <p className="truncate text-[10px] text-muted-foreground">{lead.lead_source}</p>
        )
      )}
      <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
        <Icon name="User" className="w-3 h-3 shrink-0" />
        <span className="truncate">{shortEmail(card.owner_email)}</span>
        {warnLost && (
          <span className="ml-auto text-[10px] text-warning" title="Marking this lost will ask for a reason">
            needs reason
          </span>
        )}
      </div>
    </TaskCardShell>
  );
}
