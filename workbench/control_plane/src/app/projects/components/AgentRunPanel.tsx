"use client";

/**
 * The agent run panel, at the top of a task's discussion (WS-55 EA-12c,
 * `external_agent_access.md` §12.1).
 *
 * An outside agent (Claude Code, through our MCP server) works on a task in a
 * RUN. This panel shows the open run: the client, its state as the shared
 * agent pill, the newest thought or action, and the link to the session.
 *
 * - An `elicitation` draws as the chat's question card (`ElicitationCard`),
 *   the same component the assistant's questions use. Submit writes a
 *   `prompt` with the picked option ids and the typed text. After the answer
 *   the card collapses to "Answered by <name>: <labels>".
 * - A reply box writes a free `prompt`.
 * - Stop writes a `prompt` with the signal `stop`, after a confirm.
 *
 * The panel reads the run again every 5 seconds while the run is open and
 * the panel is mounted. It asks only for the activities after the newest
 * `seq` it holds.
 *
 * **Gated on the API.** While the flag `agent_runs_enabled` is off, every
 * route answers 404, and this panel draws nothing at all.
 *
 * It lives in `TaskBody`, so both hosts (Projects and My Tasks) show it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import ElicitationCard from "@/components/ElicitationCard";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Textarea } from "@/components/ui/Input";
import { useAccess } from "@/components/AccessProvider";
import type { ElicitationAnswers } from "@/lib/elicitationAnswer";

import { ProjectsApiError, type TaskRow, agentRunsApi } from "../lib/api";
import {
  type AgentActivity,
  type AgentPromptBody,
  type AgentRun,
  type AgentState,
  answerPayload,
  answerTo,
  answeredLine,
  cardQuestion,
  clientName,
  isOpenRun,
  lastSeq,
  latestElicitation,
  mergeActivities,
  newestEphemeral,
  panelState,
  replyPayload,
  runToShow,
  stopPayload,
  writeFailure,
} from "../lib/agentRuns";
import { AgentPill } from "./AgentPill";

/** How often the open run is read again. */
export const AGENT_RUN_POLL_MS = 5_000;

/** The line beside the card's Submit. The chat's own line names "the assistant". */
export const AGENT_SUBMIT_HINT = "The agent reads your answer at its next step.";

/** The statuses that take a member's Stop (§12.1, the status machine). */
const STOPPABLE = new Set(["created", "in_progress", "awaiting"]);
/** The statuses that take a member's reply. */
const REPLYABLE = new Set(["in_progress", "awaiting", "completed", "stale"]);

/** The host of an `https` link, or null for any other link. */
function httpsHost(link?: string | null): string | null {
  if (!link) return null;
  try {
    const url = new URL(link);
    return url.protocol === "https:" ? url.host : null;
  } catch {
    return null;
  }
}

interface Props {
  task: TaskRow;
  personLabels?: ReadonlyMap<string, string>;
  /** Merge the run's state into the task, so each pill agrees with the panel. */
  onTaskChanged?: (task: TaskRow) => void;
  /** A write added a comment row. The host reads its discussion again. */
  onActivity?: () => void;
}

