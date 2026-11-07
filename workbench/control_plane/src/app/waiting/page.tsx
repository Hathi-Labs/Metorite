"use client";

/**
 * /waiting — what waits on you (WS-AP AP-2a, `waiting_on_me.md` §5.2).
 *
 * The list "For you": every decision, chat question, app request and held
 * write that waits on the member (the read seam, `GET /api/shell/waiting`),
 * and the member's own agent questions (`agentRunsApi.waiting()`), in one
 * list, oldest first. An agent question answers in place with
 * `ElicitationCard`. Every other row opens its app through a link.
 *
 * "On others" is AP-2b. The pane is `preview` (`nav.ts`), and the gateway
 * answers 404 while `WAITING_ORGS` does not hold the org. Then the page
 * shows its off state, never "Nothing waits on you".
 *
 * The rules are in `lib/forYou.ts` and `lib/waitingForYou.ts`. Both reads go
 * through the one read cache (`useCachedResource`). An answer is a Projects
 * write, so it drops the `projects/` family and both reads run again.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppTopBar } from "@/components/AppTopBar";
import ElicitationCard from "@/components/ElicitationCard";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { answerPayload, cardQuestion } from "@/app/projects/lib/agentRuns";
import { PROJECTS_CACHE, agentRunsApi } from "@/app/projects/lib/api";
import { cacheKey } from "@/lib/dataCache";
import type { ElicitationAnswers } from "@/lib/elicitationAnswer";
import { relativeTime } from "@/lib/taskCard";
import { useCachedResource } from "@/lib/useCachedResource";

import { KIND_WORDS, fetchWaiting, forYou, type SeamRead, type Wait } from "./lib/forYou";
import {
  answersInPlace,
  openQuestion,
  rowAgent,
  rowTitle,
  taskHref,
  unanswered,
  waitingRuns,
  type WaitingRun,
} from "./lib/waitingForYou";

/** The seam's list. Under `PROJECTS_CACHE`, so a Projects write drops it. */
const SEAM_KEY = cacheKey(`${PROJECTS_CACHE}shell/waiting`, { direction: "for_you" });
/** The member's own agent questions. */
const AGENT_KEY = `${PROJECTS_CACHE}waiting/agent-questions`;
/** How often the page reads both lists again while it is open. */
const POLL_MS = 15_000;

async function fetchAgentQuestions(): Promise<WaitingRun[] | null> {
  const runs = await agentRunsApi.waiting();
  return runs === null ? null : waitingRuns(runs);
}

