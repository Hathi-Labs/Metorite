"use client";

/**
 * The Agents view · the Board and List lenses (WS-MCP AMR-2,
 * `agent_coordination.md` §5.12). The same model and the same card as the
 * Graph. The Board is one column for each person. The List is an indented
 * outline: person and orchestrator, then each session. Read only.
 *
 * AMR-4 (§3.12.9): one orchestrator draws a bounded board, and a List past 50
 * rows draws only the rows on screen.
 */
import { useMemo } from "react";

import Icon from "@/components/Icon";
import { PersonAvatar } from "@/components/room/Identity";
import Button from "@/components/ui/Button";

import {
  type LensFilters,
  type LensGroup,
  type LensModel,
  type LensSession,
  type LensState,
  activeCount,
  foldWord,
  lensWord,
  matchesFind,
  personSessions,
  sessionLine,
  splitSessions,
  subWords,
} from "../lib/agentLenses";
import { type BucketWindows, WINDOW_ABOVE, boundSessions, bucketWord } from "../lib/agentScale";
import { useWindowedList } from "../lib/windowedList";
import { FoldRow, SessionCard, StatePill, SubCount, pressKeys, sessionSpoken } from "./AgentSessionCard";
import { BucketRow, NO_MATCH_COPY, NO_ORCHESTRATOR, Squares } from "./AgentSquares";

export { BucketRow, NO_MATCH_COPY, NO_ORCHESTRATOR };

export interface LensViewProps {
  model: LensModel;
  filters: LensFilters;
  opened: ReadonlySet<string>;
  onOpenGroup: (key: string) => void;
  selectedId: string | null;
  onOpenSession: (session: LensSession) => void;
  onOpenPerson: (person: string) => void;
  nowMs: number;
  nameOf: (email: string) => string;
  /** AMR-4: keep one orchestrator group only. */
  onlyGroup?: string | null;
}

/** Each person with the groups that the filters leave something in. */
function visiblePeople({ model, filters, opened, nameOf, onlyGroup }: LensViewProps) {
  const find = filters.find.trim();
  return model.people
    .filter((p) => !filters.person || p.person === filters.person)
    .map((p) => ({
      person: p.person,
      active: activeCount(personSessions(p)),
      groups: p.groups
        .filter((group) => !onlyGroup || group.key === onlyGroup)
        .map((group) => ({
          group,
          orch:
            group.orch && (!find || matchesFind(group.orch, find, nameOf(group.orch.person))) ? group.orch : null,
          split: splitSessions(group.sessions, filters, opened.has(group.key), nameOf),
        }))
        .filter((g) => g.orch || g.split.shown.length > 0 || g.split.folded.length > 0),
    }))
    .filter((p) => p.groups.length > 0);
}

function Empty() {
  return (
    <p data-agent-lens-empty="" className="p-4 text-sm text-muted-foreground">
      {NO_MATCH_COPY}
    </p>
  );
}

