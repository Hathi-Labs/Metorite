"use client";

/**
 * Projects · subtasks and dependencies in the task panel (WS-27p).
 *
 * Both existed in the schema since WS-27a and neither had a surface: links
 * could be created and deleted but never listed, and subtasks could be created
 * but never shown. *"Data with no surface is a promise the product does not
 * keep."*
 *
 * **Blocked by comes first**, because it is the only section that changes what
 * somebody should do next; the rest is context. A blocker that has finished
 * disappears from it — the gateway derives that — so the section going quiet is
 * how you learn you can start.
 *
 * WS-27bd item 2 — **unlinking is per row.** It used to be one shared `error`
 * string and no pending state at all: three unlinks in a row showed nothing
 * happening, and a refusal from one of them was printed at the top of the
 * block, unattached to any of the three. The state is now `lib/rowState.ts`'s
 * `pending: Set<id>` / `errors: Map<id, string>`, kept in a pure reducer so
 * "three concurrent operations, one attributed failure" is a unit test
 * (`rowState.test.ts`) rather than something only a human with three slow
 * network calls could ever see.
 */

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useCallback, useEffect, useReducer, useState } from "react";

import type { TaskRow } from "../lib/api";
import { projectsApi } from "../lib/api";
import { NO_ROWS, errorFor, isPending, rowsReducer } from "../lib/rowState";
import { conflictLabel, conflicts } from "../lib/timeline";
import {
  type LinkType,
  type Relations,
  isResolved,
  populated,
  progressLabel,
  progressPercent,
} from "../lib/relations";

const SELECT =
  "cc-control rounded-lg border border-border bg-background px-2 py-1.5 " +
  "text-xs text-foreground outline-none focus:border-primary/50";

const LINK_LABELS: Array<[LinkType, string]> = [
  ["blocks", "blocks"],
  ["relates_to", "relates to"],
  ["duplicates", "duplicates"],
];

interface Props {
  taskId: string;
  /**
   * WS-27t — the task this block belongs to, so the schedule warning can be
   * computed here as well as on the timeline. Optional because the rule is a
   * courtesy: without it the block still lists everything, it just cannot say
   * that two of the dates disagree.
   */
  task?: Pick<TaskRow, "start_date" | "due_at">;
  /** Bumped by the panel when it adds a subtask, so this reloads. */
  refreshKey?: number;
  onOpenTask: (taskId: string) => void;
}

