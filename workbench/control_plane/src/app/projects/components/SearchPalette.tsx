"use client";

/**
 * Projects · the search palette (WS-27r).
 *
 * `⌘K` from anywhere in Projects, type, arrow to a hit, Enter to open it. The
 * last row of the parity backlog: `?q=` has been on the list endpoint since
 * WS-27a with no way to reach it.
 *
 * **A palette rather than a search page**, because the question it answers is
 * *"where is that task"* — asked while doing something else, usually about
 * work in a project the person is not looking at. A page would make finding
 * something a place you navigate TO, which is one navigation more than the
 * problem has.
 *
 * All of the logic — the debounce, what to show while a request is in flight,
 * which keystrokes belong to the palette, and how to ignore a stale response —
 * is in `lib/search.ts` and tested there. Those are the rules that only break
 * under real typing speed on a real connection, which is not a thing a
 * component test reproduces.
 *
 * ## Commands (WS-27ab item 3)
 *
 * It is a command palette now, not only a task finder. **The commands are a
 * declared registry** (`lib/commands.ts`), not branches in this file: one list
 * that the palette lists, the key sequences run, and the `?` sheet is printed
 * from — so the help cannot describe a behaviour this component does not have.
 * Nothing about a command is decided here; this renders `paletteList` and
 * calls `command.run`.
 *
 * **Commands sit above the task hits.** They are local and instant; the hits
 * arrive after a debounce, and a list that reorders itself under the cursor
 * when the response lands is a list that opens the wrong thing. Rows appearing
 * *below* the cursor cannot move what is already under it.
 */

import Icon from "@/components/Icon";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { useEffect, useRef, useState } from "react";

import { projectsApi } from "../lib/api";
import {
  type CommandActions,
  type CommandContext,
  paletteCommands,
  paletteList,
  sequenceLabel,
} from "../lib/commands";
import {
  DEBOUNCE_MS,
  type Hit,
  highlight,
  hitContext,
  isCurrent,
  moveSelection,
  paletteKey,
  paletteState,
} from "../lib/search";

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenTask: (taskId: string) => void;
  /**
   * WS-27ab — what a command is allowed to do; the Projects page supplies
   * every one. My Tasks mounts this palette for its own ⌘K and passes neither
   * this nor `context`, and then the palette is task search only
   * (`paletteCommands`).
   */
  actions?: CommandActions;
  /** Where the app is, so a command that would no-op is not offered. */
  context?: CommandContext;
}

