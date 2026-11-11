"use client";

/**
 * Projects · Questions for me (WS-55 EA-18b, `external_agent_access.md` §12.8).
 *
 * Reading this as: the one list of the agent questions that wait on a person,
 * grouped under the project path of their task. Its neighbour in the sidebar is
 * Reports, so it keeps that pane's header grammar: a bar with the title, then
 * the controls, then the body.
 *
 * ⚠️ **Draws the server's rows and decides nothing.** The rows, the counts,
 * `why`, `can_answer` and `question.kind` are `agent_questions.py`'s. The
 * answer part is `AgentQuestionCard`, the same component the run panel uses.
 * The words and the pure rules are in `lib/agentQuestions.ts`.
 *
 * ⚠️ **A typed archive request** goes to `archive-approval`, never to
 * `/activities` (`answerRoute`).
 *
 * The list reads again after each answer and every 60 seconds while the page
 * is open. The "Everyone" tab shows only when the server sent its count.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import { ControlLink } from "@/components/ControlLink";
import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import SelectButton from "@/components/ui/SelectButton";
import type { ElicitationAnswers } from "@/lib/elicitationAnswer";

import {
  type AgentActivity,
  type ArchiveDecision,
  answerPayload,
  archiveApprovalPayload,
  writeFailure,
} from "../lib/agentRuns";
import {
  ANSWERED_HOLD_MS,
  QUESTIONS_COPY,
  QUESTIONS_POLL_MS,
  type QuestionRow,
  type QuestionScope,
  type QuestionSort,
  type QuestionsPage,
  answerRoute,
  askedLine,
  cappedLine,
  emptyCopy,
  everyoneTabShown,
  groupByProject,
  pathLabel,
  projectHref,
  questionActivity,
  questionsCount,
  runLine,
  tabLabel,
  taskHref,
  whyTag,
} from "../lib/agentQuestions";
import { ProjectsApiError, agentRunsApi, questionsApi } from "../lib/api";
import { AgentQuestionCard } from "./AgentQuestionCard";

/** One option of the project picker. `depth` indents a subproject. */
export interface QuestionProjectOption {
  id: string;
  label: string;
  depth: number;
}

export interface QuestionsViewProps {
  projects: readonly QuestionProjectOption[];
  personLabels?: ReadonlyMap<string, string>;
  onOpenTask: (taskId: string) => void;
  onOpenProject: (projectId: string) => void;
  /** An answer landed. The sidebar badge reads again. */
  onChanged?: () => void;
}