export function RelationsBlock({
  taskId,
  task,
  refreshKey = 0,
  onOpenTask,
}: Props) {
  const [data, setData] = useState<Relations | null>(null);
  /**
   * The ADD form's own failure. Deliberately not folded into `rows`: the form
   * is one control, not a row, and its refusal ("that link would make a loop")
   * belongs beside the field somebody is still typing in.
   */
  const [error, setError] = useState<string | null>(null);
  const [linking, setLinking] = useState(false);
  const [target, setTarget] = useState("");
  const [kind, setKind] = useState<LinkType>("blocks");
  const [busy, setBusy] = useState(false);
  /** Per-link-row pending and error, keyed by `link_id` (WS-27bd item 2). */
  const [rows, dispatch] = useReducer(rowsReducer, NO_ROWS);

  const load = useCallback(async () => {
    try {
      setData(await projectsApi.relations(taskId));
    } catch {
      // A panel that works without its relations block beats one that refuses
      // to open because the block did not load.
      setData(null);
    }
  }, [taskId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  /**
   * Row state cannot outlive its row.
   *
   * Every reload prunes to the link ids actually on screen, so a spinner or a
   * message keyed to a link that has since gone (unlinked from here, or from
   * the other end of the relation) is dropped rather than left invisible and
   * permanent. `rowsReducer` returns the SAME object when nothing changed,
   * which is what keeps this effect from re-rendering itself.
   */
  useEffect(() => {
    if (!data) return;
    dispatch({
      kind: "prune",
      ids: [...data.links, ...data.blocked_by].map((link) => link.link_id),
    });
  }, [data]);

  if (!data) return null;

  const sections = populated(data.links);
  const hasAnything = data.subtasks.length > 0 || sections.length > 0;

  async function addLink(event: React.FormEvent) {
    event.preventDefault();
    const id = target.trim();
    if (!id) return;
    setBusy(true);
    setError(null);
    try {
      await projectsApi.createLink(taskId, id, kind);
      setTarget("");
      setLinking(false);
      await load();
    } catch (err) {
      // The gateway refuses a loop with an explanation; showing it verbatim is
      // better than paraphrasing a rule the server owns.
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  /**
   * Unlink ONE row, and say so on that row.
   *
   * Three of these can be in flight at once and each keeps its own spinner;
   * a refusal is written under the row it was about, not at the top of the
   * block where it could be read as being about any of them. The previous
   * error is deliberately NOT cleared on entry — it clears on success — so a
   * retry does not blank the explanation at the moment somebody is reading it
   * (`lib/rowState.ts` writes the rule out).
   */
  async function removeLink(linkId: string) {
    dispatch({ kind: "start", id: linkId });
    try {
      await projectsApi.deleteLink(taskId, linkId);
      dispatch({ kind: "settled", id: linkId });
      await load();
    } catch (err) {
      dispatch({
        kind: "failed",
        id: linkId,
        message: String((err as Error).message),
      });
    }
  }

  return (
    <div className="space-y-2">
      {error ? (
        <p className="rounded-md bg-muted px-2 py-1 text-xs text-foreground">
          {error}
        </p>
      ) : null}

      {data.blocked_by.length ? (
        <Badge tone="warning" icon="Ban">
          Blocked by {data.blocked_by.length}
        </Badge>
      ) : null}

      {/* WS-27t / D-PM-12 — the warning half of "constrain, but only warn".
          Nothing here reschedules anything, and the sentence says so: a user
          who assumes the tool fixed it is worse off than one who was never
          told. Same pure rule as the timeline's red arrow. */}
      {task
        ? data.blocked_by
            .filter((blocker) => conflicts(blocker, task))
            .map((blocker) => (
              <p
                key={`clash:${blocker.link_id}`}
                className="flex items-start gap-1.5 rounded border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs text-foreground"
              >
                <Icon
                  name="AlertTriangle"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive"
                />
                <span>{conflictLabel(blocker.title)}</span>
              </p>
            ))
        : null}

      {data.subtasks.length ? (
        <div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">Subtasks</span>
            <span className="text-xs text-muted-foreground">
              {progressLabel(data.progress)}
            </span>
          </div>
          <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary"
              style={{ width: `${progressPercent(data.progress)}%` }}
            />
          </div>
          <ul className="mt-1 space-y-0.5">
            {data.subtasks.map((child) => (
              <li key={child.id}>
                <button
                  type="button"
                  onClick={() => onOpenTask(child.id)}
                  className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left text-xs hover:bg-muted"
                >
                  <Icon
                    name={isResolved(child.category) ? "CheckCircle2" : "Circle"}
                    size={12}
                    className="shrink-0 text-muted-foreground"
                  />
                  <span
                    className={`min-w-0 flex-1 truncate text-foreground ${
                      isResolved(child.category) ? "line-through opacity-60" : ""
                    }`}
                  >
                    {child.title}
                  </span>
                  <span className="shrink-0 text-muted-foreground">
                    {child.status_name}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {sections.map((s) => (
        <div key={s.key}>
          <span className="text-xs text-muted-foreground">{s.label}</span>
          <ul className="mt-0.5 space-y-0.5">
            {s.links.map((l) => {
              // WS-27bd item 2 — this row's own state. Two rows unlinking at
              // once show two spinners, and a refusal names the row it hit.
              const pending = isPending(rows, l.link_id);
              const failure = errorFor(rows, l.link_id);
              return (
                <li key={l.link_id}>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => onOpenTask(l.id)}
                      className="flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-0.5 text-left text-xs hover:bg-muted"
                    >
                      {l.task_number ? (
                        <span className="shrink-0 text-muted-foreground">
                          #{l.task_number}
                        </span>
                      ) : null}
                      <span
                        className={`min-w-0 flex-1 truncate text-foreground ${
                          isResolved(l.category) ? "line-through opacity-60" : ""
                        }`}
                      >
                        {l.title}
                      </span>
                      <span className="shrink-0 text-muted-foreground">
                        {l.status_name}
                      </span>
                    </button>
                    {/* `loading` on THIS button only — a single shared flag
                        would disable every row in the block, so two thirds of
                        a working list would look broken. The primitive already
                        disables what it is spinning on. */}
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      icon="X"
                      loading={pending}
                      aria-label={`Unlink ${l.title}`}
                      onClick={() => void removeLink(l.link_id)}
                    />
                  </div>
                  {failure ? (
                    <p
                      role="alert"
                      className="mt-0.5 flex items-start gap-1.5 rounded border border-destructive/40 bg-destructive/10 px-2 py-1 text-[11px] text-foreground"
                    >
                      <Icon
                        name="AlertTriangle"
                        className="mt-0.5 h-3 w-3 shrink-0 text-destructive"
                      />
                      <span className="min-w-0 flex-1">{failure}</span>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        icon="X"
                        aria-label={`Dismiss the error on ${l.title}`}
                        onClick={() => dispatch({ kind: "dismiss", id: l.link_id })}
                      />
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ))}

      {linking ? (
        <form onSubmit={addLink} className="flex flex-wrap items-center gap-1">
          <span className="text-xs text-muted-foreground">This</span>
          <select
            aria-label="Link type"
            className={SELECT}
            value={kind}
            onChange={(e) => setKind(e.target.value as LinkType)}
          >
            {LINK_LABELS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <Input
            autoFocus
            inputSize="sm"
            className="min-w-[12rem] flex-1"
            aria-label="Task id to link"
            placeholder="task id"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setLinking(false);
            }}
          />
          <Button type="submit" size="sm" loading={busy} disabled={!target.trim()}>
            Link
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setLinking(false)}>
            Cancel
          </Button>
        </form>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          icon="Link2"
          onClick={() => setLinking(true)}
        >
          {hasAnything ? "Link another task" : "Link a task"}
        </Button>
      )}
    </div>
  );
}
