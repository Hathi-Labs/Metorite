"use client";

/**
 * Projects · the Agents view (WS-MCP AMR-5, `agent_coordination.md` §3.12.8,
 * dark behind `AGENT_LENSES_ORGS`).
 *
 * Reading this as: the Agents canvas of a project, as one map of everyone's
 * work. The toolbar holds the people, one switch (Active or All), a find box and
 * "Needs you N" with a step back and next. Each node folds in place. Details open
 * in a dock at the right, where the task panel opens. A narrow window uses the
 * shared `Modal`. At 760 px and under, the phone view takes the place of the map.
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
import { QUESTIONS_COPY } from "../lib/agentQuestions";
import { type AgentMap, type AgentMapRead, MAP_RESPONSE_MARK, MAP_TRUNCATED_COPY } from "../lib/agentMap";
import { type GraphNode, type OpenSubs, ROOT_ID } from "../lib/agentGraph";
import { type LensSession, type LensState, buildLensModel, lensIcon, matchesFind, subRows } from "../lib/agentLenses";
import {
  type MapMode,
  type MapView,
  NEW_VIEW,
  bucketView,
  foldView,
  isActive,
  personNodeId,
  questionStops,
  restView,
  sessionNodeId,
  stepStop,
} from "../lib/agentScale";
import { type ReadSession, useSessionReads, wantedSessions } from "../lib/agentSessionReads";
import { AgentDetails, type LensSelection, selectionNodeId, selectionTitle } from "./AgentDetails";
import { AgentGraphLens, EVERYONE, type GraphFocus, type NodeActions } from "./AgentGraphLens";
import { NO_MATCH_COPY } from "./AgentSquares";
import { AgentsPhone } from "./AgentsPhone";

const MODES: readonly ModeOption<MapMode>[] = [
  { id: "active", icon: "Activity", label: "Active", title: "Needs you, failed, working and waiting" },
  { id: "all", icon: "Layers", label: "All", title: "Idle and done too" },
];
export const MODE_SWITCH_LABEL = "Agents shown";
export const PEOPLE_LABEL = "People";
export const ME = "Me";
/** The value of the "Me" option of the People filter. */
const ME_VALUE = "me";
export const FIND_LABEL = "Find a session, task or subagent";
export const NEEDS_YOU = "Needs you";
export const NO_AGENT_OF_YOURS = "No agent of yours is here now.";
/** The performance mark and measure of the first drawn map (AMR-4, AMR-5). */
export const MAP_DRAWN_MARK = "agents-view:map-drawn";
export const MAP_MEASURE = "agents-view:first-map";

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
 * Marks the paint of the map, and measures it from the answer of the read that it
 * draws. Each answer is measured once (AMR-4).
 */
