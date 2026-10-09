/**
 * Mark a task done from outside My Tasks, through the ONE store path.
 *
 * My Day (NS-3) completes tasks from two cards. It must not complete them
 * by a second path. `quickDispose(id, "DONE")` is the gesture My Tasks, the
 * Calendar and Focus Mode use, and it holds three rules a second path would
 * lose:
 *
 * - D-PM-38 decision 2: a parent with open subtasks ASKS first. The store
 *   sets `subtaskPrompt`, and `SubtaskPromptHost` (mounted once, in
 *   `AppShell`) draws the question.
 * - D79: Undo puts back the status the SERVER had. `UndoToast` offers it
 *   from the store's snapshot.
 * - A failed write reports through `reportSyncFailure`.
 *
 * The store is hydrated by the page that uses it (`/tasks`, `/calendar`).
 * My Day does not hydrate it on every visit, because that reads every task
 * the member holds. So the first act hydrates it, once, and then runs the
 * gesture. Fence: `completeFromHome.test.ts`.
 */
import { useTaskStore } from "./taskStore";

/** The parts of the store this file uses, so a test can hand in a fake. */
export type CompletionStore = Pick<typeof useTaskStore, "getState" | "subscribe">;

let hydrating: Promise<void> | null = null;

/** One hydration for any number of quick clicks. */
function hydrateOnce(store: CompletionStore): Promise<void> {
  hydrating ??= store
    .getState()
    .hydrate()
    .finally(() => {
      hydrating = null;
    });
  return hydrating;
}

/** What the gesture did: it asked the subtask question, or it completes. */
export type CompletionStart = "asked" | "completing";

/**
 * Complete `id` through the store. Throws when the store cannot load, or
 * when it does not hold the task, so the caller can put its row back.
 *
 * ⚠️ The store is loaded once, and My Day's rows are newer than it can be.
 * So before the gesture, the store reads that ONE task again through its own
 * `refreshItem`. A parent that gained open subtasks since the load then asks
 * (D-PM-38). A task the store has never seen makes it load again, through
 * `hydrate`. Neither is a new fetch path.
 */
export async function completeFromHome(
  id: string,
  store: CompletionStore = useTaskStore,
): Promise<CompletionStart> {
  let fresh = false;
  if (store.getState().backend !== "live") {
    await hydrateOnce(store);
    fresh = true;
  }
  // ⚠️ A hydrate that failed leaves the DEMO store, with mock rows. A done
  // there writes nothing, so it must fail loudly here.
  if (store.getState().backend !== "live") throw new Error("My Tasks could not load. Try again.");
  const held = () => store.getState().items.some((i) => i.id === id);
  if (!held() && !fresh) {
    // A task that reached My Day after the store loaded.
    await hydrateOnce(store);
    fresh = true;
    if (store.getState().backend !== "live") throw new Error("My Tasks could not load. Try again.");
  }
  if (!held()) throw new Error("Open it in My Tasks to finish it.");
  // The row the store holds may be older than the one My Day drew.
  if (!fresh) await store.getState().refreshItem(id);
  store.getState().quickDispose(id, "DONE");
  return store.getState().subtaskPrompt?.ids.includes(id) ? "asked" : "completing";
}

/**
 * After the store ASKED, resolve `true` if the answer completed the task and
 * `false` if the question closed with no done (cancelled, or replaced).
 *
 * `answerSubtaskPrompt` clears the question and THEN runs `quickDispose`, in
 * the same task. So the check waits one microtask after the question clears.
 */
export function whenAnswered(id: string, store: CompletionStore = useTaskStore): Promise<boolean> {
  return new Promise((resolve) => {
    const off = store.subscribe((state) => {
      if (state.subtaskPrompt?.ids.includes(id)) return;
      off();
      void Promise.resolve().then(() =>
        resolve(store.getState().items.find((i) => i.id === id)?.disposition === "DONE"),
      );
    });
  });
}

/** What a card does to its row while a done runs. */
export interface DoneRow {
  /** Take the row off the card. */
  hide(): void;
  /** Put the row back. */
  show(): void;
  /** Put the row back with a short error. */
  fail(message: string): void;
}

/**
 * The whole optimistic done, for any card. The row leaves at once. If the
 * store ASKS (a parent with open subtasks), the row comes back while the
 * question is up, and leaves again only if the answer completes the task.
 * The failure watch starts when the store actually writes, so an answer
 * given late is covered too.
 */
export async function markDoneFromHome(
  id: string,
  row: DoneRow,
  store: CompletionStore = useTaskStore,
  failure = "Could not mark it done. Try again.",
): Promise<"done" | "kept" | "failed"> {
  row.hide();
  let start: CompletionStart;
  try {
    start = await completeFromHome(id, store);
  } catch (err) {
    row.fail(err instanceof Error && err.message ? err.message : failure);
    return "failed";
  }
  if (start === "asked") {
    row.show();
    if (!(await whenAnswered(id, store))) return "kept";
    row.hide();
  }
  onNextSyncFailure(() => row.fail(failure), store);
  return "done";
}

/**
 * Call `onFail` once if the store reports a new write failure within `ms`.
 * The store's gesture is fire-and-forget, and this is its failure signal.
 * Returns the function that stops the watch.
 */
export function onNextSyncFailure(
  onFail: (message: string) => void,
  store: CompletionStore = useTaskStore,
  ms = 30_000,
): () => void {
  const before = store.getState().syncFailure;
  const off = store.subscribe((state) => {
    if (state.syncFailure && state.syncFailure !== before) {
      stop();
      onFail(state.syncFailure.message);
    }
  });
  // The store never calls a listener during `subscribe`, so `timer` is set
  // before `stop` can run.
  const timer = setTimeout(stop, ms);
  function stop() {
    off();
    clearTimeout(timer);
  }
  return stop;
}
