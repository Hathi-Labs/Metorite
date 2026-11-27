"use client";

/**
 * The Agents view on a phone (WS-MCP AMR-3 and AMR-4, `agent_coordination.md`
 * §3.12.8 and §3.12.9).
 *
 * Reading this as: one level on each screen. First a find box, four counts, one
 * question at a time and the people with their squares. A person opens their
 * orchestrators. An orchestrator opens its sessions by state, ten at a time,
 * with the idle and done ones as rows that open. The graph never draws on a
 * phone.
 *
 * ⚠️ A question is answered through the one list of agent questions
 * (`QuestionsView`, the card `AgentQuestionCard`). This view adds no answer
 * path. A session opens in the bottom sheet of the shared `Modal`.
 */
import { useState } from "react";

import Icon from "@/components/Icon";
import { PersonAvatar } from "@/components/room/Identity";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

import {
  type LensCounts,
  type LensModel,
  type LensSession,
  type LensState,
  LENS_STATES,
  countStates,
  lensIcon,
  lensWord,
  machineWord,
  matchesFind,
  membersOf,
} from "../lib/agentLenses";
import { PHONE_MORE, PHONE_PAGE, WINDOW_ABOVE, allSessionsOf, attention, rankSessions, subTotals } from "../lib/agentScale";
import { useWindowedList } from "../lib/windowedList";
import { SessionCard, pressKeys } from "./AgentSessionCard";
import { NO_MATCH_COPY, NO_ORCHESTRATOR, Squares, SubBar, TileCounts } from "./AgentSquares";
import { QuestionsView } from "./QuestionsView";

/** One level of the phone view. An empty path is the first screen. */
export type PhoneStep =
  | { kind: "state"; state: LensState; key?: string; person?: string }
  | { kind: "person"; person: string }
  | { kind: "group"; person: string; key: string };

/** The counts of the first screen, in order. */
export const PHONE_TILES: readonly LensState[] = ["you", "failed", "working", "waiting"];
/** The squares of one row. */
const ROW_SQUARES = 60;
export const PHONE_FIND_LABEL = "Find a session, task or subagent";

export interface AgentsPhoneProps {
  model: LensModel;
  nowMs: number;
  nameOf: (email: string) => string;
  onOpenSession: (session: LensSession) => void;
  onOpenTask: (taskId: string) => void;
  onOpenProject: (projectId: string) => void;
  projectId?: string;
  personLabels?: ReadonlyMap<string, string>;
  /** False when the questions list is off for this org. The sessions then draw. */
  questionsOn: boolean;
  /** An answer landed. The view reads the map again. */
  onAnswered?: () => void;
  initialPath?: PhoneStep[];
  /** How many cards each list shows at the start, by its key. A test gives it. */
  initialPages?: Record<string, number>;
  find?: string;
  onFind?: (find: string) => void;
}

/** The height of one card of a long list with the gap below it, in rem. */
const CARD_ROW_REM = 4.5;

/**
 * The cards of one list, then "Show more". Past `WINDOW_ABOVE` cards it draws
 * only the cards on screen. The page of the phone view scrolls, not the list.
 */
function SessionPage({
  sessions,
  shown,
  nowMs,
  onOpenSession,
  onMore,
}: {
  sessions: readonly LensSession[];
  shown: number;
  nowMs: number;
  onOpenSession: (session: LensSession) => void;
  onMore: () => void;
}) {
  const cards = sessions.slice(0, shown);
  const rest = sessions.length - cards.length;
  const long = cards.length > WINDOW_ABOVE;
  const { attach, start, end, before, after } = useWindowedList(long ? cards.map(() => CARD_ROW_REM) : [], {
    inPage: true,
  });
  return (
    <div className="flex flex-col gap-2">
      <ul
        ref={long ? attach : undefined}
        className={long ? "" : "flex flex-col gap-2"}
        style={long ? { paddingTop: `${before}rem`, paddingBottom: `${after}rem` } : undefined}
        data-agent-phone-window={long ? "" : undefined}
      >
        {(long ? cards.slice(start, end) : cards).map((s) => (
          <li key={s.id} className={long ? "" : "h-16"} style={long ? { height: `${CARD_ROW_REM}rem` } : undefined}>
            <div className="h-16">
              <SessionCard session={s} nowMs={nowMs} onOpen={() => onOpenSession(s)} />
            </div>
          </li>
        ))}
      </ul>
      {rest > 0 ? (
        <Button
          variant="secondary"
          layout="flex h-10 w-full items-center justify-center rounded-lg"
          data-agent-phone-more=""
          onClick={onMore}
        >
          Show {Math.min(PHONE_MORE, rest)} more{rest > PHONE_MORE ? ` of ${rest}` : ""}
        </Button>
      ) : null}
    </div>
  );
}

