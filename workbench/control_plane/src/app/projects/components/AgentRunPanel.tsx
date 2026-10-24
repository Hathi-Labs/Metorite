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
 *
 * **Ask an agent (EA-12d).** While the routes are on and the caller has no
 * open run on the task, the panel offers "Ask an agent". It opens
 * `AskAgentDialog`, and the run that the dialog starts shows here as
 * "Queued for <client>".
 *
 * **An archive request (EA-6g, EA3-40).** The agent asks to archive the task.
 * Only the run's member answers: the same question card, with the server's
 * two options and no free text, writes through the approval route. Every
 * other member reads the question and "Waiting for <name>", with no button.
 * The reply box stays away while the request waits, because the server
 * refuses a reply. A request past its expiry reads as expired, and it is no
 * dead end (fix round 1): the member may Decline it, and the reply box comes
 * back for everyone, because the server takes a reply then.
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
  ARCHIVE_CARD_TITLE,
  ARCHIVE_EXPIRED_HINT,
  ARCHIVE_EXPIRED_TEXT,
  ARCHIVE_HINT,
  type AgentActivity,
  type AgentRun,
  type AgentStartResult,
  type AgentState,
  answerPayload,
  answerTo,
  answeredLine,
  archiveApprovalPayload,
  archiveCardQuestion,
  archiveDecisionOf,
  archiveExpired,
  archiveRequestOf,
  archiveWaitingText,
  canReleaseRun,
  cardQuestion,
  clientName,
  heldByLine,
  RELEASE_COPY,
  releaseBody,
  STALE_HOLD_TEXT,
  runIdentity,
  isOpenRun,
  isRunMember,
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
import { ASK_COPY, hasOpenRunOf } from "../lib/askAgent";
import { AgentPill } from "./AgentPill";
import { AskAgentDialog } from "./AskAgentDialog";

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
  const { access } = useAccess();
  const me = access.email;
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
  const [confirmRelease, setConfirmRelease] = useState(false);
  /** The caller has an open run on this task that the panel does not show. */
  const [otherOpenMine, setOtherOpenMine] = useState(false);
  const [asking, setAsking] = useState(false);

  // The newest values, for the poll's closure.
  const runRef = useRef<AgentRun | null>(null);
  const activitiesRef = useRef<AgentActivity[]>([]);
  const meRef = useRef(me);
  useEffect(() => {
    runRef.current = run;
    activitiesRef.current = activities;
    meRef.current = me;
  }, [run, activities, me]);

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
    setOtherOpenMine(false);
    setAsking(false);
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
        setOtherOpenMine(hasOpenRunOf(runs.filter((r) => r.id !== shown?.id), meRef.current));
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

  if (!enabled) return null;

  // EA-12d: the caller may ask an agent while they have no open run here.
  const canAsk = !otherOpenMine && !(run && isOpenRun(run) && hasOpenRunOf([run], me));

  function started(result: AgentStartResult) {
    setAsking(false);
    setActivities([]);
    setOptimistic(null);
    setSent(null);
    setError(null);
    runRef.current = result.run;
    activitiesRef.current = [];
    setRun(result.run);
    onActivity?.();
    void refresh(result.run.id).catch(() => undefined);
  }

  const askDialog = (
    <AskAgentDialog
      open={asking}
      taskId={task.id}
      onClose={() => setAsking(false)}
      onStarted={started}
    />
  );
  const askButton = canAsk ? (
    <Button
      variant="ghost"
      size="sm"
      icon="Bot"
      data-ask-agent=""
      onClick={() => setAsking(true)}
    >
      {ASK_COPY.open}
    </Button>
  ) : null;

  if (!run) {
    if (!canAsk) return null;
    return (
      <div className="mb-3 flex items-center" data-ask-agent-row="">
        {askButton}
        {askDialog}
      </div>
    );
  }

  const client = clientName(run.client_via);
  // The closures below cannot see the narrowing of `run` above.
  const shownRunId = run.id;
  const host = httpsHost(run.external_link);
  const ephemeral = newestEphemeral(activities);
  const elicitation = latestElicitation(activities, run);
  const answer =
    elicitation &&
    (answerTo(activities, elicitation) ??
      (sent && sent.in_reply_to === elicitation.id ? sent : null));
  const cardOpen = Boolean(elicitation && !answer && run.status === "awaiting");
  // EA-6g: a typed archive request. Only the run's member may answer it.
  const archiveRequest = cardOpen ? archiveRequestOf(elicitation) : null;
  const archiveMine = isRunMember(run, me);
  // An expired request is stale: the server takes a reply, and a decline.
  const archiveStale = archiveRequest !== null && archiveExpired(archiveRequest);
  const canStop = STOPPABLE.has(run.status);
  // EA-14a: one AI for each task. The card says who holds the task, and the
  // run's member, an assignee or an admin may release it.
  const holding = isOpenRun(run);
  const canRelease = canReleaseRun(run, {
    me,
    isAdmin: access.is_admin,
    assignees: task.assignees,
  });
  const canReply = REPLYABLE.has(run.status) && (!cardOpen || archiveStale);

  /** One member write: `send` posts it. The panel guesses the state first. */
  async function write(
    send: () => Promise<unknown>,
    guess: AgentState,
    failure: string,
  ) {
    if (!run) return false;
    setBusy(true);
    setError(null);
    setOptimistic(guess);
    try {
      await send();
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
    const ok = await write(
      () => agentRunsApi.post(shownRunId, body),
      "answered",
      "Could not send the answer.",
    );
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

  /** EA-6g: the run's member approves or declines the archive request. */
  async function submitArchive(answers: ElicitationAnswers) {
    if (!elicitation) return;
    const decision = archiveDecisionOf(elicitation, answers);
    if (decision) await sendDecision(decision);
  }

  async function sendDecision(decision: "approve" | "decline") {
    if (!elicitation) return;
    const body = archiveApprovalPayload(decision);
    let archivedAt: string | null | undefined;
    const ok = await write(
      async () => {
        const result = await agentRunsApi.archiveApproval(shownRunId, body);
        archivedAt = result.task?.archived_at;
      },
      "answered",
      "Could not send the decision.",
    );
    if (!ok) return;
    setSent({
      id: `local:${body.idempotency_key}`,
      seq: Number.MAX_SAFE_INTEGER,
      type: "prompt",
      content: { selected: [decision], archived: decision === "approve" },
      actor: me || null,
      in_reply_to: elicitation.id,
    });
    // The task is on the shelf now. The host learns it from the row.
    if (archivedAt) onTaskChanged?.({ ...task, archived_at: archivedAt });
  }

  async function sendReply() {
    const text = reply.trim();
    if (!text) return;
    const ok = await write(
      () => agentRunsApi.post(shownRunId, replyPayload(text)),
      "answered",
      "Could not send the reply.",
    );
    if (ok) setReply("");
  }

  async function release() {
    const ok = await write(
      () => agentRunsApi.release(shownRunId),
      "stale",
      RELEASE_COPY.failure,
    );
    if (ok) setConfirmRelease(false);
  }

  async function stop() {
    const ok = await write(
      () => agentRunsApi.post(shownRunId, stopPayload()),
      "stopping",
      "Could not stop the agent.",
    );
    if (ok) setConfirmStop(false);
  }

  return (
    <section
      aria-label={`Agent run of ${client}`}
      data-agent-run-panel={run.status}
      className="@container mb-3 space-y-2 rounded-lg border border-border bg-card p-3"
    >
      {/* One row that never wraps: the name and the pill give way, and the
          Stop control keeps its place at the end. The docked panel is about
          340 px wide, and a wrapped Stop read as a second control row. */}
      <div className="flex min-w-0 items-center gap-2" data-agent-run-header="">
        <Icon name="Bot" size={14} className="shrink-0 text-muted-foreground" aria-hidden />
        <span
          className="min-w-0 truncate text-sm font-medium text-foreground"
          // EA-13d: "Claude Code · claude.ai". The host is the one that the
          // gateway verified, so a client cannot name its own.
          data-agent-run-client=""
        >
          {runIdentity(run)}
        </span>
        <span className="flex min-w-0">
          <AgentPill
            state={state}
            size="sm"
            clientVia={run.client_via}
            memberEmail={run.member_email}
            thought={ephemeral}
            labels={personLabels}
          />
        </span>
        {canStop ? (
          // In the docked panel the control is its icon, with its name as
          // the label and the tip. A wider panel shows the words too.
          <Button
            className="ml-auto shrink-0"
            variant="ghost"
            size="sm"
            icon="CircleStop"
            aria-label="Stop agent"
            title="Stop agent"
            disabled={busy}
            onClick={() => setConfirmStop(true)}
          >
            <span className="hidden @md:inline">Stop agent</span>
          </Button>
        ) : null}
      </div>

      {/* EA-14a: who holds the task, and the Release control. */}
      {holding ? (
        <div className="flex min-w-0 items-center gap-2 text-xs" data-agent-held-by="">
          <p className="min-w-0 flex-1 truncate text-muted-foreground" title={heldByLine(run, personLabels)}>
            {heldByLine(run, personLabels)}
            {run.lease_expired ? (
              <span
                className="ml-1.5 rounded border border-border px-1 text-[10px] uppercase tracking-wide"
                data-agent-held-stale=""
                title="No activity for a long time. The next claim can take this task."
              >
                {STALE_HOLD_TEXT}
              </span>
            ) : null}
          </p>
          {canRelease ? (
            <Button
              className="shrink-0"
              variant="ghost"
              size="sm"
              icon="LogOut"
              data-agent-release=""
              disabled={busy}
              onClick={() => setConfirmRelease(true)}
            >
              {RELEASE_COPY.button}
            </Button>
          ) : null}
        </div>
      ) : null}

      {/* The second line: the newest thought, and the session link. */}
      {(ephemeral && run.status !== "failed") || host ? (
        <div className="flex min-w-0 items-center gap-2 text-xs">
          {ephemeral && run.status !== "failed" ? (
            <p
              className="min-w-0 flex-1 truncate text-muted-foreground"
              title={ephemeral}
              data-agent-ephemeral=""
            >
              {ephemeral}
            </p>
          ) : null}
          {host ? (
            <a
              href={run.external_link ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto inline-flex min-w-0 shrink-0 items-center gap-1 text-primary hover:underline"
              title={`Open the session on ${host}`}
            >
              <Icon name="ExternalLink" size={12} className="shrink-0" aria-hidden />
              <span className="truncate">{host}</span>
            </a>
          ) : null}
        </div>
      ) : null}

      {cardOpen && elicitation && !archiveRequest ? (
        <ElicitationCard
          id={`agent-run:${elicitation.id}`}
          questions={[cardQuestion(elicitation)]}
          onSubmit={(answers) => void submitAnswer(answers)}
          disabled={busy}
          hint={AGENT_SUBMIT_HINT}
        />
      ) : null}

      {archiveRequest && elicitation ? (
        archiveStale ? (
          <div data-archive-request="expired" className="space-y-1.5 text-xs">
            <p className="flex items-start gap-1.5 text-muted-foreground">
              <Icon name="Clock" size={12} className="mt-0.5 shrink-0" aria-hidden />
              <span className="min-w-0 break-words">{ARCHIVE_EXPIRED_TEXT}</span>
            </p>
            {archiveMine ? (
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  icon="X"
                  disabled={busy}
                  data-archive-decline=""
                  onClick={() => void sendDecision("decline")}
                >
                  Decline
                </Button>
                <span className="text-[10px] text-muted-foreground">{ARCHIVE_EXPIRED_HINT}</span>
              </div>
            ) : null}
          </div>
        ) : archiveMine ? (
          <div data-archive-request="mine">
            <ElicitationCard
              id={`agent-run:${elicitation.id}`}
              questions={[archiveCardQuestion(elicitation)]}
              onSubmit={(answers) => void submitArchive(answers)}
              disabled={busy}
              hint={ARCHIVE_HINT}
              title={ARCHIVE_CARD_TITLE}
            />
          </div>
        ) : (
          // Every other member reads the request and who must answer it.
          <div
            data-archive-request="waiting"
            className="space-y-1 rounded-md border border-border p-2 text-xs"
          >
            <p className="flex items-start gap-1.5 text-foreground">
              <Icon name="Archive" size={12} className="mt-0.5 shrink-0" aria-hidden />
              <span className="min-w-0 whitespace-pre-wrap break-words">
                {elicitation.content.body?.trim() || `${client} asks to archive this task.`}
              </span>
            </p>
            <p className="text-muted-foreground" data-archive-waiting="">
              {archiveWaitingText(run, personLabels)}
            </p>
          </div>
        )
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

      {askButton ? <div className="flex items-center">{askButton}</div> : null}
      {askDialog}

      <ConfirmDialog
        open={confirmRelease}
        title={RELEASE_COPY.title}
        body={releaseBody(client)}
        confirmLabel={RELEASE_COPY.confirm}
        icon="LogOut"
        busy={busy}
        onConfirm={() => void release()}
        onCancel={() => setConfirmRelease(false)}
      />

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
