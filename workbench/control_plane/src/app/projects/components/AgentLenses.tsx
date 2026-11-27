"use client";

/**
 * Projects · the Agents view with its lenses (WS-MCP AMR-1 to AMR-3,
 * `agent_coordination.md` §5.12, dark behind `AGENT_LENSES_ORGS`).
 *
 * Reading this as: the Agents canvas of a project. A toolbar with the state
 * filters, the person filter, a find box and one switch, Board, Graph or List.
 * Details open in a dock at the right, where the task panel opens. A narrow
 * window uses the shared `Modal`. At 760 px and under, the phone view takes the
 * place of all three lenses.
 *
 * AMR-4 (§3.12.9): past 40 sessions, "All agents" and a person draw the
 * overview tiles. A tile or a square opens one orchestrator, bounded. A
 * breadcrumb leads back, a pager walks the questions, and one session at a time
 * opens its sub-agents, which its own read loads.
 *
 * ⚠️ **The view only reads.** Every act on an agent is a control of the run
 * panel or of the question card, and each keeps its own route.
 *
 * ⚠️ Every name in the map is untrusted text, drawn as text.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import { type ModeOption, ModeSwitch } from "@/components/ModeSwitch";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import SelectButton from "@/components/ui/SelectButton";
import { Skeleton } from "@/components/ui/Skeleton";

import { agentMapApi, questionsApi } from "../lib/api";
import { type AgentMap, type AgentMapRead, MAP_RESPONSE_MARK, MAP_TRUNCATED_COPY } from "../lib/agentMap";
import type { GraphNode, OpenSubs } from "../lib/agentGraph";
import {
  DEFAULT_STATES,
  LENS_STATES,
  type LensFilters,
  type LensSession,
  type LensState,
  buildLensModel,
  countStates,
  lensIcon,
  lensWord,
  matchesFind,
  membersOf,
  subRows,
} from "../lib/agentLenses";
import {
  type BucketWindows,
  findGroup,
  groupKeyOf,
  openBucket,
  overviewTiles,
  questionStops,
  scopeSessions,
  stepStop,
  subTotals,
  usesOverview,
} from "../lib/agentScale";
import { type ReadSession, useSessionReads, wantedSessions } from "../lib/agentSessionReads";
import { AgentBoardLens, AgentListLens, BoundedBoard, type LensViewProps, NO_ORCHESTRATOR } from "./AgentBoardList";
import { ALL_AGENTS, AgentCrumbs, type Crumb } from "./AgentCrumbs";
import { AgentDetails, type LensSelection, selectionNodeId, selectionTitle } from "./AgentDetails";
import { AgentGraphLens, type GraphFocus } from "./AgentGraphLens";
import { AgentOverview } from "./AgentOverview";
import { AgentsPhone } from "./AgentsPhone";

export type Lens = "board" | "graph" | "list";
const LENSES: readonly ModeOption<Lens>[] = [
  { id: "board", icon: "Kanban", label: "Board", title: "Board" },
  { id: "graph", icon: "Network", label: "Graph", title: "Graph" },
  { id: "list", icon: "ListTree", label: "List", title: "List" },
];
/**
 * The toolbar is one row in a narrow column too: it hides the words of the
 * chips first, then the words of the lens switch. The width is that of the
 * toolbar, not of the window, because the dock takes its room. A hidden word
 * stays the name of its control.
 */
const CHIP_WORD = "@max-[66rem]:sr-only";
const LENS_WORD = "@max-[54rem]:sr-only";
export const LENS_SWITCH_LABEL = "Lens";
export const FIND_LABEL = "Find a session, task or subagent";
/** The performance mark and measure of the first drawn overview (AMR-4). */
export const OVERVIEW_MARK = "agents-view:overview-drawn";
export const OVERVIEW_MEASURE = "agents-view:first-overview";
export const EVERYONE = "Everyone";

/** The phone view takes over at this width (the owner's mockup). */
export const PHONE_QUERY = "(max-width: 760px)";
/** The dock stands beside the lens from this width. Under it, the details are a `Modal`. */
export const DOCK_QUERY = "(min-width: 1100px)";
const NOW_TICK_MS = 15_000;

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    const mql = window.matchMedia(query);
    const sync = () => setMatches(mql.matches);
    sync();
    mql.addEventListener("change", sync);
    return () => mql.removeEventListener("change", sync);
  }, [query]);
  return matches;
}

