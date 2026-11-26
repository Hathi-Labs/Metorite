"use client";

/**
 * The Agents view · the overview of a large fleet (WS-MCP AMR-4, §3.12.9).
 *
 * Reading this as: one tile for each orchestrator, under its person, with the
 * ones that want attention first. A tile holds its counts, one square for each
 * session and a bar of its sub-agents. The quiet orchestrators fold into one
 * row. A tile, or a square, opens that orchestrator's graph. Read only.
 */
import { useState } from "react";

import Icon from "@/components/Icon";
import { PersonAvatar } from "@/components/room/Identity";
import Button from "@/components/ui/Button";

import { type LensCounts, type LensSession, lensWord } from "../lib/agentLenses";
import type { Tile, TilePerson } from "../lib/agentScale";
import { NO_MATCH_COPY, NO_ORCHESTRATOR } from "./AgentBoardList";
import { SquareLegend, Squares, SubBar } from "./AgentSquares";

/** "2 need you · 1 failed · 13 working · 11 waiting · 22 idle or done". */
export function TileCounts({ counts }: { counts: LensCounts }) {
  const rest = counts.idle + counts.done;
  const parts: Array<[string, string]> = [];
  if (counts.you > 0) parts.push([counts.you === 1 ? "1 needs you" : `${counts.you} need you`, "text-warning"]);
  if (counts.failed > 0) parts.push([`${counts.failed} failed`, "text-destructive"]);
  if (counts.working > 0) parts.push([`${counts.working} working`, ""]);
  if (counts.waiting > 0) parts.push([`${counts.waiting} waiting`, ""]);
  if (rest > 0) parts.push([`${rest} idle or done`, ""]);
  return (
    <span className="flex min-w-0 flex-wrap gap-x-2 text-[11px] text-muted-foreground">
      {parts.map(([text, tone]) => (
        <span key={text} className={`whitespace-nowrap ${tone}`}>
          {text}
        </span>
      ))}
    </span>
  );
}

export function tileName(tile: Pick<Tile, "orch">): string {
  return tile.orch?.name ?? NO_ORCHESTRATOR;
}

/** "48 sessions · metorite-home, aroh-dev-1 and 2 more". */
export function tileSub(tile: Pick<Tile, "sessions" | "machines">): string {
  const n = tile.sessions.length;
  const named = tile.machines.slice(0, 2).join(", ");
  const more = tile.machines.length - 2;
  return [`${n} ${n === 1 ? "session" : "sessions"}`, named + (more > 0 ? ` and ${more} more` : "")]
    .filter(Boolean)
    .join(" · ");
}

function TileCard({
  tile,
  nowMs,
  onOpen,
  onPick,
}: {
  tile: Tile;
  nowMs: number;
  onOpen: (tile: Tile) => void;
  onPick: (session: LensSession) => void;
}) {
  const name = tileName(tile);
  return (
    <div
      data-agent-tile={tile.key}
      data-agent-tile-attention={tile.counts.you > 0 ? "you" : undefined}
      onClick={() => onOpen(tile)}
      className={`flex min-w-0 cursor-pointer flex-col gap-1.5 rounded-lg border-2 bg-card px-3 py-2.5 shadow-sm hover:shadow-md ${
        tile.counts.you > 0 ? "border-warning" : "border-border hover:border-primary/40"
      }`}
    >
      <Button
        variant="text"
        size="none"
        aria-label={`Open ${name}, ${tileSub(tile)}`}
        onClick={(event) => {
          event.stopPropagation();
          onOpen(tile);
        }}
        layout="flex min-w-0 items-center justify-start gap-2 rounded-md text-left text-foreground no-underline hover:no-underline"
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
          <Icon name="Bot" size={15} aria-hidden />
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-sm font-semibold text-foreground">{name}</span>
          <span className="truncate text-xs font-normal text-muted-foreground">{tileSub(tile)}</span>
        </span>
        <Icon name="ChevronRight" size={15} className="shrink-0 text-muted-foreground" aria-hidden />
      </Button>
      <TileCounts counts={tile.counts} />
      <Squares sessions={tile.sessions} nowMs={nowMs} onPick={onPick} />
      <SubBar subs={tile.subs} />
    </div>
  );
}

const GRID = { gridTemplateColumns: "repeat(auto-fill, minmax(min(17rem, 100%), 1fr))" } as const;

export interface AgentOverviewProps {
  people: readonly TilePerson[];
  nowMs: number;
  nameOf: (email: string) => string;
  onOpenTile: (tile: Tile) => void;
  onPickSession: (session: LensSession) => void;
  onOpenPerson: (person: string) => void;
  /** A find is on: a quiet orchestrator that matches draws too. */
  finding?: boolean;
}

export function AgentOverview({
  people,
  nowMs,
  nameOf,
  onOpenTile,
  onPickSession,
  onOpenPerson,
  finding = false,
}: AgentOverviewProps) {
  const [shown, setShown] = useState<ReadonlySet<string>>(() => new Set());
  if (people.length === 0) {
    return (
      <p data-agent-lens-empty="" className="p-4 text-sm text-muted-foreground">
        {NO_MATCH_COPY}
      </p>
    );
  }
  return (
    <div data-agent-overview="" className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-3">
      <SquareLegend />
      {people.map((p) => {
        const open = finding || shown.has(p.person);
        const sessions = p.quiet.reduce((sum, t) => sum + t.sessions.length, 0);
        const tiles = open ? [...p.live, ...p.quiet] : p.live;
        return (
          <section key={p.person} aria-label={nameOf(p.person)} className="flex min-w-0 flex-col gap-2">
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <Button
                variant="ghost"
                size="none"
                onClick={() => onOpenPerson(p.person)}
                layout="flex min-w-0 items-center gap-2 rounded-md py-0.5 pr-2"
              >
                <PersonAvatar email={p.person} displayName={nameOf(p.person)} size={24} />
                <span className="min-w-0 truncate text-sm font-semibold text-foreground">{nameOf(p.person)}</span>
              </Button>
              <TileCounts counts={p.counts} />
            </div>
            {tiles.length > 0 ? (
              <div className="grid items-start gap-3" style={GRID}>
                {tiles.map((tile) => (
                  <TileCard key={tile.key} tile={tile} nowMs={nowMs} onOpen={onOpenTile} onPick={onPickSession} />
                ))}
              </div>
            ) : null}
            {p.quiet.length > 0 && !open ? (
              <Button
                variant="secondary"
                size="sm"
                icon="CirclePause"
                data-agent-quiet-row=""
                onClick={() => setShown((prev) => new Set(prev).add(p.person))}
                layout="flex min-h-9 w-full items-center justify-start gap-1.5 rounded-lg px-3"
              >
                {p.quiet.length} quiet {p.quiet.length === 1 ? "orchestrator" : "orchestrators"}, {sessions}{" "}
                {lensWord("idle").toLowerCase()} or {lensWord("done").toLowerCase()}{" "}
                {sessions === 1 ? "session" : "sessions"} · Show
              </Button>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

export default AgentOverview;
