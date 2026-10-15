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
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { listMine, type AgentConnection } from "@/app/integrations/lib/agents";
import Button from "@/components/ui/Button";
import { Select, Textarea } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import { ProjectsApiError, agentRunsApi } from "../lib/api";
import type { AgentStartResult } from "../lib/agentRuns";
import {
  ASK_COPY,
  CONNECT_AGENT_HREF,
  MAX_INSTRUCTION,
  askableConnections,
  connectionLabel,
  startFailure,
  startPayload,
} from "../lib/askAgent";

interface Props {
  open: boolean;
  taskId: string;
  onClose: () => void;
  /** The run started. The panel shows it. */
  onStarted: (result: AgentStartResult) => void;
}

export function AskAgentDialog({ open, taskId, onClose, onStarted }: Props) {
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
    if (!payload || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await agentRunsApi.start(taskId, payload);
      setText("");
      onStarted(result);
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
      description={ASK_COPY.description}
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
            <label className="block space-y-1">
              <span className="text-muted-foreground">{ASK_COPY.agentLabel}</span>
              <Select
                value={connectionId}
                onChange={(e) => setConnectionId(e.target.value)}
                inputSize="sm"
                disabled={busy}
              >
                {rows.map((row) => (
                  <option key={row.id} value={row.id}>
                    {connectionLabel(row, rows)}
                  </option>
                ))}
              </Select>
            </label>
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

        {error ? (
          <p
            role="alert"
            className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive"
          >
            {error}
          </p>
        ) : null}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          {none || listFailed ? null : (
            <Button
              size="sm"
              icon="Send"
              disabled={!payload || busy}
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