function useMapTiming(drawn: boolean, map: AgentMap | null) {
  const measured = useRef<number | null>(null);
  useEffect(() => {
    if (!drawn) return undefined;
    // Two frames: the first draws the cards, and the second runs after that paint.
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => {
        const answer = performance.getEntriesByName(MAP_RESPONSE_MARK).at(-1);
        performance.clearMarks(MAP_DRAWN_MARK);
        performance.mark(MAP_DRAWN_MARK);
        if (answer && measured.current !== answer.startTime) {
          measured.current = answer.startTime;
          performance.measure(MAP_MEASURE, MAP_RESPONSE_MARK, MAP_DRAWN_MARK);
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
  /** A test fixes the clock, the switch, the scope, the width and the viewer. */
  viewerEmail?: string;
  nowMs?: number;
  initialMode?: MapMode;
  /** "", "me" or an address. */
  initialPerson?: string;
  phone?: boolean;
  /** AMR-4: reads one session when it opens. A test gives its own. */
  readSession?: ReadSession;
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
  initialMode = "active",
  initialPerson = "",
  phone: phoneGiven,
  viewerEmail,
  readSession,
}: AgentLensesProps) {
  const nowMs = useNowMs(nowGiven);
  const narrow = useMedia(PHONE_QUERY);
  const phone = phoneGiven ?? narrow;
  const docked = useMedia(DOCK_QUERY);
  const [mode, setMode] = useState<MapMode>(initialMode);
  const [person, setPerson] = useState(initialPerson);
  const [find, setFind] = useState("");
  const [view, setView] = useState<MapView>(NEW_VIEW);
  const [subsOf, setSubsOf] = useState<string | null>(null);
  const [focus, setFocus] = useState<GraphFocus | null>(null);
  const [questionAt, setQuestionAt] = useState(0);
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
  const openTask = useCallback(
    (taskId: string) => {
      // The task panel takes the place of the dock.
      setSelection(null);
      onOpenTask(taskId);
    },
    [onOpenTask],
  );
  const openSession = useCallback((s: LensSession) => setSelection({ kind: "session", id: s.id }), []);
  const mine = (me ?? "").trim().toLowerCase();
  /** The address of the scope: "" for everyone. "Me" is the address of the viewer. */
  // With no address, "Me" matches nobody. It never reads as everyone.
  const scope =
    person === ME_VALUE
      ? (model?.people.find((p) => p.person.toLowerCase() === mine)?.person ?? (mine || ME_VALUE))
      : person;
  const selected = selection?.kind === "session" ? selection.id : null;
  const options = useMemo(
    () => ({ ...view, mode, person: scope, find, nameOf, pin: selected }),
    [view, mode, scope, find, nameOf, selected],
  );

  const stops = useMemo(() => (model ? questionStops(model, questions.order) : []), [model, questions.order]);
  const drawn = !phone && model !== null && model.sessions.length > 0;
  useMapTiming(drawn, map);

  /** Closes the details when their session is under a node that folds. */
  const dropSelection = useCallback(
    (hidden: (s: LensSession) => boolean) => {
      const s = selected ? model?.byId.get(selected) : undefined;
      if (s && hidden(s)) setSelection(null);
    },
    [model, selected],
  );

  const onFold = useCallback(
    (node: GraphNode) => {
      const open = "folded" in node && node.folded;
      if (node.kind === "root") {
        setView((old) => foldView(old, ROOT_ID, open, null));
        if (!open) setSelection(null);
      } else if (node.kind === "person") {
        setView((old) => foldView(old, personNodeId(node.person), open, node.person));
        if (!open) dropSelection((s) => s.person === node.person);
      } else if (node.kind === "orch") {
        setView((old) => foldView(old, sessionNodeId(node.session.id), open, node.session.person));
        const lanes = new Set(node.members.map((m) => m.id));
        if (!open) dropSelection((s) => s.id !== node.session.id && lanes.has(s.id));
      }
    },
    [dropSelection],
  );
  const onRest = useCallback(
    (who: string) => {
      setView((old) => restView(old, who));
      if (view.restOpen.has(who)) dropSelection((s) => s.person === who && !isActive(s));
    },
    [view.restOpen, dropSelection],
  );
  const onBucket = useCallback((who: string, state: LensState, next: number) => {
    setView((old) => bucketView(old, who, state, next));
  }, []);
  const onPick = useCallback((node: GraphNode) => {
    if (node.kind === "root") setSelection({ kind: "root", person: node.person });
    else if (node.kind === "person") setSelection({ kind: "person", person: node.person });
    else if (node.kind === "session" || node.kind === "orch") setSelection({ kind: "session", id: node.session.id });
  }, []);
  /** Unfolds the way to a session, selects it and centres its card. */
  const goToSession = useCallback(
    (sessionId: string) => {
      const s = model?.byId.get(sessionId);
      if (!model || !s) return;
      if (scope && s.person !== scope) setPerson("");
      if (find && !matchesFind(s, find, nameOf(s.person))) setFind("");
      const orch = model.people
        .find((p) => p.person === s.person)
        ?.groups.find((g) => g.sessions.some((x) => x.id === s.id))?.orch;
      setView((old) => {
        let next = foldView(old, ROOT_ID, true, null);
        if (orch) next = foldView(next, sessionNodeId(orch.id), true, s.person);
        return foldView(next, personNodeId(s.person), true, s.person);
      });
      setSelection({ kind: "session", id: sessionId });
      setFocus((old) => ({ id: sessionNodeId(sessionId), n: (old?.n ?? 0) + 1 }));
    },
    [model, scope, find, nameOf],
  );

  // AMR-4: only an open session is read: the one whose rows show, and the one in the details.
  const subsShown = phone ? null : subsOf;
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
  const actions = useMemo<Omit<NodeActions, "onHover" | "onPickId">>(
    () => ({
      onPick,
      onFold,
      onRest,
      onBucket,
      onToggleSubs: toggleSubs,
      onPickSession: (s: LensSession) => goToSession(s.id),
    }),
    [onPick, onFold, onRest, onBucket, toggleSubs, goToSession],
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

  const details =
    selection && title !== null ? (
      <AgentDetails
        model={model}
        selection={selection}
        // A session of the details opens the way to its card, so it always draws.
        onSelect={(next) => (next.kind === "session" ? goToSession(next.id) : setSelection(next))}
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

  const inScope = scope ? model.sessions.filter((s) => s.person === scope) : model.sessions;
  const emptyWords =
    inScope.length === 0
      ? person === ME_VALUE
        ? NO_AGENT_OF_YOURS
        : NO_MATCH_COPY
      : find.trim() && !inScope.some((s) => matchesFind(s, find, nameOf(s.person)))
        ? NO_MATCH_COPY
        : null;
  const questionStop = selected ? stops.indexOf(selected) : -1;
  const at = questionStop >= 0 ? questionStop : Math.min(questionAt, Math.max(stops.length - 1, 0));
  const goQuestion = (by: number) => {
    const next = stepStop(at, by, stops.length);
    setQuestionAt(next);
    if (stops[next]) goToSession(stops[next]);
  };
  return (
    <div data-agent-lenses="map" className="flex h-full min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="@container shrink-0 border-b border-border">
          <div role="toolbar" aria-label="Agent filters" className="flex flex-wrap items-center gap-x-2 gap-y-1 px-2 py-2">
            <SelectButton
              label={PEOPLE_LABEL}
              widthClass="w-36 @max-[36rem]:w-28"
              value={person}
              defaultValue=""
              onChange={(who) => {
                setPerson(who);
                setView((old) => ({ ...old, latest: null, window: null }));
                setSelection(null);
              }}
              options={[
                { value: "", label: EVERYONE },
                { value: ME_VALUE, label: ME },
                ...model.people.map((p) => ({ value: p.person, label: nameOf(p.person) })),
              ]}
            />
            <ModeSwitch
              modes={MODES}
              mode={mode}
              onPick={(next) => {
                setMode(next);
                setView((old) => ({ ...old, window: null }));
              }}
              label={MODE_SWITCH_LABEL}
            />
            <div className="min-w-24 max-w-64 flex-1 basis-32">
              <Input
                inputSize="sm"
                icon="Search"
                value={find}
                onChange={(e) => setFind(e.target.value)}
                placeholder="Find"
                aria-label={FIND_LABEL}
              />
            </div>
            {stops.length > 0 ? (
              <div className="ml-auto flex items-center gap-0.5" role="group" aria-label={NEEDS_YOU} data-agent-question-pager="">
                <Button
                  variant="ghost"
                  size="icon-sm"
                  icon="ChevronLeft"
                  aria-label={QUESTIONS_COPY.previous}
                  onClick={() => goQuestion(-1)}
                />
                <Button variant="ghost" size="sm" onClick={() => goQuestion(0)}>
                  <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                    <Icon name={lensIcon("you")} size={14} className="text-warning" aria-hidden />
                    {NEEDS_YOU} {stops.length}
                  </span>
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  icon="ChevronRight"
                  aria-label={QUESTIONS_COPY.next}
                  onClick={() => goQuestion(1)}
                />
              </div>
            ) : null}
          </div>
        </div>
        {emptyWords ? (
          <p data-agent-lens-empty="" className="p-4 text-sm text-muted-foreground">
            {emptyWords}
          </p>
        ) : (
          <AgentGraphLens
            model={model}
            options={options}
            selectedId={selectionNodeId(selection)}
            nowMs={nowMs}
            subs={openSubs}
            actions={actions}
            focus={focus}
          />
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
