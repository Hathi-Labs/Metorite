"use client";

/**
 * The Agents view · the Board and List lenses (WS-MCP AMR-2,
 * `agent_coordination.md` §5.12). The same model and the same card as the
 * Graph. The Board is one column for each person. The List is an indented
 * outline: person and orchestrator, then each session. Read only.
 */
import Icon from "@/components/Icon";
import { PersonAvatar } from "@/components/room/Identity";
import Button from "@/components/ui/Button";

import {
  type LensFilters,
  type LensModel,
  type LensSession,
  activeCount,
  foldWord,
  matchesFind,
  personSessions,
  sessionLine,
  splitSessions,
} from "../lib/agentLenses";
import { FoldRow, SessionCard, StatePill, pressKeys, sessionSpoken } from "./AgentSessionCard";

export const NO_MATCH_COPY = "No session matches these filters.";
export const NO_ORCHESTRATOR = "No orchestrator";

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
}

/** Each person with the groups that the filters leave something in. */
function visiblePeople({ model, filters, opened, nameOf }: LensViewProps) {
  const find = filters.find.trim();
  return model.people
    .filter((p) => !filters.person || p.person === filters.person)
    .map((p) => ({
      person: p.person,
      active: activeCount(personSessions(p)),
      groups: p.groups
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

export function AgentListLens(props: LensViewProps) {
  const { nowMs, nameOf, selectedId, onOpenSession, onOpenGroup } = props;
  const people = visiblePeople(props);
  if (people.length === 0) return <Empty />;
  const line = (s: LensSession, indent: boolean) => (
    <li key={s.id}>
      <div
        role="button"
        tabIndex={0}
        aria-label={sessionSpoken(s, nowMs)}
        aria-pressed={selectedId === `s:${s.id}`}
        onClick={() => onOpenSession(s)}
        onKeyDown={pressKeys(() => onOpenSession(s))}
        data-agent-list-row={s.state}
        className={`flex min-w-0 cursor-pointer items-center gap-2 rounded-md py-1 pr-2 hover:bg-secondary ${indent ? "pl-6" : "pl-1"} ${selectedId === `s:${s.id}` ? "bg-primary/10" : ""}`}
      >
        {indent ? <Icon name="ChevronRight" size={13} className="shrink-0 text-muted-foreground" aria-hidden /> : null}
        <StatePill state={s.state} />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-sm font-medium text-foreground">{s.name}</span>
          <span className="truncate text-xs text-muted-foreground">{sessionLine(s, nowMs)}</span>
        </span>
        {s.host ? <span className="max-w-[30%] shrink-0 truncate text-[11px] text-muted-foreground">{s.host}</span> : null}
      </div>
    </li>
  );
  return (
    <div data-agent-list="" className="min-h-0 flex-1 overflow-auto px-4 py-3">
      <ul className="flex flex-col gap-3">
        {people.map((p) =>
          p.groups.map(({ group, orch, split }) => (
            <li key={group.key}>
              <h4 className="flex min-w-0 items-center gap-2 py-1 text-sm font-semibold text-foreground">
                <Icon name="Bot" size={15} className="shrink-0 text-muted-foreground" aria-hidden />
                <span className="truncate">{group.orch ? group.orch.name : NO_ORCHESTRATOR}</span>
                <span className="shrink-0 text-xs font-normal text-muted-foreground">
                  {nameOf(p.person)}
                  {group.orch ? ` · dispatched ${group.sessions.length}` : ""}
                </span>
              </h4>
              <ul>
                {orch ? line(orch, false) : null}
                {split.shown.map((s) => line(s, true))}
              </ul>
              {split.folded.length > 0 ? (
                <div className="pl-6">
                  <Button variant="text" size="none" onClick={() => onOpenGroup(group.key)}>
                    {foldWord(split.folded)} · Show
                  </Button>
                </div>
              ) : null}
            </li>
          )),
        )}
      </ul>
    </div>
  );
}
