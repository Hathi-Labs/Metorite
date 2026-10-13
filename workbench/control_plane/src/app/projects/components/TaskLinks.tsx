"use client";

/**
 * The task card's Links section (WS-57 TR-2, `specs/task_references.md`).
 *
 * A task's pull requests, issues, commits, branches, comments, docs and plain
 * URLs. Paste a link into the field and it is added at once; type one and
 * press Enter. The gateway classifies it (`routes/projects/refs.py`) and, for
 * a GitHub pull request or issue, reads its title and state through the
 * organization's GitHub connection. A person, the chat agent (`add_task_ref`)
 * and, later, an outside agent over MCP (EA-6) all write this one list.
 *
 * Each row opens its link in a new tab, so the task stays where it is. The
 * drawing rules (icon, state chip, names) are `lib/taskLinks.ts`.
 */

import { useCallback, useEffect, useState } from "react";

import AppIcon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

import { refsApi, type TaskRefRow } from "../lib/api";
import { pastedLink, refIcon, refNames, refState } from "../lib/taskLinks";

export function TaskLinks({
  taskId,
  onCount,
}: {
  taskId: string;
  /** The row count, for the section header. */
  onCount?: (count: number) => void;
}) {
  const [rows, setRows] = useState<TaskRefRow[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    refsApi
      .list(taskId)
      .then((res) => {
        if (live) setRows(res.rows);
      })
      .catch(() => {
        // A card with no links section beats a card that fails to open.
        if (live) setRows([]);
      });
    return () => {
      live = false;
    };
  }, [taskId]);

  useEffect(() => {
    onCount?.(rows.length);
  }, [rows.length, onCount]);

  const add = useCallback(
    async (url: string) => {
      const value = url.trim();
      if (!value || busy) return;
      setBusy(true);
      setError(null);
      try {
        const row = await refsApi.add(taskId, value);
        // A second add of one URL answers with the row already there.
        setRows((current) =>
          current.some((r) => r.id === row.id)
            ? current.map((r) => (r.id === row.id ? row : r))
            : [...current, row],
        );
        setDraft("");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Couldn't add the link.");
      } finally {
        setBusy(false);
      }
    },
    [taskId, busy],
  );

  async function remove(row: TaskRefRow) {
    setRows((current) => current.filter((r) => r.id !== row.id));
    try {
      await refsApi.remove(taskId, row.id);
    } catch (err) {
      // Put it back, and say why.
      setRows((current) => (current.some((r) => r.id === row.id) ? current : [...current, row]));
      setError(err instanceof Error ? err.message : "Couldn't remove the link.");
    }
  }

  return (
    <div className="space-y-1">
      {rows.map((row) => {
        const { main, aside } = refNames(row);
        const state = refState(row.state);
        return (
          <div
            key={row.id}
            className="group flex items-center gap-2 rounded-md border border-border bg-card p-1.5 text-xs"
          >
            <a
              href={row.url}
              target="_blank"
              rel="noopener noreferrer"
              title={row.url}
              className="flex min-w-0 flex-1 items-center gap-2 text-left hover:text-primary"
            >
              <AppIcon name={refIcon(row)} className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 truncate text-foreground">{main}</span>
              {aside ? <span className="shrink-0 text-muted-foreground">{aside}</span> : null}
            </a>
            {state ? (
              <Badge tone={state.tone} title={`${row.label} is ${state.label.toLowerCase()}`}>
                {state.label}
              </Badge>
            ) : null}
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              aria-label={`Remove the link ${main}`}
              title="Remove this link"
              onClick={() => void remove(row)}
            />
          </div>
        );
      })}
      <Input
        icon="Link"
        value={draft}
        disabled={busy}
        onChange={(e) => {
          setDraft(e.target.value);
          if (error) setError(null);
        }}
        onPaste={(e) => {
          // A paste of one link into an empty field adds it at once.
          if (draft.trim()) return;
          const url = pastedLink(e.clipboardData.getData("text"));
          if (!url) return;
          e.preventDefault();
          void add(url);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void add(draft);
          }
        }}
        placeholder="Paste a link: a pull request, an issue, a commit, a doc…"
        aria-label="Add a link"
      />
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