export function SearchPalette({
  open,
  onClose,
  onOpenTask,
  actions,
  context,
}: Props) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  // Read inside the async callback so a response can be checked against what
  // is in the box NOW, not against the value captured when it was sent.
  const liveQuery = useRef(query);
  liveQuery.current = query;

  // Reset on every open. A palette that reopens showing the last search is a
  // palette you have to clear before you can use it.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setHits(null);
    setTruncated(false);
    setError(null);
    setCursor(0);
    inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const term = query.trim();
    if (term.length < 2) {
      setHits(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const res = await projectsApi.search(term);
        // The out-of-order guard: "par" and "parser" are two requests with no
        // ordering guarantee, and a slow first one landing last would replace
        // the right answers with stale ones.
        if (!isCurrent(res.query, liveQuery.current)) return;
        setHits(res.rows);
        setTruncated(res.truncated);
        setError(null);
        setCursor(0);
      } catch (err) {
        if (isCurrent(term, liveQuery.current)) {
          setError(String((err as Error).message));
        }
      } finally {
        if (isCurrent(term, liveQuery.current)) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, open]);

  // WS-27ak — no early `if (!open) return null`. `Modal` is controlled and the
  // substrate owns mounting, so bailing here would tear the popup out from
  // under its own focus-return rather than letting it hand focus back.
  const view = paletteState({ query, loading, hits, truncated, error });
  const found = view.kind === "results" ? view.hits : [];
  // Applicable commands first, then the ones the query names. Both halves are
  // the registry's own functions — this file decides nothing about either.
  const commands = paletteCommands(context, query);
  const { rows, pickable } = paletteList(commands, found);
  const at = moveSelection(cursor, 0, pickable.length);

  function activate(index: number) {
    const row = rows[pickable[index]];
    if (!row) return;
    if (row.kind === "task") {
      onOpenTask(row.hit.id);
      onClose();
      return;
    }
    if (row.kind === "command" && actions && context) {
      // Closed BEFORE the action runs: several commands open something of
      // their own (the shortcuts sheet, the field manager), and a palette
      // still up over them is a second dismissal nobody asked for.
      onClose();
      row.command.run(actions, context);
    }
  }

  return (
    // No `title`: this palette's first row IS its header, so the name is given
    // as `label` and the Modal draws no header of its own. `initialFocus` aims
    // at the box rather than letting "first tabbable element" decide.
    <Modal
      open={open}
      onClose={onClose}
      label="Search tasks"
      size="xl"
      placement="top"
      initialFocus={inputRef}
    >
      <div className="flex items-center gap-2 border-b border-border px-3">
        <Icon name="Search" className="h-4 w-4 shrink-0 text-muted-foreground" />
        <Input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={context ? "Search tasks, or type a command…" : "Search tasks…"}
          aria-label={context ? "Search tasks and commands" : "Search tasks"}
          className="border-0 focus:border-0"
          onKeyDown={(e) => {
            const action = paletteKey(e);
            if (!action) return;
            // Claimed before the browser sees them: an unhandled ArrowUp
            // moves the text caret to the start of the query, so the
            // selection and the cursor would both move on one key.
            e.preventDefault();
            if (action === "close") onClose();
            if (action === "down")
              setCursor((c) => moveSelection(c, 1, pickable.length));
            if (action === "up")
              setCursor((c) => moveSelection(c, -1, pickable.length));
            if (action === "open" && pickable.length > 0) activate(at);
          }}
        />
        <kbd className="shrink-0 rounded border border-border px-1 text-[10px] text-muted-foreground">
          esc
        </kbd>
      </div>

      <div className="max-h-96 overflow-y-auto">
        {/* The task-search states. They speak for the TASK half only now, so
            each one is worded as such — a palette that says "nothing
            matches" while eight commands are listed under the message is a
            palette arguing with itself. */}
        {view.kind === "idle" ? (
          <p className="px-3 py-2 text-[11px] text-muted-foreground">
            Two characters searches your tasks. A number like{" "}
            <code className="text-foreground">#42</code> finds that one.
          </p>
        ) : null}
        {view.kind === "searching" || view.kind === "typing" ? (
          <p className="px-3 py-2 text-[11px] text-muted-foreground">
            Searching tasks…
          </p>
        ) : null}
        {view.kind === "empty" ? (
          <p className="px-3 py-2 text-[11px] text-muted-foreground">
            No task matches “{query.trim()}”.
          </p>
        ) : null}
        {view.kind === "error" ? (
          <p className="px-3 py-2 text-xs text-destructive">{view.message}</p>
        ) : null}

        {/* Nothing at all — no command and no task. Suppressed when the task
            half already said so, or a short query would show this message
            alongside "No task matches" for the same word. */}
        {rows.length === 0 && view.kind !== "empty" && view.kind !== "error" ? (
          <p className="px-3 py-4 text-xs text-muted-foreground">
            Nothing matches “{query.trim()}”.
          </p>
        ) : null}

        <ul>
          {rows.map((row, index) => {
            if (row.kind === "section")
              return (
                <li
                  key={row.key}
                  className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"
                >
                  {row.label}
                </li>
              );
            const pick = pickable.indexOf(index);
            // AGENTS.md rule 6 — the house's active token. This row was the
            // app's other `bg-accent text-accent-foreground`.
            const selectedClass =
              pick === at ? "bg-primary/10 text-primary" : "hover:bg-muted";
            if (row.kind === "command")
              return (
                <li key={row.key}>
                  <button
                    type="button"
                    onMouseEnter={() => setCursor(pick)}
                    onClick={() => activate(pick)}
                    className={`flex w-full items-center gap-2 px-3 py-2 text-left ${selectedClass}`}
                  >
                    <Icon
                      name={row.command.icon}
                      className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                    />
                    <span className="min-w-0 flex-1 truncate text-sm">
                      {row.command.label}
                    </span>
                    {row.command.sequence ? (
                      <span className="flex shrink-0 items-center gap-0.5">
                        {sequenceLabel(row.command)
                          .split(" ")
                          .map((key, i) => (
                            <kbd
                              key={`${row.key}:${i}`}
                              className="rounded border border-border px-1 text-[10px] text-muted-foreground"
                            >
                              {key}
                            </kbd>
                          ))}
                      </span>
                    ) : null}
                  </button>
                </li>
              );
            return (
              <li key={row.key}>
                <button
                  type="button"
                  onMouseEnter={() => setCursor(pick)}
                  onClick={() => activate(pick)}
                  className={`flex w-full items-baseline gap-2 px-3 py-2 text-left ${selectedClass}`}
                >
                  <span
                    className={`min-w-0 flex-1 truncate text-sm ${
                      row.hit.completed_at ? "line-through opacity-60" : ""
                    }`}
                  >
                    {highlight(row.hit.title, query).map((part, i) => (
                      <span
                        key={`${row.key}:${i}`}
                        className={part.match ? "font-semibold text-primary" : ""}
                      >
                        {part.text}
                      </span>
                    ))}
                  </span>
                  <span className="shrink-0 text-[11px] text-muted-foreground">
                    {hitContext(row.hit)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        {view.kind === "results" && view.truncated ? (
          <p className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
            More matches than fit — add a word to narrow it.
          </p>
        ) : null}
        </div>
    </Modal>
  );
}