export default function WaitingPage() {
  const seam = useCachedResource<SeamRead>(SEAM_KEY, fetchWaiting);
  const agent = useCachedResource<WaitingRun[] | null>(AGENT_KEY, fetchAgentQuestions);
  // The ids of the QUESTIONS this page answered (never the run ids). They
  // leave at once, before the next read confirms it. A new question of the
  // same run has a new id, so it shows (`unanswered`).
  // read confirms it.
  const [answered, setAnswered] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [answerErrors, setAnswerErrors] = useState<Record<string, string>>({});

  const refreshSeam = seam.refresh;
  const refreshAgent = agent.refresh;
  const refresh = useCallback(() => {
    refreshSeam();
    refreshAgent();
  }, [refreshSeam, refreshAgent]);
  useEffect(() => {
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const runs = agent.data ? unanswered(agent.data, answered) : agent.data;
  const page = forYou({ data: seam.data, error: seam.error }, { data: runs, error: agent.error });
  const reading = seam.refreshing || agent.refreshing || seam.loading || agent.loading;

  const answer = async (run: WaitingRun, answers: ElicitationAnswers) => {
    const question = openQuestion(run);
    if (!question) return;
    setBusy((b) => ({ ...b, [run.id]: true }));
    setAnswerErrors((e) => {
      const n = { ...e };
      delete n[run.id];
      return n;
    });
    try {
      await agentRunsApi.post(run.id, answerPayload(question, answers));
      setAnswered((s) => new Set([...s, question.id]));
    } catch (err) {
      setAnswerErrors((e) => ({
        ...e,
        [run.id]: err instanceof Error ? err.message : "Could not send this answer.",
      }));
    } finally {
      setBusy((b) => {
        const n = { ...b };
        delete n[run.id];
        return n;
      });
    }
  };

  // No number in the bar: the sidebar row holds the one count, the seam's
  // (`waiting_on_me.md` §5.1). A second number here that adds the agent
  // questions would disagree with it until AP-1b.
  return (
    <div className="flex h-full min-h-0 flex-col">
      <AppTopBar
        title="Waiting"
        tools={
          <Button variant="ghost" size="sm" icon="RefreshCw" onClick={refresh} disabled={reading}>
            {reading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl px-4 py-6">
          <section aria-labelledby="waiting-for-you">
            <h2 id="waiting-for-you" className="text-sm font-semibold text-foreground">
              For you
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Every agent question, decision, request and held write that waits on your answer,
              oldest first.
            </p>

            {/* One live region, always mounted, so a screen reader hears what
                could not load when it arrives. */}
            <div role="status">
              {page.failed && (
                <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-destructive">
                  <span>{page.failed}</span>
                  <Button variant="secondary" size="sm" onClick={refresh} disabled={reading}>
                    Try again
                  </Button>
                </div>
              )}
              {page.off && page.view === "list" && (
                <p className="mt-4 text-xs text-muted-foreground">
                  Decisions, chat questions, app requests and held writes show here when Waiting is
                  on for your organization.
                </p>
              )}
            </div>

            {page.view === "loading" && (
              <div className="mt-4 flex flex-col gap-3" aria-hidden>
                <Skeleton className="h-16 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            )}

            {page.view === "off" && (
              <div className="mt-4 rounded-lg border border-border bg-card/50 px-4 py-8 text-center">
                <p className="text-sm text-muted-foreground">Waiting is not on for your organization yet.</p>
                <p className="mt-1 text-xs text-muted-foreground/70">
                  Your agent questions show here when an agent asks you one. Held writes stay in
                  Approvals.
                </p>
                <div className="mt-3">
                  <Button variant="secondary" size="sm" href="/approvals">
                    Open Approvals
                  </Button>
                </div>
              </div>
            )}

            {page.view === "empty" && (
              <div className="mt-4 rounded-lg border border-border bg-card/50 px-4 py-8 text-center">
                <p className="text-sm text-muted-foreground">Nothing waits on you.</p>
                <p className="mt-1 text-xs text-muted-foreground/70">
                  An agent question, a decision, a request or a held write shows here when it needs
                  your answer.
                </p>
              </div>
            )}

            {page.view === "list" && (
              <ul className="mt-4 flex flex-col gap-3">
                {page.entries.map((entry) =>
                  entry.type === "wait" ? (
                    <WaitRow key={entry.key} wait={entry.wait} />
                  ) : (
                    <AgentRow
                      key={entry.key}
                      run={entry.run}
                      busy={!!busy[entry.run.id]}
                      error={answerErrors[entry.run.id]}
                      onAnswer={(answers) => void answer(entry.run, answers)}
                    />
                  ),
                )}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

/** One row of the seam. The title and the waiter draw as text, never markup. */
function WaitRow({ wait }: { wait: Wait }) {
  const age = relativeTime(wait.since);
  const titleClass = "min-w-0 flex-1 basis-48 line-clamp-2 break-words text-sm font-medium text-foreground";
  return (
    <li className="rounded-lg border border-border bg-card/50 p-3 text-xs" data-wait-kind={wait.kind}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        {wait.href ? (
          <Link href={wait.href} className={`${titleClass} hover:underline`}>
            {wait.title}
          </Link>
        ) : (
          <span className={titleClass}>{wait.title}</span>
        )}
        <span className="text-muted-foreground">
          {wait.waiter}
          {age ? ` · ${age}` : ""}
        </span>
      </div>
      <p className="mt-0.5 text-muted-foreground">{KIND_WORDS[wait.kind].label}</p>
      {wait.acts.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {wait.acts.map((act) => (
            <Button key={act.id} variant="secondary" size="sm" href={act.href}>
              {act.label}
            </Button>
          ))}
        </div>
      )}
    </li>
  );
}

/** One agent question. It answers in place, unless it is an archive request. */
function AgentRow({
  run,
  busy,
  error,
  onAnswer,
}: {
  run: WaitingRun;
  busy: boolean;
  error: string | undefined;
  onAnswer: (answers: ElicitationAnswers) => void;
}) {
  const question = openQuestion(run);
  if (!question) return null;
  const age = relativeTime(question.created_at);
  return (
    <li className="rounded-lg border border-border bg-card/50 p-3 text-xs" data-wait-kind="agent_question">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <Link
          href={taskHref(run)}
          className="min-w-0 flex-1 basis-48 line-clamp-2 break-words text-sm font-medium text-foreground hover:underline"
        >
          {rowTitle(run)}
        </Link>
        <span className="text-muted-foreground">
          {rowAgent(run)}
          {age ? ` · ${age}` : ""}
        </span>
      </div>
      <p className="mt-0.5 text-muted-foreground">
        Agent question{run.task?.project_name ? ` · ${run.task.project_name}` : ""}
      </p>
      <div className="mt-2">
        {answersInPlace(run) ? (
          <ElicitationCard
            id={`waiting:${question.id}`}
            questions={[cardQuestion(question)]}
            onSubmit={onAnswer}
            disabled={busy}
          />
        ) : (
          <p className="whitespace-pre-wrap break-words text-foreground">
            {question.content.body?.trim() || "The agent makes a request."}
            <span className="ml-1 text-muted-foreground">Open the task to answer it.</span>
          </p>
        )}
      </div>
      {error && (
        <p className="mt-1.5 text-destructive" role="alert">
          {error} Try again, or open the task.
        </p>
      )}
    </li>
  );
}