export function AgentRunPanel({ task, personLabels, onTaskChanged, onActivity }: Props) {
  const me = useAccess().access.email;
  /** `null` until the first read. `false` when the routes are off. */
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [activities, setActivities] = useState<AgentActivity[]>([]);
  /** The state the panel shows between a write and the re-read. */
  const [optimistic, setOptimistic] = useState<AgentState | null>(null);
  /** An answer sent from here, until the re-read carries it. */
  const [sent, setSent] = useState<AgentActivity | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);

  // The newest values, for the poll's closure.
  const runRef = useRef<AgentRun | null>(null);
  const activitiesRef = useRef<AgentActivity[]>([]);
  useEffect(() => {
    runRef.current = run;
    activitiesRef.current = activities;
  }, [run, activities]);

  /** Read the run again: the activities after the newest `seq` held. */
  const refresh = useCallback(async (runId: string) => {
    const detail = await agentRunsApi.get(runId, lastSeq(activitiesRef.current));
    if (runRef.current?.id !== runId) return;
    setRun((current) => ({ ...(current ?? detail.run), ...detail.run }));
    setActivities((held) => mergeActivities(held, detail.activities));
    setOptimistic(null);
  }, []);

  // The first read, for each task.
  useEffect(() => {
    let live = true;
    // Reset FIRST, so the next task never shows the last task's run.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRun(null);
    setActivities([]);
    setOptimistic(null);
    setSent(null);
    setReply("");
    setError(null);
    void (async () => {
      try {
        const runs = await agentRunsApi.list(task.id);
        if (!live) return;
        if (runs === null) {
          setEnabled(false);
          return;
        }
        setEnabled(true);
        const shown = runToShow(runs);
        if (!shown) return;
        runRef.current = shown;
        setRun(shown);
        const detail = await agentRunsApi.get(shown.id);
        if (!live) return;
        setRun({ ...shown, ...detail.run });
        setActivities(mergeActivities([], detail.activities));
      } catch {
        // A failed read must not blank the task. The panel stays away.
        if (live) setEnabled(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [task.id]);

  // Poll while the run is open.
  const runId = run?.id ?? null;
  const open = run ? isOpenRun(run) : false;
  useEffect(() => {
    if (!runId || !open) return;
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      void refresh(runId).catch(() => undefined);
    }, AGENT_RUN_POLL_MS);
    return () => window.clearInterval(timer);
  }, [runId, open, refresh]);

  const state = run ? panelState(run, optimistic) : null;

  // Each pill reads the task row. Merge the panel's facts into it.
  useEffect(() => {
    if (!enabled || !onTaskChanged) return;
    const next = {
      agent_state: state,
      agent_client_via: run?.client_via ?? null,
      agent_member_email: run?.member_email ?? null,
    };
    if (
      (task.agent_state ?? null) === next.agent_state &&
      (task.agent_client_via ?? null) === next.agent_client_via &&
      (task.agent_member_email ?? null) === next.agent_member_email
    ) {
      return;
    }
    onTaskChanged({ ...task, ...next });
    // Only a change of the run's facts reports. The task object changes on
    // every merge, and listing it would report again for each one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, state, run?.client_via, run?.member_email]);

  if (!enabled || !run) return null;

  const client = clientName(run.client_via);
  const host = httpsHost(run.external_link);
  const ephemeral = newestEphemeral(activities);
  const elicitation = latestElicitation(activities, run);
  const answer =
    elicitation &&
    (answerTo(activities, elicitation) ??
      (sent && sent.in_reply_to === elicitation.id ? sent : null));
  const cardOpen = Boolean(elicitation && !answer && run.status === "awaiting");
  const canStop = STOPPABLE.has(run.status);
  const canReply = REPLYABLE.has(run.status) && !cardOpen;

  async function write(body: AgentPromptBody, guess: AgentState, failure: string) {
    if (!run) return false;
    setBusy(true);
    setError(null);
    setOptimistic(guess);
    try {
      await agentRunsApi.post(run.id, body);
      onActivity?.();
      void refresh(run.id).catch(() => undefined);
      return true;
    } catch (err) {
      setOptimistic(null);
      const status = err instanceof ProjectsApiError ? err.status : undefined;
      const message = err instanceof Error ? err.message : undefined;
      const failed = writeFailure(status, message, failure);
      setError(failed.text);
      // A 409: the run moved under the member. Show its newest state.
      if (failed.reload) void refresh(run.id).catch(() => undefined);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function submitAnswer(answers: ElicitationAnswers) {
    if (!elicitation) return;
    const body = answerPayload(elicitation, answers);
    const ok = await write(body, "answered", "Could not send the answer.");
    if (ok) {
      setSent({
        id: `local:${body.idempotency_key}`,
        seq: Number.MAX_SAFE_INTEGER,
        type: "prompt",
        content: { body: body.body ?? null, selected: body.selected ?? [] },
        actor: me || null,
        in_reply_to: elicitation.id,
      });
    }
  }

  async function sendReply() {
    const text = reply.trim();
    if (!text) return;
    const ok = await write(replyPayload(text), "answered", "Could not send the reply.");
    if (ok) setReply("");
  }

  async function stop() {
    const ok = await write(stopPayload(), "stopping", "Could not stop the agent.");
    if (ok) setConfirmStop(false);
  }

  return (
    <section
      aria-label={`Agent run of ${client}`}
      data-agent-run-panel={run.status}
      className="mb-3 space-y-2 rounded-lg border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Icon name="Bot" size={14} className="shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-sm font-medium text-foreground">{client}</span>
        <AgentPill
          state={state}
          size="sm"
          clientVia={run.client_via}
          memberEmail={run.member_email}
          thought={ephemeral}
          labels={personLabels}
        />
        {host ? (
          <a
            href={run.external_link ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-w-0 items-center gap-1 text-xs text-primary hover:underline"
            title={`Open the session on ${host}`}
          >
            <Icon name="ExternalLink" size={12} className="shrink-0" aria-hidden />
            <span className="truncate">{host}</span>
          </a>
        ) : null}
        {canStop ? (
          <Button
            className="ml-auto"
            variant="ghost"
            size="sm"
            icon="CircleStop"
            disabled={busy}
            onClick={() => setConfirmStop(true)}
          >
            Stop agent
          </Button>
        ) : null}
      </div>

      {ephemeral && run.status !== "failed" ? (
        <p className="truncate text-xs text-muted-foreground" title={ephemeral} data-agent-ephemeral="">
          {ephemeral}
        </p>
      ) : null}

      {cardOpen && elicitation ? (
        <ElicitationCard
          id={`agent-run:${elicitation.id}`}
          questions={[cardQuestion(elicitation)]}
          onSubmit={(answers) => void submitAnswer(answers)}
          disabled={busy}
          hint={AGENT_SUBMIT_HINT}
        />
      ) : null}

      {elicitation && answer ? (
        <p
          data-agent-answered=""
          className="flex items-start gap-1.5 text-xs text-muted-foreground"
        >
          <Icon name="CircleCheck" size={12} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0 break-words">
            {answeredLine(answer, elicitation, personLabels)}
          </span>
        </p>
      ) : null}

      {canReply ? (
        <div className="space-y-1.5">
          <Textarea
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            aria-label={`Reply to ${client}`}
            placeholder={`Reply to ${client}…`}
            rows={2}
            inputSize="sm"
            className="resize-none"
            disabled={busy}
          />
          <Button
            size="sm"
            variant="secondary"
            icon="Send"
            disabled={busy || !reply.trim()}
            onClick={() => void sendReply()}
          >
            Send reply
          </Button>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}

      <ConfirmDialog
        open={confirmStop}
        title="Stop the agent?"
        body={`${client} stops at its next step. After that, it cannot continue this run.`}
        confirmLabel="Stop agent"
        confirmVariant="destructive"
        icon="CircleStop"
        busy={busy}
        onConfirm={() => void stop()}
        onCancel={() => setConfirmStop(false)}
      />
    </section>
  );
}
