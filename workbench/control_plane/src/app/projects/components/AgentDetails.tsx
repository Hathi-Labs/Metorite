"use client";

/**
 * The Agents view · the details of a selection (WS-MCP AMR-1).
 *
 * The same body fills the side dock on a desktop and the bottom sheet on a
 * phone. A session shows its facts, its tasks and the run panel of the picked
 * task (`AgentRunPanel`). Every act on an agent is a control of that panel:
 * Open session, the answer, the reply and Stop. This view adds none.
 *
 * AMR-4 (§3.12.9): the runs and the sub-agents of the open session come from
 * its own read, and the sub-agents list by state with the done ones folded.
 */
import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import { PersonAvatar } from "@/components/room/Identity";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { relativeTime } from "@/lib/taskCard";

import { type TaskRow, projectsApi } from "../lib/api";
import { type BlockKey, YOU_WORD, blockOf, blockSentence } from "../lib/agentGraph";
import {
  DISPATCHED_BY_LABEL,
  DISPATCHED_LABEL,
  OPEN_TASK_LABEL,
  sessionPlace,
} from "../lib/agentMap";
import {
  type LensModel,
  type LensPerson,
  type LensSession,
  LENS_STATES,
  compareSessions,
  countStates,
  lensIcon,
  lensRun,
  lensWord,
  personSessions,
  sessionLine,
  subRows,
} from "../lib/agentLenses";
import { MODEL_ICON, NOT_REPORTED, hostKindWord, hostSourceSentence } from "../lib/agentPlace";
import { waitingText } from "../lib/agentRuns";
import { WINDOW_ABOVE } from "../lib/agentScale";
import type { SessionRead } from "../lib/agentSessionReads";
import { useWindowedList } from "../lib/windowedList";
import { AgentRunPanel } from "./AgentRunPanel";
import { STATE_PILL, StatePill, pressKeys } from "./AgentSessionCard";
import { AgentSubagents } from "./AgentSubagents";

/** What the dock or the sheet shows. */
export type LensSelection =
  | { kind: "session"; id: string }
  | { kind: "person"; person: string }
  | { kind: "block"; block: BlockKey };

/** The node of the graph that a selection lights. */
export function selectionNodeId(selection: LensSelection | null): string | null {
  if (!selection) return null;
  if (selection.kind === "session") return `s:${selection.id}`;
  return selection.kind === "person" ? `p:${selection.person}` : `b:${selection.block}`;
}

/** The sessions that a "waits on" selection lists, the longest wait first. */
export function blockSessions(model: LensModel, block: BlockKey): LensSession[] {
  return model.sessions
    .filter((s) => blockOf(s) === block)
    .sort((a, b) => (a.lastAt ?? "").localeCompare(b.lastAt ?? ""));
}

/** The title of a selection, or `null` when it left the map. */
export function selectionTitle(
  model: LensModel,
  selection: LensSelection,
  nameOf: (email: string) => string,
): string | null {
  if (selection.kind === "session") return model.byId.get(selection.id)?.name ?? null;
  if (selection.kind === "person") {
    return model.people.some((p) => p.person === selection.person) ? nameOf(selection.person) : null;
  }
  return blockSessions(model, selection.block).length > 0 ? YOU_WORD : null;
}

export interface DetailsProps {
  model: LensModel;
  selection: LensSelection;
  onSelect: (next: LensSelection) => void;
  onOpenTask: (taskId: string) => void;
  personLabels?: ReadonlyMap<string, string>;
  nameOf: (email: string) => string;
  nowMs: number;
  /** AMR-4: the read of the selected session. Absent: only the map rows draw. */
  read?: SessionRead;
  onRetryRead?: () => void;
}

/** The height of one session row and of one task row, in rem. A long list sums it. */
const SESSION_ROW_REM = 2.75;
const TASK_ROW_REM = 2.5;

function Fact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-2 text-sm">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words text-foreground">{children}</dd>
    </div>
  );
}

