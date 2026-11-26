"use client";

/**
 * The Agents view on a phone (WS-MCP AMR-3, `agent_coordination.md` §5.12).
 *
 * Reading this as: one level on each screen. First three counts, then the
 * questions that wait on the member, oldest first, then the people. A person
 * opens their orchestrators, and an orchestrator opens its sessions. The graph
 * never draws on a phone.
 *
 * ⚠️ A question is answered through the one list of agent questions
 * (`QuestionsView`, the card `AgentQuestionCard`). This view adds no answer
 * path. A session opens in the bottom sheet of the shared `Modal`.
 */
import { useState } from "react";

import Icon from "@/components/Icon";
import { PersonAvatar } from "@/components/room/Identity";
import Button from "@/components/ui/Button";

import {
  type LensModel,
  type LensPerson,
  type LensSession,
  type LensState,
  activeCount,
  compareSessions,
  countStates,
  foldWord,
  lensIcon,
  lensWord,
  machineWord,
  membersOf,
  personSessions,
} from "../lib/agentLenses";
import { NO_ORCHESTRATOR } from "./AgentBoardList";
import { CountLine, FoldRow, SessionCard, pressKeys } from "./AgentSessionCard";
import { QuestionsView } from "./QuestionsView";

/** One level of the phone view. An empty path is the first screen. */
export type PhoneStep =
  | { kind: "state"; state: LensState }
  | { kind: "person"; person: string }
  | { kind: "group"; person: string; key: string };

/** How many questions the first screen draws before "Show all". */
export const PHONE_QUESTIONS = 3;

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
}

const DOT: Record<Exclude<LensState, "idle">, string> = {
  you: "bg-warning",
  working: "bg-primary",
  waiting: "bg-muted-foreground/60",
};
const DOTS_EACH = 6;

/** One dot for each session that is not idle, at most six of a state. */
function Dots({ sessions }: { sessions: readonly LensSession[] }) {
  const counts = countStates(sessions);
  return (
    <span aria-hidden className="ml-auto flex shrink-0 gap-0.5">
      {(["you", "working", "waiting"] as const).flatMap((state) =>
        Array.from({ length: Math.min(counts[state], DOTS_EACH) }, (_, i) => (
          <i key={`${state}${i}`} className={`size-1.5 rounded-full ${DOT[state]}`} />
        )),
      )}
    </span>
  );
}

function Row({ onOpen, label, children }: { onOpen: () => void; label: string; children: React.ReactNode }) {
  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        aria-label={label}
        onClick={onOpen}
        onKeyDown={pressKeys(onOpen)}
        data-agent-phone-row=""
        className="flex min-h-14 min-w-0 cursor-pointer items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2 shadow-sm"
      >
        {children}
        <Icon name="ChevronRight" size={15} className="shrink-0 text-muted-foreground" aria-hidden />
      </div>
    </li>
  );
}

