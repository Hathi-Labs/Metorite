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

/** What the gesture did: it asked the subtask question, or it completes. */
export type CompletionStart = "asked" | "completing";

/**
 * Complete `id` through the store. Throws when the store cannot load, or
 * when it does not hold the task, so the caller can put its row back.
 */
export async function completeFromHome(
  id: string,
  store: CompletionStore = useTaskStore,
): Promise<CompletionStart> {
  if (store.getState().backend !== "live") {
    // One hydration for any number of quick clicks.
    hydrating ??= store
      .getState()
      .hydrate()
      .finally(() => {
        hydrating = null;
      });
    await hydrating;
  }
  const s = store.getState();
  // ⚠️ A hydrate that failed leaves the DEMO store, with mock rows. A done
  // there writes nothing, so it must fail loudly here.
  if (s.backend !== "live") throw new Error("My Tasks could not load. Try again.");
  if (!s.items.some((i) => i.id === id)) throw new Error("Open it in My Tasks to finish it.");
  s.quickDispose(id, "DONE");
  return store.getState().subtaskPrompt?.ids.includes(id) ? "asked" : "completing";
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