function useNowMs(given: number | undefined): number {
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    if (given !== undefined) return undefined;
    const timer = window.setInterval(() => setClock(Date.now()), NOW_TICK_MS);
    return () => window.clearInterval(timer);
  }, [given]);
  return given ?? clock;
}

interface Questions {
  /** False when the questions list is off for this org. */
  on: boolean;
  /** Run id to the text of its open question. */
  byRun: ReadonlyMap<string, string>;
  /** The runs of the questions, oldest first. The pager walks them. */
  order: readonly string[];
}

const NO_QUESTIONS: Questions = { on: false, byRun: new Map(), order: [] };

/**
 * The text of each question that waits on the member, read again with the map.
 * It is the list of `QuestionsView`, so the card and the list never disagree.
 * A failed read keeps the last answer.
 */
function useQuestionTexts(map: AgentMap | null, projectId: string | undefined): Questions {
  const [questions, setQuestions] = useState<Questions>(NO_QUESTIONS);
  useEffect(() => {
    if (!map) return;
    let live = true;
    questionsApi
      .list({ scope: "mine", project: projectId ?? null, sort: "oldest" })
      .then((page) => {
        if (!live) return;
        if (!page) return setQuestions(NO_QUESTIONS);
        const byRun = new Map<string, string>();
        for (const row of page.rows) {
          if (row.question.body && !byRun.has(row.run_id)) byRun.set(row.run_id, row.question.body);
        }
        setQuestions({ on: true, byRun, order: page.rows.map((row) => row.run_id) });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [map, projectId]);
  return questions;
}

/**
 * Marks the paint of the overview, and measures it from the answer of the map
 * that it draws. Each answer is measured once (AMR-4).
 */
function useOverviewTiming(drawn: boolean, map: AgentMap | null) {
  const measured = useRef<number | null>(null);
  useEffect(() => {
    if (!drawn) return undefined;
    // Two frames: the first draws the tiles, and the second runs after that paint.
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => {
        const answer = performance.getEntriesByName(MAP_RESPONSE_MARK).at(-1);
        performance.clearMarks(OVERVIEW_MARK);
        performance.mark(OVERVIEW_MARK);
        if (answer && measured.current !== answer.startTime) {
          measured.current = answer.startTime;
          performance.measure(OVERVIEW_MEASURE, MAP_RESPONSE_MARK, OVERVIEW_MARK);
        }
      });
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, [drawn, map]);
}

export interface AgentLensesProps {
  read: AgentMapRead;
  onOpenTask: (taskId: string) => void;
  onOpenProject?: (projectId: string) => void;
  /** The project in scope. The questions list reads its subtree. */
  projectId?: string;
  personLabels?: ReadonlyMap<string, string>;
  /** The addresses on the map, so the page can resolve their names. */
  onPeopleSeen?: (emails: string[]) => void;
  /** Reads the map again, after an answer. */
  onRefresh?: () => void;
  emptyCopy: string;
  /** A test fixes the clock, the lens, the width and the viewer. */
  viewerEmail?: string;
  nowMs?: number;
  initialLens?: Lens;
  phone?: boolean;
  /** AMR-4: reads one session when it opens. A test gives its own. */
  readSession?: ReadSession;
  /** AMR-4: the orchestrator group that is open at the start. */
  initialGroup?: string;
}

export function AgentLenses({
  read,
  onOpenTask,
  onOpenProject,
  projectId,
  personLabels,
  onPeopleSeen,
  onRefresh,
  emptyCopy,
  nowMs: nowGiven,
  initialLens = "graph",
  phone: phoneGiven,
  viewerEmail,
  readSession,
  initialGroup,
}: AgentLensesProps) {
  const nowMs = useNowMs(nowGiven);
  const narrow = useMedia(PHONE_QUERY);
  const phone = phoneGiven ?? narrow;
  const docked = useMedia(DOCK_QUERY);
  const [lens, setLens] = useState<Lens>(initialLens);
  const [states, setStates] = useState<ReadonlySet<LensState>>(() => new Set(DEFAULT_STATES));
  const [person, setPerson] = useState("");
  const [find, setFind] = useState("");
  const [groupKey, setGroupKey] = useState<string | null>(initialGroup ?? null);
  const [windows, setWindows] = useState<BucketWindows>({});
  const [subsOf, setSubsOf] = useState<string | null>(null);
  const [focus, setFocus] = useState<GraphFocus | null>(null);
  const [questionAt, setQuestionAt] = useState(0);
  const [opened, setOpened] = useState<ReadonlySet<string>>(() => new Set());
  const [selection, setSelection] = useState<LensSelection | null>(null);
  const map = read.kind === "ready" ? read.map : null;
  const questions = useQuestionTexts(map, projectId);
  const signedIn = useAccess().access.email;
  const me = viewerEmail ?? signedIn;
  const model = useMemo(
    () => (map ? buildLensModel(map, questions.byRun, { email: me, labels: personLabels }) : null),
    [map, questions.byRun, me, personLabels],
  );
  const nameOf = useCallback(
    (email: string) => personLabels?.get(email) ?? personLabels?.get(email.toLowerCase()) ?? email,
    [personLabels],
  );
  const emails = (map?.people ?? []).map((p) => p.person).join("\n");
  useEffect(() => {
    if (emails) onPeopleSeen?.(emails.split("\n"));
  }, [emails, onPeopleSeen]);

  const title = model && selection ? selectionTitle(model, selection, nameOf) : null;
  // A selection whose node left the map closes at once, and does not come back.
  if (model && selection !== null && title === null) setSelection(null);
  const openGroup = useCallback((key: string) => setOpened((prev) => new Set(prev).add(key)), []);
  const openTask = useCallback(
    (taskId: string) => {
      // The task panel takes the place of the dock.
      setSelection(null);
      onOpenTask(taskId);
    },
    [onOpenTask],
  );
  const openSession = useCallback((s: LensSession) => setSelection({ kind: "session", id: s.id }), []);
  const pickNode = useCallback((node: GraphNode) => {
    if (node.kind === "person") setSelection({ kind: "person", person: node.person });
    else if (node.kind === "block") setSelection({ kind: "block", block: node.block });
    else if (node.kind === "session" || node.kind === "orch") setSelection({ kind: "session", id: node.session.id });
  }, []);
  const filters: LensFilters = useMemo(() => ({ states, person, find }), [states, person, find]);

  const here = model ? findGroup(model, groupKey) : null;
  // An orchestrator that left the map gives its level back.
  if (model && groupKey !== null && here === null) setGroupKey(null);
  const overview = !phone && model !== null && here === null && lens !== "list" && usesOverview(model, person);
  const stops = useMemo(() => (model ? questionStops(model, questions.order) : []), [model, questions.order]);
  const tiles = useMemo(
    () => (model && overview ? overviewTiles(model, { person, find }, nameOf) : []),
    [model, overview, person, find, nameOf],
  );
  useOverviewTiming(overview, map);

  const openLevel = useCallback((who: string, key: string | null) => {
    setPerson(who);
    setGroupKey(key);
    setWindows({});
    setSubsOf(null);
  }, []);
  /** Opens the orchestrator of a session, selects it and centres its card. */
  const goToSession = useCallback(
    (sessionId: string) => {
      if (!model) return;
      const at = groupKeyOf(model, sessionId);
      if (!at) return;
      if (at.key !== groupKey) openLevel(at.person, at.key);
      else setPerson(at.person);
      setLens("graph");
      setSelection({ kind: "session", id: sessionId });
      setFocus((old) => ({ id: `s:${sessionId}`, n: (old?.n ?? 0) + 1 }));
    },
    [model, groupKey, openLevel],
  );

  // AMR-4: only an open session is read: the one whose rows show, and the one in the details.
  const selected = selection?.kind === "session" ? selection.id : null;
  const subsShown = !phone && lens === "graph" && !overview ? subsOf : null;
  const reader = useCallback<ReadSession>(
    (id) => (readSession ? readSession(id) : agentMapApi.read({ session: id, projectId })),
    [readSession, projectId],
  );
  const { reads, retry } = useSessionReads(projectId ?? "", wantedSessions(subsShown, selected), map, reader);
  const subsRead = subsShown ? reads.get(subsShown) : undefined;
  const openSubs: OpenSubs | null = useMemo(
    () =>
      subsShown
        ? {
            sessionId: subsShown,
            rows: subsRead?.kind === "ready" ? subRows(subsRead.children) : null,
            failed: subsRead?.kind === "error",
          }
        : null,
    [subsShown, subsRead],
  );
  const toggleSubs = useCallback((id: string) => setSubsOf((old) => (old === id ? null : id)), []);
  const onBucket = useCallback(
    (state: LensState, rest: number) => setWindows((old) => openBucket(old, state, rest)),
    [],
  );

  if (read.kind === "loading" || (read.kind === "ready" && !model)) {
    return (
      <div className="space-y-2 p-3" data-agent-lenses="loading">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (!map || !model) return null;
  if (map.sessions.length === 0) {
    return (
      <p data-agent-map-empty="" className="p-3 text-sm text-muted-foreground">
        {emptyCopy}
      </p>
    );
  }

  const scope = here ? membersOf(here.group) : scopeSessions(model, person);
  const counts = countStates(scope.filter((s) => matchesFind(s, find, nameOf(s.person))));
  const details =
    selection && title !== null ? (
      <AgentDetails
        model={model}
        selection={selection}
        onSelect={setSelection}
        onOpenTask={openTask}
        personLabels={personLabels}
        nameOf={nameOf}
        nowMs={nowMs}
        read={selected ? reads.get(selected) : undefined}
        onRetryRead={retry}
      />
    ) : null;
  const sheet = (
    <Modal
      open={details !== null && (phone || !docked)}
      onClose={() => setSelection(null)}
      title={title ?? ""}
      placement={phone ? "sheet" : "end"}
      size="md"
    >
      <div className="min-h-0 flex-1 overflow-y-auto p-3">{details}</div>
    </Modal>
  );
  const truncated = map.truncated ? <p className="px-3 py-2 text-xs text-muted-foreground">{MAP_TRUNCATED_COPY}</p> : null;

  if (phone) {
    return (
      <div data-agent-lenses="phone" className="flex h-full min-h-0 flex-1 flex-col">
        <AgentsPhone
          model={model}
          nowMs={nowMs}
          nameOf={nameOf}
          onOpenSession={openSession}
          onOpenTask={openTask}
          onOpenProject={onOpenProject ?? (() => undefined)}
          projectId={projectId}
          personLabels={personLabels}
          questionsOn={questions.on}
          onAnswered={onRefresh}
          find={find}
          onFind={setFind}
        />
        {truncated}
        {sheet}
      </div>
    );
  }

  const shared: LensViewProps = {
    model,
    filters,
    opened,
    onOpenGroup: openGroup,
    selectedId: selectionNodeId(selection),
    onOpenSession: openSession,
    onOpenPerson: (who) => setSelection({ kind: "person", person: who }),
    nowMs,
    nameOf,
    onlyGroup: here?.group.key ?? null,
  };
  const crumbs: Crumb[] = [
    { label: ALL_AGENTS, onOpen: person || here ? () => openLevel("", null) : undefined },
    ...(person ? [{ label: nameOf(person), onOpen: here ? () => openLevel(person, null) : undefined }] : []),
    ...(here ? [{ label: here.group.orch?.name ?? NO_ORCHESTRATOR }] : []),
  ];
  const subagents = subTotals(model.sessions).total;
  const fleetWords = `${model.sessions.length} ${model.sessions.length === 1 ? "session" : "sessions"} · ${subagents} ${subagents === 1 ? "subagent" : "subagents"}`;
  const questionStop = selected ? stops.indexOf(selected) : -1;
  const at = questionStop >= 0 ? questionStop : Math.min(questionAt, Math.max(stops.length - 1, 0));
  const goQuestion = (by: number) => {
    const next = stepStop(at, by, stops.length);
    setQuestionAt(next);
    if (stops[next]) goToSession(stops[next]);
  };
  const showCrumbs = here !== null || stops.length > 0 || usesOverview(model, "");
  return (
    <div data-agent-lenses={lens} className="flex h-full min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="@container shrink-0 border-b border-border">
          <div role="toolbar" aria-label="Agent filters" className="flex flex-wrap items-center gap-1 px-2 py-2">
            <div className="flex min-w-0 flex-wrap items-center gap-1 @max-[54rem]:gap-0 @max-[44rem]:[&>button]:px-1.5">
              {LENS_STATES.map((state) => (
                <Button
                  key={state}
                  variant="ghost"
                  size="sm"
                  selected={states.has(state)}
                  data-agent-chip={state}
                  title={`${lensWord(state)}: ${counts[state]}`}
                  onClick={() =>
                    setStates((prev) => {
                      const next = new Set(prev);
                      if (next.has(state)) next.delete(state);
                      else next.add(state);
                      return next;
                    })
                  }
                >
                  <span className="inline-flex items-center gap-1.5 @max-[54rem]:gap-1">
                    <Icon name={lensIcon(state)} size={14} className={state === "you" ? "text-warning" : undefined} aria-hidden />
                    <span className={CHIP_WORD}>{lensWord(state)}</span>
                    <b className="font-semibold tabular-nums">{counts[state]}</b>
                  </span>
                </Button>
              ))}
            </div>
            {/* One block: in a column too narrow for one row, it takes the second row whole. */}
            <div className="flex min-w-[min(17rem,100%)] flex-1 flex-wrap items-center gap-1">
              <SelectButton
                label="Person"
                widthClass="w-28 @max-[44rem]:w-26"
                value={person}
                defaultValue=""
                onChange={(who) => openLevel(who, null)}
                options={[
                  { value: "", label: EVERYONE },
                  ...model.people.map((p) => ({ value: p.person, label: nameOf(p.person) })),
                ]}
              />
              <div className="min-w-16 max-w-52 flex-1 basis-16">
                <Input
                  inputSize="sm"
                  icon="Search"
                  value={find}
                  onChange={(e) => setFind(e.target.value)}
                  placeholder="Find"
                  aria-label={FIND_LABEL}
                />
              </div>
              <div className="ml-auto">
                <ModeSwitch modes={LENSES} mode={lens} onPick={setLens} label={LENS_SWITCH_LABEL} labelClass={LENS_WORD} />
              </div>
            </div>
          </div>
        </div>
        {showCrumbs ? (
          <AgentCrumbs
            crumbs={crumbs}
            // The dock narrows the column: the count gives way to the levels and the pager.
            summary={docked && details !== null ? "" : fleetWords}
            question={{ at, total: stops.length, onGo: goQuestion }}
          />
        ) : null}
        {overview ? (
          <AgentOverview
            people={tiles}
            nowMs={nowMs}
            nameOf={nameOf}
            onOpenTile={(tile) => openLevel(tile.person, tile.key)}
            onPickSession={(s) => goToSession(s.id)}
            onOpenPerson={(who) => openLevel(who, null)}
            finding={find.trim() !== ""}
          />
        ) : lens === "graph" ? (
          <AgentGraphLens
            {...shared}
            onPick={pickNode}
            legend={!(docked && details !== null)}
            orchestrator={here}
            windows={windows}
            onBucket={onBucket}
            subs={openSubs}
            onToggleSubs={toggleSubs}
            focus={focus}
          />
        ) : lens === "board" ? (
          here ? (
            <BoundedBoard
              person={here.person}
              group={here.group}
              filters={filters}
              windows={windows}
              onBucket={onBucket}
              selectedId={shared.selectedId}
              onOpenSession={openSession}
              nowMs={nowMs}
              nameOf={nameOf}
            />
          ) : (
            <AgentBoardLens {...shared} />
          )
        ) : (
          <AgentListLens {...shared} />
        )}
        {truncated}
      </div>
      {docked && details !== null ? (
        <aside
          aria-label="Agent details"
          data-agent-dock=""
          className="flex min-h-0 w-[22rem] shrink-0 flex-col border-l border-border"
          onKeyDown={(e) => {
            if (e.key === "Escape" && !e.defaultPrevented) setSelection(null);
          }}
        >
          <div className="flex min-h-10 items-center gap-2 border-b border-border py-1 pl-3 pr-2">
            <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{title}</h3>
            <Button variant="ghost" size="icon-sm" icon="X" aria-label="Close the details" onClick={() => setSelection(null)} />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">{details}</div>
        </aside>
      ) : (
        sheet
      )}
    </div>
  );
}

export default AgentLenses;