export function AgentsPhone(props: AgentsPhoneProps) {
  const { model, nowMs, nameOf, onOpenSession } = props;
  const [path, setPath] = useState<PhoneStep[]>(props.initialPath ?? []);
  const [opened, setOpened] = useState<ReadonlySet<string>>(() => new Set());
  const step = path.at(-1) ?? null;
  const go = (next: PhoneStep) => setPath((p) => [...p, next]);
  const back = () => setPath((p) => p.slice(0, -1));
  const counts = countStates(model.sessions);

  const cards = (sessions: readonly LensSession[]) =>
    sessions.map((s) => (
      <li key={s.id} className="h-16">
        <SessionCard session={s} nowMs={nowMs} onOpen={() => onOpenSession(s)} />
      </li>
    ));
  /** The sessions that are not idle, then one line for the idle ones. */
  const withIdleFold = (sessions: readonly LensSession[], key: string) => {
    const open = opened.has(key);
    const shown = sessions.filter((s) => open || s.state !== "idle");
    const folded = sessions.filter((s) => !open && s.state === "idle");
    return (
      <ul className="flex flex-col gap-2">
        {cards(shown)}
        {folded.length > 0 ? (
          <li key="fold">
            <FoldRow word={foldWord(folded)} onShow={() => setOpened((prev) => new Set(prev).add(key))} />
          </li>
        ) : null}
      </ul>
    );
  };

  const stepWord = (s: PhoneStep | undefined): string => {
    if (!s) return "Agents";
    if (s.kind === "state") return lensWord(s.state);
    return nameOf(s.person);
  };

  const person: LensPerson | undefined =
    step && step.kind !== "state" ? model.people.find((p) => p.person === step.person) : undefined;
  const group = step?.kind === "group" ? person?.groups.find((g) => g.key === step.key) : undefined;

  let title = "Agents";
  let sub = "";
  let body: React.ReactNode;
  if (!step) {
    body = (
      <>
        <div className="grid grid-cols-3 gap-2">
          {(["you", "working", "waiting"] as const).map((state) => (
            <Button
              key={state}
              variant="secondary"
              size="none"
              data-agent-phone-tile={state}
              aria-label={`${counts[state]} ${lensWord(state)}`}
              onClick={() => {
                if (state === "you" && props.questionsOn) {
                  document.querySelector("[data-agent-phone-questions]")?.scrollIntoView({ block: "start" });
                } else go({ kind: "state", state });
              }}
              layout="flex min-h-16 min-w-0 flex-col items-start justify-center gap-0.5 rounded-lg bg-card px-3 py-2"
            >
              <span className={`text-xl font-bold leading-tight ${state === "you" ? "text-warning" : "text-foreground"}`}>
                {counts[state]}
              </span>
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Icon name={lensIcon(state)} size={12} aria-hidden />
                {lensWord(state)}
              </span>
            </Button>
          ))}
        </div>
        {counts.idle > 0 ? (
          <div>
            <Button variant="text" size="sm" icon={lensIcon("idle")} onClick={() => go({ kind: "state", state: "idle" })}>
              {counts.idle === 1 ? "1 idle session" : `${counts.idle} idle sessions`}
            </Button>
          </div>
        ) : null}
        {props.questionsOn ? (
          <section aria-label="Questions for you" data-agent-phone-questions="" className="flex flex-col gap-2">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
              <Icon name={lensIcon("you")} size={15} className="text-warning" aria-hidden />
              Questions for you
              <span className="font-normal text-muted-foreground">oldest first</span>
            </h3>
            <QuestionsView
              embedded
              projects={[]}
              fixedProject={props.projectId}
              initialShown={PHONE_QUESTIONS}
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
              const all = personSessions(p);
              const c = countStates(all);
              return (
                <Row key={p.person} label={`${nameOf(p.person)}. ${activeCount(all)} active.`} onOpen={() => go({ kind: "person", person: p.person })}>
                  <PersonAvatar email={p.person} displayName={nameOf(p.person)} size={32} />
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate text-sm font-semibold text-foreground">{nameOf(p.person)}</span>
                    <span className="flex gap-2 text-[11px] text-muted-foreground">
                      <span>{activeCount(all)} active</span>
                      {c.you > 0 ? <span className="text-warning">{c.you} need you</span> : null}
                    </span>
                  </span>
                  <Dots sessions={all} />
                </Row>
              );
            })}
          </ul>
        </section>
      </>
    );
  } else if (step.kind === "state") {
    const list = model.sessions.filter((s) => s.state === step.state).sort(compareSessions);
    title = lensWord(step.state);
    sub = list.length === 1 ? "1 session" : `${list.length} sessions`;
    body = <ul className="flex flex-col gap-2">{cards(list)}</ul>;
  } else if (step.kind === "person" && person) {
    const own = person.groups.find((g) => !g.orch);
    title = nameOf(person.person);
    sub = `${activeCount(personSessions(person))} active`;
    body = (
      <>
        <ul className="flex flex-col gap-2">
          {person.groups.map((g) =>
            g.orch ? (
              <Row key={g.key} label={`Orchestrator ${g.orch.name}`} onOpen={() => go({ kind: "group", person: person.person, key: g.key })}>
                <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
                  <Icon name="Bot" size={16} aria-hidden />
                </span>
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm font-semibold text-foreground">{g.orch.name}</span>
                  <CountLine counts={countStates(membersOf(g))} />
                </span>
                <Dots sessions={membersOf(g)} />
              </Row>
            ) : null,
          )}
        </ul>
        {own ? (
          <section aria-label={NO_ORCHESTRATOR} className="flex flex-col gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">{NO_ORCHESTRATOR}</h3>
            {withIdleFold(own.sessions, own.key)}
          </section>
        ) : null}
      </>
    );
  } else if (step.kind === "group" && group?.orch) {
    title = group.orch.name;
    sub = `Orchestrator · ${machineWord(group.orch)}`;
    body = (
      <>
        <ul>{cards([group.orch])}</ul>
        <section aria-label="Sessions it dispatched" className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-muted-foreground">Sessions it dispatched</h3>
          {withIdleFold(group.sessions, group.key)}
        </section>
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
