"use client";

/**
 * Undo/redo for every app — the React half.
 *
 * `src/lib/undo.ts` owns the rules; this owns the state, the keyboard and the
 * one thing a stack of promises must not get wrong (running two at once).
 * Mounted once in the root layout, so any app can call `useUndo()` and any
 * future app gets it for nothing.
 *
 * ── SCOPE: undo must never revert something you cannot see ────────────────
 *
 * A single global history sounds right and is a trap. Edit a task in Projects,
 * navigate to Tasks, press Ctrl+Z — a global stack reverts the Projects edit,
 * off screen, with a toast naming a row you are not looking at. So a scope
 * string identifies the surface (`projects:<id>`, `tasks`), and **changing it
 * clears the history**. Undo reaches back exactly as far as the thing in front
 * of you.
 *
 * Clearing rather than keeping a stack per scope is deliberate: parked
 * histories hold closures over rows that have since been reloaded, edited by
 * somebody else, or deleted, and the longer they sit the more confidently they
 * restore something wrong.
 *
 * ── One at a time ─────────────────────────────────────────────────────────
 *
 * Every entry does IO. Ctrl+Z held down, or pressed twice quickly, would
 * otherwise start two reverts against the same row and let the slower one win —
 * the classic way an undo stack corrupts the thing it is protecting. `busy`
 * gates the whole thing, and the buttons disable while it is set.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  EMPTY_UNDO,
  type UndoEntry,
  type UndoStack,
  canRedo,
  canUndo,
  record,
  redoLabel,
  stepBack,
  stepForward,
  undoLabel,
} from "@/lib/undo";
import { isRedoShortcut, isTypingTarget, isUndoShortcut } from "@/lib/keyboard";

export interface UndoApi {
  /** Register a change that has ALREADY been applied, with how to reverse it. */
  record: (entry: UndoEntry) => void;
  /** Adopt a surface. Changing the scope clears the history. */
  setScope: (scope: string) => void;
  undo: () => void;
  redo: () => void;
  clear: () => void;
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string | null;
  redoLabel: string | null;
  busy: boolean;
  /** The last failure, for the surface to show however it shows errors. */
  error: string | null;
}

const NOOP: UndoApi = {
  record: () => {},
  setScope: () => {},
  undo: () => {},
  redo: () => {},
  clear: () => {},
  canUndo: false,
  canRedo: false,
  undoLabel: null,
  redoLabel: null,
  busy: false,
  error: null,
};

const UndoContext = createContext<UndoApi>(NOOP);

/**
 * The stack for the surface you are on.
 *
 * Outside a provider this returns an inert API rather than throwing, so a
 * component that offers undo still renders in a test or a storybook that did
 * not wrap it. The buttons read `canUndo`, so they simply stay disabled.
 */
export function useUndo(): UndoApi {
  return useContext(UndoContext);
}

/**
 * Claim a surface for the duration of a component's life.
 *
 * `useUndoScope("projects:" + projectId)` in a page is the whole integration:
 * switching project clears the history without the page thinking about it.
 */
export function useUndoScope(scope: string): UndoApi {
  const api = useUndo();
  const { setScope } = api;
  useEffect(() => {
    setScope(scope);
  }, [scope, setScope]);
  return api;
}

export function UndoProvider({ children }: { children: React.ReactNode }) {
  const [stack, setStack] = useState<UndoStack>(EMPTY_UNDO);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scopeRef = useRef<string | null>(null);
  // The listener is bound once; it must not close over a stale stack.
  const stackRef = useRef<UndoStack>(EMPTY_UNDO);
  const busyRef = useRef(false);
  stackRef.current = stack;

  const setScope = useCallback((next: string) => {
    if (scopeRef.current === next) return;
    scopeRef.current = next;
    setStack(EMPTY_UNDO);
    setError(null);
  }, []);

  const doRecord = useCallback((entry: UndoEntry) => {
    setStack((current) => record(current, entry));
    setError(null);
  }, []);

  const clear = useCallback(() => setStack(EMPTY_UNDO), []);

  /**
   * Run one step, and put the stack back if it fails.
   *
   * The optimistic move is deliberate: the buttons update immediately, so a
   * slow network does not make undo feel broken. A rejection rolls the stack
   * back to exactly where it was, so the failed step is still the next one —
   * pressing undo again retries rather than skipping past it silently.
   */
  const run = useCallback(
    (direction: "back" | "forward") => {
      if (busyRef.current) return;
      const before = stackRef.current;
      const step =
        direction === "back" ? stepBack(before) : stepForward(before);
      if (!step) return;

      busyRef.current = true;
      setBusy(true);
      setStack(step.stack);
      setError(null);

      void (async () => {
        try {
          await (direction === "back" ? step.entry.undo() : step.entry.redo());
        } catch (err) {
          setStack(before);
          setError(String((err as Error).message ?? err));
        } finally {
          busyRef.current = false;
          setBusy(false);
        }
      })();
    },
    [],
  );

  const undo = useCallback(() => run("back"), [run]);
  const redo = useCallback(() => run("forward"), [run]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // ⚠️ A text field's undo belongs to the BROWSER. Without this, typing a
      // task description and pressing Ctrl+Z reverts somebody's due date
      // instead of the sentence you were writing.
      if (isTypingTarget(event.target as HTMLElement | null)) return;
      if (isUndoShortcut(event)) {
        event.preventDefault();
        undo();
        return;
      }
      if (isRedoShortcut(event)) {
        event.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  const api = useMemo<UndoApi>(
    () => ({
      record: doRecord,
      setScope,
      undo,
      redo,
      clear,
      canUndo: canUndo(stack),
      canRedo: canRedo(stack),
      undoLabel: undoLabel(stack),
      redoLabel: redoLabel(stack),
      busy,
      error,
    }),
    [doRecord, setScope, undo, redo, clear, stack, busy, error],
  );

  return <UndoContext.Provider value={api}>{children}</UndoContext.Provider>;
}