export function QuestionsView({
  projects,
  personLabels,
  onOpenTask,
  onOpenProject,
  onChanged,
}: QuestionsViewProps) {
  const { access } = useAccess();
  const me = access.email;
  const [scope, setScope] = useState<QuestionScope>("mine");
  const [project, setProject] = useState("");
  const [sort, setSort] = useState<QuestionSort>("oldest");
  const [page, setPage] = useState<QuestionsPage | null>(null);
  /** The counts outlive a tab switch, so both tabs keep their numbers. */
  const [counts, setCounts] = useState<QuestionsPage["counts"] | null>(null);
  const [off, setOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<Record<string, string>>({});
  /** The answer sent from here, by activity id, until the list reads again. */
  const [sent, setSent] = useState<Record<string, AgentActivity>>({});
  const [now, setNow] = useState(() => Date.now());

  const query = useRef({ scope, project, sort });
  useEffect(() => {
    query.current = { scope, project, sort };
  }, [scope, project, sort]);
  const timers = useRef<number[]>([]);

  const load = useCallback(async () => {
    const q = query.current;
    try {
      const got = await questionsApi.list({
        scope: q.scope,
        project: q.project || null,
        sort: q.sort,
      });
      // A slow read of an old tab must not draw over the new one.
      if (q.scope !== query.current.scope || q.project !== query.current.project || q.sort !== query.current.sort) {
        return;
      }
      if (got === null) {
        setOff(true);
        return;
      }
      setOff(false);
      setPage(got);
      setCounts(got.counts);
      setNow(Date.now());
      setError(null);
      setSent({});
    } catch (err) {
      const status = err instanceof ProjectsApiError ? err.status : undefined;
      if (status === 403 && q.scope === "everyone") {
        setScope("mine");
        return;
      }
      setError(
        err instanceof Error && err.message
          ? `${QUESTIONS_COPY.readFailed} ${err.message}`
          : QUESTIONS_COPY.readFailed,
      );
    }
  }, []);

  // A new tab, project or sort reads at once.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPage(null);
    void load();
  }, [scope, project, sort, load]);

  // Every 60 seconds while the page is open.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      void load();
    }, QUESTIONS_POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const held = timers.current;
    return () => held.forEach((t) => window.clearTimeout(t));
  }, []);

  /** After a write: show "Answered by <name>" for a moment, then read again. */
  function afterWrite(row: QuestionRow, prompt: AgentActivity) {
    setSent((held) => ({ ...held, [row.activity_id]: prompt }));
    onChanged?.();
    timers.current.push(window.setTimeout(() => void load(), ANSWERED_HOLD_MS));
  }

  async function run(row: QuestionRow, send: () => Promise<void>, failure: string) {
    setBusy(row.activity_id);
    setFailed((held) => ({ ...held, [row.activity_id]: "" }));
    try {
      await send();
      return true;
    } catch (err) {
      const status = err instanceof ProjectsApiError ? err.status : undefined;
      const message = err instanceof Error ? err.message : undefined;
      const said = writeFailure(status, message, failure);
      setFailed((held) => ({ ...held, [row.activity_id]: said.text }));
      // A 409: another member answered first. Read the list again.
      if (said.reload) void load();
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function answer(row: QuestionRow, answers: ElicitationAnswers) {
    const elicitation = questionActivity(row);
    const body = answerPayload(elicitation, answers);
    const ok = await run(
      row,
      async () => {
        await agentRunsApi.post(row.run_id, body);
      },
      "Could not send the answer.",
    );
    if (!ok) return;
    afterWrite(row, {
      id: `local:${body.idempotency_key}`,
      seq: Number.MAX_SAFE_INTEGER,
      type: "prompt",
      content: { body: body.body ?? null, selected: body.selected ?? [] },
      actor: me || null,
      in_reply_to: row.activity_id,
    });
  }

  async function decide(row: QuestionRow, decision: ArchiveDecision) {
    const body = archiveApprovalPayload(decision);
    const ok = await run(
      row,
      async () => {
        await agentRunsApi.archiveApproval(row.run_id, body);
      },
      "Could not send the decision.",
    );
    if (!ok) return;
    afterWrite(row, {
      id: `local:${body.idempotency_key}`,
      seq: Number.MAX_SAFE_INTEGER,
      type: "prompt",
      content: { selected: [decision], archived: decision === "approve" },
      actor: me || null,
      in_reply_to: row.activity_id,
    });
  }

  const groups = useMemo(() => (page ? groupByProject(page.rows) : []), [page]);
  const capped = page ? cappedLine(page, sort) : null;
  const empty = emptyCopy(scope);
  const showEveryone = everyoneTabShown(counts);

  if (off) {
    return (
      <section aria-label={QUESTIONS_COPY.title} className="flex min-h-0 flex-1 flex-col" data-questions="off">
        <EmptyState icon="MessageCircleQuestion" message={QUESTIONS_COPY.off} hint={QUESTIONS_COPY.offHint} />
      </section>
    );
  }

  return (
    <section aria-label={QUESTIONS_COPY.title} className="flex min-h-0 flex-1 flex-col" data-questions="">
      <div className="flex min-w-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <Icon name="MessageCircleQuestion" className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <h3 className="text-sm font-medium text-foreground">{QUESTIONS_COPY.title}</h3>
        <div role="tablist" aria-label="Which questions" className="flex items-center gap-1">
          <Button
            role="tab"
            aria-selected={scope === "mine"}
            size="sm"
            variant={scope === "mine" ? "secondary" : "ghost"}
            data-questions-tab="mine"
            onClick={() => setScope("mine")}
          >
            {tabLabel("mine", counts?.mine)}
          </Button>
          {showEveryone ? (
            <Button
              role="tab"
              aria-selected={scope === "everyone"}
              size="sm"
              variant={scope === "everyone" ? "secondary" : "ghost"}
              data-questions-tab="everyone"
              onClick={() => setScope("everyone")}
            >
              {tabLabel("everyone", counts?.everyone)}
            </Button>
          ) : null}
        </div>
        <div className="ml-auto flex min-w-0 flex-wrap items-center gap-2">
          <SelectButton
            label={QUESTIONS_COPY.projectFilter}
            value={project}
            defaultValue=""
            onChange={setProject}
            widthClass="max-w-[14rem]"
            options={[
              { value: "", label: QUESTIONS_COPY.allProjects },
              ...projects.map((p) => ({ value: p.id, label: p.label, depth: p.depth })),
            ]}
          />
          <SelectButton
            label={QUESTIONS_COPY.sortLabel}
            value={sort}
            defaultValue="oldest"
            onChange={(v) => setSort(v === "newest" ? "newest" : "oldest")}
            options={[
              { value: "oldest", label: QUESTIONS_COPY.sortOldest },
              { value: "newest", label: QUESTIONS_COPY.sortNewest },
            ]}
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 py-3">
        {error ? (
          <p role="alert" className="mb-2 text-xs text-destructive">
            {error}
          </p>
        ) : null}

        {page === null && !error ? (
          <div className="space-y-2" aria-label={QUESTIONS_COPY.loading}>
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : null}

        {page && groups.length === 0 ? (
          <EmptyState icon="CircleCheck" message={empty.message} hint={empty.hint} />
        ) : null}

        {groups.map((group) => (
          <div key={group.key} className="mb-5" data-questions-group={group.key}>
            <p className="mb-2 flex min-w-0 flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
              {group.path.map((step, i) => (
                <span key={step.id} className="inline-flex min-w-0 items-center gap-1">
                  {i > 0 ? <span aria-hidden>›</span> : null}
                  <ControlLink
                    href={projectHref(step.id)}
                    onActivate={() => onOpenProject(step.id)}
                    className="truncate font-medium text-foreground hover:underline"
                    title={`Open ${step.name}`}
                    data-questions-project=""
                  >
                    {step.name}
                  </ControlLink>
                </span>
              ))}
              <span className="tabular-nums" title={pathLabel(group.path)}>
                · {questionsCount(group.rows.length)}
              </span>
            </p>
            <ul className="divide-y divide-border rounded-md border border-border bg-card">
              {group.rows.map((row) => {
                const done = sent[row.activity_id] ?? null;
                const tag = whyTag(row.why);
                const second = runLine(row, now);
                return (
                  <li
                    key={row.activity_id}
                    className="space-y-2 px-3 py-2.5"
                    data-question-row={row.question.kind}
                  >
                    <div className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1">
                      <div className="min-w-0 flex-1 basis-56">
                        <p className="truncate text-sm font-medium text-foreground">
                          {typeof row.task_number === "number" ? `#${row.task_number} ` : ""}
                          {row.task_title}
                        </p>
                        <p className="text-xs text-muted-foreground">{askedLine(row, now)}</p>
                        {second ? <p className="text-[11px] text-muted-foreground">{second}</p> : null}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {tag ? (
                          <Badge size="sm" title={row.why ?? undefined}>
                            {tag}
                          </Badge>
                        ) : null}
                        <ControlLink
                          href={taskHref(row.task_id)}
                          onActivate={() => onOpenTask(row.task_id)}
                          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                          title={`Open ${row.task_title}`}
                        >
                          <Icon name="ArrowUpRight" size={12} aria-hidden />
                          {QUESTIONS_COPY.openTask}
                        </ControlLink>
                      </div>
                    </div>
                    <AgentQuestionCard
                      elicitation={questionActivity(row)}
                      run={{ member_email: "" }}
                      clientLabel={row.agent_name || "The agent"}
                      answer={done}
                      open
                      canAnswer={row.can_answer}
                      archiveMine={answerRoute(row) === "archive-approval"}
                      kind={row.question.kind}
                      expired={false}
                      busy={busy === row.activity_id}
                      personLabels={personLabels}
                      onAnswer={(answers) => void answer(row, answers)}
                      onDecision={(decision) => void decide(row, decision)}
                    />
                    {failed[row.activity_id] ? (
                      <p role="alert" className="text-xs text-destructive">
                        {failed[row.activity_id]}
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}

        {capped ? (
          <p className="text-xs text-muted-foreground" data-questions-capped="">
            {capped}
          </p>
        ) : null}
      </div>
    </section>
  );
}