export function AgentBoardLens(props: LensViewProps) {
  const { nowMs, nameOf, selectedId, onOpenSession, onOpenPerson, onOpenGroup } = props;
  const people = visiblePeople(props);
  if (people.length === 0) return <Empty />;
  const card = (s: LensSession) => (
    <li key={s.id} className="h-16">
      <SessionCard session={s} nowMs={nowMs} selected={selectedId === `s:${s.id}`} onOpen={() => onOpenSession(s)} />
    </li>
  );
  return (
    <div data-agent-board="" className="min-h-0 flex-1 overflow-auto p-3">
      <div className="grid items-start gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(18rem, 100%), 1fr))" }}>
        {people.map((p) => (
          <section key={p.person} aria-label={nameOf(p.person)} className="flex min-w-0 flex-col gap-2">
            <Button variant="ghost" size="none" onClick={() => onOpenPerson(p.person)} layout="flex w-full items-center justify-start gap-2 rounded-md px-1 py-1">
              <PersonAvatar email={p.person} displayName={nameOf(p.person)} size={24} />
              <span className="min-w-0 truncate text-sm font-semibold text-foreground">{nameOf(p.person)}</span>
              <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{p.active} active</span>
            </Button>
            {p.groups.map(({ group, orch, split }) => (
              <div key={group.key} className="flex flex-col gap-2">
                <h4 className="flex items-center gap-1.5 px-1 pt-1 text-[11px] font-medium text-muted-foreground">
                  <Icon name="Bot" size={12} aria-hidden />
                  <span className="truncate">{group.orch ? group.orch.name : NO_ORCHESTRATOR}</span>
                </h4>
                <ul className="flex flex-col gap-2">
                  {orch ? card(orch) : null}
                  {split.shown.map(card)}
                  {split.folded.length > 0 ? (
                    <li key="fold">
                      <FoldRow word={foldWord(split.folded)} onShow={() => onOpenGroup(group.key)} />
                    </li>
                  ) : null}
                </ul>
              </div>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

type ListRow =
  | { kind: "head"; key: string; group: LensGroup; person: string }
  | { kind: "line"; key: string; session: LensSession; indent: boolean }
  | { kind: "fold"; key: string; groupKey: string; word: string };

/** The row heights, in rem. The window sums them at the applied root size. */
const ROW_REM = { head: 2.25, line: 2.75, fold: 1.75 } as const;

export function AgentListLens(props: LensViewProps) {
  const { nowMs, nameOf, selectedId, onOpenSession, onOpenGroup } = props;
  const people = visiblePeople(props);
  const rows: ListRow[] = people.flatMap((p) =>
    p.groups.flatMap(({ group, orch, split }): ListRow[] => [
      { kind: "head", key: `h:${group.key}`, group, person: p.person },
      ...(orch ? [{ kind: "line", key: orch.id, session: orch, indent: false } as const] : []),
      ...split.shown.map((s) => ({ kind: "line", key: s.id, session: s, indent: true }) as const),
      ...(split.folded.length > 0
        ? [{ kind: "fold", key: `f:${group.key}`, groupKey: group.key, word: foldWord(split.folded) } as const]
        : []),
    ]),
  );
  const { attach, start, end, before, after } = useWindowedList(rows.map((r) => ROW_REM[r.kind]));
  if (people.length === 0) return <Empty />;
  const draw = (row: ListRow) => {
    const style = { height: `${ROW_REM[row.kind]}rem` };
    if (row.kind === "head") {
      return (
        <li key={row.key} style={style} className="flex min-w-0 items-end gap-2 pb-1 text-sm font-semibold text-foreground">
          <Icon name="Bot" size={15} className="shrink-0 text-muted-foreground" aria-hidden />
          <h4 className="truncate">{row.group.orch ? row.group.orch.name : NO_ORCHESTRATOR}</h4>
          <span className="shrink-0 text-xs font-normal text-muted-foreground">
            {nameOf(row.person)}
            {row.group.orch ? ` · dispatched ${row.group.sessions.length}` : ""}
          </span>
        </li>
      );
    }
    if (row.kind === "fold") {
      return (
        <li key={row.key} style={style} className="flex items-center pl-6">
          <Button variant="text" size="none" onClick={() => onOpenGroup(row.groupKey)}>
            {row.word} · Show
          </Button>
        </li>
      );
    }
    const s = row.session;
    return (
      <li key={row.key} style={style}>
        <div
          role="button"
          tabIndex={0}
          aria-label={sessionSpoken(s, nowMs)}
          aria-pressed={selectedId === `s:${s.id}`}
          onClick={() => onOpenSession(s)}
          onKeyDown={pressKeys(() => onOpenSession(s))}
          data-agent-list-row={s.state}
          className={`flex h-full min-w-0 cursor-pointer items-center gap-2 rounded-md pr-2 hover:bg-secondary ${row.indent ? "pl-6" : "pl-1"} ${selectedId === `s:${s.id}` ? "bg-primary/10" : ""}`}
        >
          {row.indent ? <Icon name="ChevronRight" size={13} className="shrink-0 text-muted-foreground" aria-hidden /> : null}
          <StatePill state={s.state} />
          <span className="flex min-w-0 flex-1 flex-col leading-tight">
            <span className="truncate text-sm font-medium text-foreground">{s.name}</span>
            <span className="truncate text-xs text-muted-foreground">{sessionLine(s, nowMs)}</span>
          </span>
          {subWords(s.subs) ? (
            <span className="hidden shrink-0 text-[11px] text-muted-foreground sm:inline">
              <SubCount session={s} />
            </span>
          ) : null}
          {s.host ? <span className="max-w-[30%] shrink-0 truncate text-[11px] text-muted-foreground">{s.host}</span> : null}
        </div>
      </li>
    );
  };
  const long = rows.length > WINDOW_ABOVE;
  return (
    <div ref={attach} data-agent-list="" className="min-h-0 flex-1 overflow-auto px-4 py-3">
      <ul style={long ? { paddingTop: `${before}rem`, paddingBottom: `${after}rem` } : undefined}>
        {(long ? rows.slice(start, end) : rows).map(draw)}
      </ul>
    </div>
  );
}

export interface BoundedBoardProps {
  person: string;
  group: LensGroup;
  filters: LensFilters;
  windows: BucketWindows;
  onBucket: (state: LensState, rest: number) => void;
  selectedId: string | null;
  onOpenSession: (session: LensSession) => void;
  nowMs: number;
  nameOf: (email: string) => string;
}

/** One orchestrator on the Board: its card, the bounded cards, then one row for each bucket. */
export function BoundedBoard(props: BoundedBoardProps) {
  const { group, filters, windows, selectedId, nowMs, nameOf, onOpenSession, onBucket } = props;
  const pin = selectedId?.startsWith("s:") ? selectedId.slice(2) : null;
  const { shown, buckets } = useMemo(
    () =>
      boundSessions(group.sessions, {
        states: filters.states,
        find: filters.find,
        nameOf,
        windows,
        pin: pin === group.orch?.id ? null : pin,
      }),
    [group, filters, windows, nameOf, pin],
  );
  const card = (s: LensSession) => (
    <li key={s.id} className="h-16">
      <SessionCard session={s} nowMs={nowMs} selected={selectedId === `s:${s.id}`} onOpen={() => onOpenSession(s)} />
    </li>
  );
  const grid = { gridTemplateColumns: "repeat(auto-fill, minmax(min(18rem, 100%), 1fr))" };
  return (
    <div data-agent-board="bounded" className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
      {group.orch ? (
        <ul className="grid gap-3" style={grid}>
          {card(group.orch)}
        </ul>
      ) : null}
      <ul className="grid items-start gap-3" style={grid}>
        {shown.map(card)}
      </ul>
      {buckets.map((bucket) => (
        <BucketRow
          key={bucket.state}
          word={bucketWord(bucket, lensWord(bucket.state), shown.filter((s) => s.state === bucket.state).length)}
          sessions={bucket.sessions}
          nowMs={nowMs}
          onOpen={() => onBucket(bucket.state, bucket.rest)}
          onPick={onOpenSession}
        />
      ))}
      {shown.length === 0 && buckets.length === 0 && !group.orch ? <Empty /> : null}
    </div>
  );
}