/** A row that opens a level: its head, then its squares. */
function Row({ onOpen, label, head, children }: { onOpen: () => void; label: string; head: React.ReactNode; children?: React.ReactNode }) {
  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        aria-label={label}
        onClick={onOpen}
        onKeyDown={pressKeys(onOpen)}
        data-agent-phone-row=""
        className="flex min-h-14 min-w-0 cursor-pointer flex-col justify-center gap-1.5 rounded-lg border border-border bg-card px-3 py-2 shadow-sm"
      >
        <span className="flex min-w-0 items-center gap-2.5">
          {head}
          <Icon name="ChevronRight" size={15} className="ml-auto shrink-0 text-muted-foreground" aria-hidden />
        </span>
        {children}
      </div>
    </li>
  );
}

const countLabel = (counts: LensCounts): string =>
  LENS_STATES.filter((s) => counts[s] > 0)
    .map((s) => `${counts[s]} ${lensWord(s).toLowerCase()}`)
    .join(", ");

export function AgentsPhone(props: AgentsPhoneProps) {
  const { model, nowMs, nameOf, onOpenSession, find = "" } = props;
  const [path, setPath] = useState<PhoneStep[]>(props.initialPath ?? []);
  /** How many cards a list shows, by its key. */
  const [pages, setPages] = useState<Record<string, number>>(props.initialPages ?? {});
  const step = path.at(-1) ?? null;
  const go = (next: PhoneStep) => setPath((p) => [...p, next]);
  const back = () => setPath((p) => p.slice(0, -1));
  const match = (list: readonly LensSession[]) => list.filter((s) => matchesFind(s, find, nameOf(s.person)));
  const all = match(model.sessions);
  const counts = countStates(all);

  /** Ten cards, then "Show more". */
  const paged = (sessions: readonly LensSession[], key: string) => {
    const shown = pages[key] ?? PHONE_PAGE;
    return (
      <SessionPage
        key={key}
        sessions={sessions}
        shown={shown}
        nowMs={nowMs}
        onOpenSession={onOpenSession}
        onMore={() => setPages((p) => ({ ...p, [key]: shown + PHONE_MORE }))}
      />
    );
  };

  const person = step && "person" in step ? model.people.find((p) => p.person === step.person) : undefined;
  const group = step && "key" in step ? person?.groups.find((g) => g.key === step.key) : undefined;
  const groupName = (g: { orch: LensSession | null }) => g.orch?.name ?? NO_ORCHESTRATOR;

  const stepWord = (s: PhoneStep | undefined): string => {
    if (!s) return "Agents";
    if (s.kind === "state") return lensWord(s.state);
    if (s.kind === "person") return nameOf(s.person);
    const found = model.people.find((p) => p.person === s.person)?.groups.find((g) => g.key === s.key);
    return found ? groupName(found) : nameOf(s.person);
  };

  const sessionsWord = (n: number) => `${n} ${n === 1 ? "session" : "sessions"}`;
  let title = "Agents";
  let sub = "";
  let body: React.ReactNode;
  if (!step) {
    body = (
      <>
        <Input
          icon="Search"
          value={find}
          onChange={(e) => props.onFind?.(e.target.value)}
          placeholder={PHONE_FIND_LABEL}
          aria-label={PHONE_FIND_LABEL}
        />
        <div className="grid grid-cols-2 gap-2">
          {PHONE_TILES.map((state) => (
            <Button
              key={state}
              variant="secondary"
              size="none"
              data-agent-phone-tile={state}
              aria-label={`${counts[state]} ${lensWord(state)}`}
              onClick={() => go({ kind: "state", state })}
              layout="flex min-h-16 min-w-0 flex-col items-start justify-center gap-0.5 rounded-lg bg-card px-3 py-2"
            >
              <span
                className={`text-xl font-bold leading-tight ${state === "you" ? "text-warning" : state === "failed" ? "text-destructive" : "text-foreground"}`}
              >
                {counts[state]}
              </span>
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Icon name={lensIcon(state)} size={12} aria-hidden />
                {lensWord(state)}
              </span>
            </Button>
          ))}
        </div>
        {counts.idle + counts.done > 0 ? (
          <div className="flex gap-3">
            {(["idle", "done"] as const).map((state) =>
              counts[state] > 0 ? (
                <Button key={state} variant="text" size="sm" icon={lensIcon(state)} onClick={() => go({ kind: "state", state })}>
                  {counts[state]} {lensWord(state).toLowerCase()}
                </Button>
              ) : null,
            )}
          </div>
        ) : null}
        {find.trim() ? (
          <section aria-label="Matches" className="flex flex-col gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">{sessionsWord(all.length)} found</h3>
            {all.length > 0 ? paged(rankSessions(all), `find:${find}`) : <p className="text-sm text-muted-foreground">{NO_MATCH_COPY}</p>}
          </section>
        ) : null}
        {props.questionsOn && !find.trim() ? (
          <section aria-label="Questions for you" data-agent-phone-questions="" className="flex flex-col gap-2">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
              <Icon name={lensIcon("you")} size={15} className="text-warning" aria-hidden />
              Questions for you
              <span className="font-normal text-muted-foreground">oldest first</span>
            </h3>
            <QuestionsView
              embedded
              pager
              projects={[]}
              fixedProject={props.projectId}
              personLabels={props.personLabels}
              onOpenTask={props.onOpenTask}
              onOpenProject={props.onOpenProject}
              onChanged={props.onAnswered}
            />
          </section>
        ) : null}
        <section aria-label="People" className="flex flex-col gap-2">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
            <Icon name="Users" size={15} className="text-muted-foreground" aria-hidden />
            People
          </h3>
          <ul className="flex flex-col gap-2">
            {model.people.map((p) => {
              const own = rankSessions(match(allSessionsOf(model, p.person)));
              if (own.length === 0) return null;
              const c = countStates(own);
              return (
                <Row
                  key={p.person}
                  label={`${nameOf(p.person)}. ${countLabel(c)}.`}
                  onOpen={() => go({ kind: "person", person: p.person })}
                  head={
                    <>
                      <PersonAvatar email={p.person} displayName={nameOf(p.person)} size={32} />
                      <span className="flex min-w-0 flex-col">
                        <span className="truncate text-sm font-semibold text-foreground">{nameOf(p.person)}</span>
                        <TileCounts counts={c} />
                      </span>
                    </>
                  }
                >
                  <Squares sessions={own} nowMs={nowMs} max={ROW_SQUARES} />
                </Row>
              );
            })}
          </ul>
        </section>
      </>
    );
  } else if (step.kind === "state") {
    const base = group ? membersOf(group) : model.sessions;
    const list = rankSessions(match(base).filter((s) => s.state === step.state));
    title = group ? `${lensWord(step.state)} · ${groupName(group)}` : lensWord(step.state);
    sub = sessionsWord(list.length);
    body = paged(list, `${step.key ?? ""}:${step.state}`);
  } else if (step.kind === "person" && person) {
    title = nameOf(person.person);
    sub = `${person.groups.filter((g) => g.orch).length} orchestrators`;
    const rows = person.groups
      .map((g) => ({ g, sessions: rankSessions(match(membersOf(g))) }))
      .filter((r) => r.sessions.length > 0)
      // The orchestrator that wants the most attention comes first, as on the PC.
      .sort((a, b) => attention(countStates(b.sessions)) - attention(countStates(a.sessions)));
    body = (
      <ul className="flex flex-col gap-2">
        {rows.map(({ g, sessions }) => {
          const c = countStates(sessions);
          return (
            <Row
              key={g.key}
              label={`${g.orch ? `Orchestrator ${g.orch.name}` : NO_ORCHESTRATOR}. ${countLabel(c)}.`}
              onOpen={() => go({ kind: "group", person: person.person, key: g.key })}
              head={
                <>
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
                    <Icon name="Bot" size={16} aria-hidden />
                  </span>
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate text-sm font-semibold text-foreground">{groupName(g)}</span>
                    <TileCounts counts={c} />
                  </span>
                </>
              }
            >
              <Squares sessions={sessions} nowMs={nowMs} max={ROW_SQUARES} />
            </Row>
          );
        })}
      </ul>
    );
  } else if (step.kind === "group" && group) {
    const members = rankSessions(match(membersOf(group)));
    const orch = group.orch;
    const rest = members.filter((s) => s.id !== orch?.id);
    title = groupName(group);
    sub = orch ? `Orchestrator · ${machineWord(orch)}` : sessionsWord(members.length);
    body = (
      <>
        <div className="flex flex-col gap-1.5">
          <TileCounts counts={countStates(members)} />
          <Squares sessions={members} nowMs={nowMs} />
          <SubBar subs={subTotals(members)} />
        </div>
        {orch && members.some((s) => s.id === orch.id) ? (
          <ul>
            <li className="h-16">
              <SessionCard session={orch} nowMs={nowMs} onOpen={() => onOpenSession(orch)} />
            </li>
          </ul>
        ) : null}
        {PHONE_TILES.map((state) => {
          const list = rest.filter((s) => s.state === state);
          if (list.length === 0) return null;
          return (
            <section key={state} aria-label={lensWord(state)} className="flex flex-col gap-2" data-agent-phone-section={state}>
              <h3 className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Icon name={lensIcon(state)} size={12} aria-hidden />
                {lensWord(state)} · {list.length}
              </h3>
              {paged(list, `${group.key}:${state}`)}
            </section>
          );
        })}
        <ul className="flex flex-col gap-2">
          {(["idle", "done"] as const).map((state) => {
            const n = rest.filter((s) => s.state === state).length;
            if (n === 0) return null;
            return (
              <Row
                key={state}
                label={`${n} ${lensWord(state).toLowerCase()}`}
                onOpen={() => go({ kind: "state", state, key: group.key, person: step.person })}
                head={
                  <>
                    <Icon name={lensIcon(state)} size={15} className="shrink-0 text-muted-foreground" aria-hidden />
                    <span className="text-sm text-foreground">
                      {n} {lensWord(state).toLowerCase()}
                    </span>
                  </>
                }
              />
            );
          })}
        </ul>
      </>
    );
  } else {
    // The level left the map on a read: say so, and keep the way back.
    body = <p className="text-sm text-muted-foreground">No agent session is here now.</p>;
  }

  return (
    <div data-agent-phone="" className="min-h-0 flex-1 overflow-y-auto px-3 pb-6 pt-2">
      <div className="flex flex-col gap-3">
        {step ? (
          <div>
            <Button variant="text" size="sm" icon="ChevronLeft" onClick={back} data-agent-phone-back="">
              {stepWord(path.at(-2))}
            </Button>
          </div>
        ) : null}
        {step ? (
          <div className="flex min-w-0 items-baseline gap-2">
            <h2 className="min-w-0 truncate text-base font-semibold text-foreground">{title}</h2>
            <span className="shrink-0 text-xs text-muted-foreground">{sub}</span>
          </div>
        ) : null}
        {body}
      </div>
    </div>
  );
}

export default AgentsPhone;
