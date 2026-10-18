"use client";

/**
 * "Ask an agent" (WS-55 EA-12d, `external_agent_access.md` §12.1).
 *
 * A member picks one of their own live agent connections and writes the
 * instruction. Send starts a `created` run with that `prompt`, and the run
 * panel then shows "Queued for <client>". The rules are in
 * `lib/askAgent.ts`. This file only draws them.
 *
 * The member's connections come from the Integrations client (`listMine`,
 * EA-4b). There is one reader of that list, not a second one here.
 *
 * **One task or many (WS-55 EA-13e, EA3-43).** The run panel gives one
 * `taskId` and reads `onStarted`, as before. The board's menu and its bulk bar
 * give `tasks`, at most `MAX_BULK_ASK`. Then Send starts one run for each task,
 * each with its own idempotency key, and the dialog shows the result of each
 * task on its own row. A 409 shows the server's text on that row, and the
 * other tasks still go. No new route: it is the one start of EA-12d.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { listMine, type AgentConnection } from "@/app/integrations/lib/agents";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import SelectButton from "@/components/ui/SelectButton";

import { ProjectsApiError, agentRunsApi } from "../lib/api";
import type { AgentStartResult } from "../lib/agentRuns";
import {
  ASK_COPY,
  type AskOutcome,
  type AskTarget,
  CONNECT_AGENT_HREF,
  MAX_BULK_ASK,
  MAX_INSTRUCTION,
  askableConnections,
  bulkDescription,
  bulkLimitText,
  connectionLabel,
  outcomeLine,
  startFailure,
  startMany,
  startPayload,
} from "../lib/askAgent";

interface Props {
  open: boolean;
  /** One task, from the run panel. Give this or `tasks`. */
  taskId?: string;
  /** One or more tasks, from the board (EA-13e). At most `MAX_BULK_ASK`. */
  tasks?: readonly AskTarget[];
  onClose: () => void;
  /** The run started, for `taskId`. The panel shows it. */
  onStarted?: (result: AgentStartResult) => void;
  /** Every task of `tasks` was tried. The board reads its tasks again. */
  onFinished?: (outcomes: AskOutcome[]) => void;
}

export function AskAgentDialog({ open, taskId, tasks, onClose, onStarted, onFinished }: Props) {
  const targets: readonly AskTarget[] = tasks ?? (taskId ? [{ id: taskId }] : []);
  const overLimit = targets.length > MAX_BULK_ASK;
  /** The result of each task after a bulk Send. */
  const [outcomes, setOutcomes] = useState<AskOutcome[] | null>(null);
  /** `null` while the list loads. */
  const [rows, setRows] = useState<AgentConnection[] | null>(null);
  const [listFailed, setListFailed] = useState(false);
  const [connectionId, setConnectionId] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    // Each open reads the list again: the member may have connected an agent
    // in another tab since the last open.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRows(null);
    setListFailed(false);
    setError(null);
    setOutcomes(null);
    void listMine()
      .then((found) => {
        if (!live) return;
        const usable = askableConnections(found);
        setRows(usable);
        setConnectionId((current) =>
          usable.some((row) => row.id === current) ? current : (usable[0]?.id ?? ""),
        );
      })
      .catch(() => {
        if (live) {
          setRows([]);
          setListFailed(true);
        }
      });
    return () => {
      live = false;
    };
  }, [open]);

  const payload = startPayload(connectionId, text);

  async function send() {
    if (!payload || busy || overLimit) return;
    setBusy(true);
    setError(null);
    if (tasks) {
      try {
        const done = await startMany(tasks, connectionId, text, agentRunsApi.start);
        setOutcomes(done);
        if (done.every((o) => o.ok)) setText("");
        onFinished?.(done);
      } finally {
        setBusy(false);
      }
      return;
    }
    try {
      const result = await agentRunsApi.start(targets[0].id, payload);
      setText("");
      onStarted?.(result);
    } catch (err) {
      const status = err instanceof ProjectsApiError ? err.status : undefined;
      setError(startFailure(status, err instanceof Error ? err.message : undefined));
    } finally {
      setBusy(false);
    }
  }

  const loading = rows === null;
  const none = !loading && rows.length === 0;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={ASK_COPY.title}
      icon="Bot"
      size="md"
      description={tasks ? bulkDescription(targets.length) : ASK_COPY.description}
    >
      <div className="space-y-3 p-3 text-xs" data-ask-agent-dialog="">
        {loading ? (
          <p className="text-muted-foreground">{ASK_COPY.loading}</p>
        ) : listFailed ? (
          <p role="alert" className="text-destructive">
            {ASK_COPY.listFailed}
          </p>
        ) : none ? (
          <div className="space-y-1" data-ask-agent-empty="">
            <p className="text-sm text-foreground">{ASK_COPY.empty}</p>
            <p className="text-muted-foreground">
              {ASK_COPY.emptyHint}{" "}
              <Link href={CONNECT_AGENT_HREF} className="text-primary hover:underline">
                {ASK_COPY.connect}
              </Link>
            </p>
          </div>
        ) : (
          <>
            <div className="space-y-1">
              <span className="block text-muted-foreground">{ASK_COPY.agentLabel}</span>
              <SelectButton
                label={ASK_COPY.agentLabel}
                value={connectionId}
                onChange={setConnectionId}
                options={rows.map((row) => ({
                  value: row.id,
                  label: connectionLabel(row, rows),
                }))}
                widthClass="max-w-full"
                disabled={busy}
              />
            </div>
            <label className="block space-y-1">
              <span className="text-muted-foreground">{ASK_COPY.instructionLabel}</span>
              <Textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={ASK_COPY.instructionPlaceholder}
                rows={5}
                inputSize="sm"
                maxLength={MAX_INSTRUCTION}
                required
                disabled={busy}
              />
            </label>
          </>
        )}

        {overLimit ? (
          <p role="alert" data-ask-agent-limit="" className="text-destructive">
            {bulkLimitText(targets.length)}
          </p>
        ) : null}

        {error ? (
          <p
            role="alert"
            className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive"
          >
            {error}
          </p>
        ) : null}

        {outcomes ? (
          <div className="space-y-1" data-ask-agent-results="">
            <p className="text-muted-foreground">{outcomeLine(outcomes)}</p>
            <ul className="space-y-1">
              {outcomes.map((outcome) => {
                const target = targets.find((t) => t.id === outcome.taskId);
                return (
                  <li
                    key={outcome.taskId}
                    data-ask-agent-result={outcome.ok ? "sent" : "refused"}
                    className="flex items-start gap-1.5"
                  >
                    <Icon
                      name={outcome.ok ? "Check" : "CircleAlert"}
                      size={12}
                      className={`mt-0.5 shrink-0 ${outcome.ok ? "text-success" : "text-destructive"}`}
                      aria-hidden
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-foreground">
                        {target?.title?.trim() || "Task"}
                      </span>
                      <span className={outcome.ok ? "text-muted-foreground" : "text-destructive"}>
                        {outcome.ok ? ASK_COPY.sentOne : outcome.error}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            {outcomes ? ASK_COPY.done : "Cancel"}
          </Button>
          {none || listFailed || outcomes ? null : (
            <Button
              size="sm"
              icon="Send"
              disabled={!payload || busy || overLimit}
              onClick={() => void send()}
            >
              {busy ? ASK_COPY.sending : ASK_COPY.send}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