function SessionList({
  sessions,
  nowMs,
  onSelect,
}: {
  sessions: readonly LensSession[];
  nowMs: number;
  onSelect: DetailsProps["onSelect"];
}) {
  const long = sessions.length > WINDOW_ABOVE;
  const { attach, start, end, before, after } = useWindowedList(long ? sessions.map(() => SESSION_ROW_REM) : []);
  const drawn = long ? sessions.slice(start, end) : sessions;
  const list = (
    <ul
      className={`divide-y divide-border ${long ? "" : "overflow-hidden rounded-lg border border-border"}`}
      style={long ? { paddingTop: `${before}rem`, paddingBottom: `${after}rem` } : undefined}
    >
      {drawn.map((s) => {
        const open = () => onSelect({ kind: "session", id: s.id });
        return (
          <li key={s.id} style={{ height: `${SESSION_ROW_REM}rem` }}>
            <div
              role="button"
              tabIndex={0}
              onClick={open}
              onKeyDown={pressKeys(open)}
              data-agent-detail-row={s.state}
              className="flex h-full min-w-0 cursor-pointer items-center gap-2 px-2.5 hover:bg-secondary"
            >
              <Icon
                name={lensIcon(s.state)}
                size={14}
                className={`shrink-0 ${s.state === "you" ? "text-warning" : "text-muted-foreground"}`}
                aria-label={lensWord(s.state)}
                role="img"
              />
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-sm font-medium text-foreground">{s.name}</span>
                <span className="truncate text-xs text-muted-foreground">{sessionLine(s, nowMs)}</span>
              </span>
              <span className="shrink-0 text-[11px] text-muted-foreground">{relativeTime(s.lastAt, nowMs)}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
  if (!long) return list;
  return (
    <div ref={attach} className="max-h-96 overflow-y-auto rounded-lg border border-border">
      {list}
    </div>
  );
}

/**
 * The tasks of one session. A session read gives up to 300 runs, so a long list
 * draws only the rows on screen.
 */
function TaskRows({
  runs,
  pickedId,
  askedOf,
  onPick,
  onOpenTask,
}: {
  runs: LensSession["runs"];
  pickedId: string | null;
  askedOf: string | null;
  onPick: (runId: string) => void;
  onOpenTask: (taskId: string) => void;
}) {
  const long = runs.length > WINDOW_ABOVE;
  const { attach, start, end, before, after } = useWindowedList(long ? runs.map(() => TASK_ROW_REM) : []);
  const list = (
    <ul
      className={`divide-y divide-border ${long ? "" : "overflow-hidden rounded-lg border border-border"}`}
      style={long ? { paddingTop: `${before}rem`, paddingBottom: `${after}rem` } : undefined}
    >
      {(long ? runs.slice(start, end) : runs).map((r) => (
        <li
          key={r.run_id}
          style={{ height: `${TASK_ROW_REM}rem` }}
          className="flex min-w-0 items-center gap-2 px-2.5"
          data-agent-task-row=""
        >
          <Button
            variant="text"
            size="none"
            selected={pickedId === r.run_id}
            onClick={() => onPick(r.run_id)}
            layout="flex min-w-0 flex-1 justify-start text-left"
          >
            <span className="truncate text-sm">
              {r.task_number === null ? "" : `#${r.task_number} `}
              {r.title}
            </span>
          </Button>
          <span className={`${STATE_PILL} ${lensRun(r, askedOf).chip}`}>{lensRun(r, askedOf).word}</span>
          <Button
            variant="ghost"
            size="icon-sm"
            icon="ArrowUpRight"
            aria-label={`${OPEN_TASK_LABEL}: ${r.title}`}
            title={OPEN_TASK_LABEL}
            onClick={() => onOpenTask(r.task_id)}
          />
        </li>
      ))}
    </ul>
  );
  if (!long) return list;
  return (
    <div ref={attach} className="max-h-96 overflow-y-auto rounded-lg border border-border" data-agent-task-window="">
      {list}
    </div>
  );
}

/** The run panel of one task. It reads the task, and the panel reads its run. */
function TaskRunPanel({ taskId, personLabels }: { taskId: string; personLabels?: ReadonlyMap<string, string> }) {
  const [read, setRead] = useState<{ id: string; task: TaskRow | null } | null>(null);
  useEffect(() => {
    let live = true;
    projectsApi
      .task(taskId)
      .then((task) => {
        if (live) setRead({ id: taskId, task });
      })
      .catch(() => {
        if (live) setRead({ id: taskId, task: null });
      });
    return () => {
      live = false;
    };
  }, [taskId]);
  if (read?.id !== taskId) return <Skeleton className="h-16 w-full" />;
  if (!read.task) {
    return (
      <p role="alert" className="text-xs text-foreground">
        The run of this task did not load. Open the task to see it.
      </p>
    );
  }
  return (
    <AgentRunPanel
      key={read.task.id}
      task={read.task}
      personLabels={personLabels}
      onTaskChanged={(fresh) => setRead({ id: taskId, task: fresh })}
    />
  );
}

/** The run that a session opens on: the one that asks, else the newest. */
export function firstRun(session: Pick<LensSession, "runs">): LensSession["runs"][number] | null {
  return session.runs.find((r) => r.state === "awaiting") ?? session.runs[0] ?? null;
}

function SessionDetails({ session, ...props }: DetailsProps & { session: LensSession }) {
  const { model, nameOf, nowMs, onSelect, onOpenTask, personLabels } = props;
  // The panel opens on the run that asks. The first choice stays, so a re-read
  // never swaps the panel (and a typed reply) under the member.
  const [picked, setPicked] = useState<string | null>(() => firstRun(session)?.run_id ?? null);
  const read = props.read;
  // AMR-4: the summary map holds one lead run. The read of the session holds each.
  const runs =
    read?.kind === "ready" && read.runs.length > 0
      ? [...read.runs].sort((a, b) => Number(a.quiet) - Number(b.quiet) || (a.age ?? 0) - (b.age ?? 0))
      : session.runs;
  const run = runs.find((r) => r.run_id === picked) ?? firstRun({ runs });
  const subs =
    read?.kind === "ready" ? subRows(read.children) : session.children.length > 0 ? subRows(session.children) : null;
  const names = (ids: readonly string[]) => ids.flatMap((id) => model.byId.get(id) ?? []);
  const place = sessionPlace({
    host: session.host,
    host_kind: session.hostKind,
    host_source: session.hostSource,
    model: session.model,
  });
  const lanes = names(session.dispatched).sort(compareSessions);
  return (
    <div className="flex flex-col gap-3" data-agent-details="session">
      <div className="flex flex-wrap items-center gap-1.5">
        <StatePill state={session.state} />
        <span className="text-xs text-muted-foreground">
          {nameOf(session.person)} · {session.client}
        </span>
      </div>
      <dl className="flex flex-col gap-1.5">
        <Fact term="Machine">
          {place.host ?? NOT_REPORTED}
          {place.kind ? ` (${hostKindWord(place.kind)})` : ""}
        </Fact>
        <Fact term="Place">{hostSourceSentence(place.source)}</Fact>
        <Fact term="Model">
          <span className="inline-flex items-center gap-1">
            <Icon name={MODEL_ICON} size={12} aria-hidden />
            {place.model ?? NOT_REPORTED}
          </span>
        </Fact>
        <Fact term="Last event">{relativeTime(session.lastAt, nowMs) || "Unknown"}</Fact>
        {session.wait ? <Fact term="Waits on">{waitingText(session.wait, nowMs).replace(/^waiting on /, "")}</Fact> : null}
        {session.dispatchers.length > 0 ? (
          <Fact term={DISPATCHED_BY_LABEL}>
            {names(session.dispatchers)
              .map((s) => s.name)
              .join(", ")}
          </Fact>
        ) : null}
        {session.quietCount > 0 ? <Fact term="Quiet runs">{session.quietCount}</Fact> : null}
      </dl>
      {runs.length > 0 ? (
        <section aria-label="Tasks" className="flex flex-col gap-1">
          <h4 className="text-[11px] font-medium text-muted-foreground">{runs.length === 1 ? "Task" : "Tasks"}</h4>
          <TaskRows
            runs={runs}
            pickedId={run?.run_id ?? null}
            askedOf={session.askedOf}
            onPick={setPicked}
            onOpenTask={onOpenTask}
          />
        </section>
      ) : null}
      {run ? <TaskRunPanel taskId={run.task_id} personLabels={personLabels} /> : null}
      <AgentSubagents
        counts={session.subs}
        rows={subs}
        failed={read?.kind === "error"}
        onRetry={props.onRetryRead}
        nowMs={nowMs}
      />
      {lanes.length > 0 ? (
        <section aria-label={DISPATCHED_LABEL} className="flex flex-col gap-1">
          <h4 className="text-[11px] font-medium text-muted-foreground">Sessions it dispatched</h4>
          <SessionList sessions={lanes} nowMs={nowMs} onSelect={onSelect} />
        </section>
      ) : null}
    </div>
  );
}

function PersonDetails({ person, ...props }: DetailsProps & { person: LensPerson }) {
  const sessions = personSessions(person);
  const counts = countStates(sessions);
  const machines = [...new Set(sessions.filter((s) => s.state !== "idle" && s.host).map((s) => s.host as string))];
  const asks = sessions.filter((s) => s.state === "you");
  return (
    <div className="flex flex-col gap-3" data-agent-details="person">
      <div className="flex items-center gap-2">
        <PersonAvatar email={person.person} displayName={props.nameOf(person.person)} size={28} />
        <span className="truncate text-xs text-muted-foreground">{person.person}</span>
      </div>
      <dl className="flex flex-col gap-1.5">
        {LENS_STATES.map((state) => (
          <Fact key={state} term={lensWord(state)}>
            {counts[state]}
          </Fact>
        ))}
        <Fact term="Machines">{machines.join(", ") || NOT_REPORTED}</Fact>
      </dl>
      {asks.length > 0 ? (
        <section aria-label="Needs you" className="flex flex-col gap-1">
          <h4 className="text-[11px] font-medium text-muted-foreground">Sessions that need you</h4>
          <SessionList sessions={asks} nowMs={props.nowMs} onSelect={props.onSelect} />
        </section>
      ) : null}
    </div>
  );
}

export function AgentDetails(props: DetailsProps) {
  const { model, selection, nowMs, onSelect } = props;
  if (selection.kind === "session") {
    const session = model.byId.get(selection.id);
    return session ? <SessionDetails key={session.id} {...props} session={session} /> : null;
  }
  if (selection.kind === "person") {
    const person = model.people.find((p) => p.person === selection.person);
    return person ? <PersonDetails {...props} person={person} /> : null;
  }
  const sessions = blockSessions(model, selection.block);
  return (
    <div className="flex flex-col gap-3" data-agent-details="block">
      <p className="text-xs text-muted-foreground">
        {blockSentence(sessions.length)}. The longest wait comes first.
      </p>
      <SessionList sessions={sessions} nowMs={nowMs} onSelect={onSelect} />
    </div>
  );
}

export default AgentDetails;
