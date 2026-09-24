/**
 * Projects · the AI chat docked beside the board (WS-27bm, spec
 * `projects_ai_chat.md` §7 "Where it mounts").
 *
 * The chat has two places and ONE component. The `ai-chat` slot shows it full
 * width. This dock shows it as a column beside the canvas, so a member can
 * talk while the board is open. Both mount `AssistantRail`.
 *
 * **One right-hand column, not two.** The docked task panel already lives on
 * the right. At 1280px a tree, a board, a task panel and a chat leave the
 * board narrower than one lane. So when a task panel docks, the chat HIDES and
 * stays mounted — a reply that is streaming keeps streaming — and it comes
 * back when the task closes. A full-width task panel is an overlay, so it
 * hides nothing.
 *
 * **Below 1280px there is no dock.** The toggle opens the full slot instead,
 * because a column beside a board that narrow is a column with no board.
 *
 * The choice persists per browser, as `panelMode.ts` does and for its reason:
 * a reading preference with no meaning on another device.
 */

/**
 * The viewport that docks, as a media query. The SAME string the page's
 * `matchMedia` reads, and the same `80rem` Tailwind's `xl` is. A pixel count
 * compared with `window.innerWidth` disagrees with `80rem` as soon as a member
 * raises the browser's font size, and then the toggle docks a column that CSS
 * hides (review of PR #415).
 */
export const DOCK_QUERY = "(min-width: 80rem)";

export const CHAT_DOCK_STORAGE_KEY = "cc-projects-chat-docked";

/** The slice of `Storage` this needs — so a test can hand it a plain object. */
export interface ChatDockStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function browserStore(): ChatDockStore | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    // Storage can throw outright (Safari private mode). The dock still works;
    // it just forgets.
    return null;
  }
}

/** The stored choice. Anything but the literal `"1"` reads as closed. */
export function readChatDocked(store?: ChatDockStore | null): boolean {
  const target = store === undefined ? browserStore() : store;
  if (!target) return false;
  try {
    return target.getItem(CHAT_DOCK_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeChatDocked(docked: boolean, store?: ChatDockStore | null): void {
  const target = store === undefined ? browserStore() : store;
  if (!target) return;
  try {
    target.setItem(CHAT_DOCK_STORAGE_KEY, docked ? "1" : "0");
  } catch {
    /* a preference that cannot be saved is not a failure worth surfacing */
  }
}

/**
 * What the dock column does on this render.
 *
 * - `absent` — not mounted. The flag is off, the member closed it, the
 *   viewport is too narrow, or the `ai-chat` slot is open (two chats).
 * - `hidden` — mounted and not shown, because a task panel holds the column.
 * - `shown` — the column.
 *
 * ⚠️ A space, a folder, Analytics and Reports do NOT remove the dock. The
 * chat's own navigation (`projects.open_project`, `projects.open_app`) goes
 * there, and a dock that unmounted on arrival would take the reply that was
 * still streaming with it (review of PR #415). Width is decided here and not
 * by a CSS class, so a narrow viewport mounts nothing and fetches nothing.
 */
export type ChatDockState = "absent" | "hidden" | "shown";

export function chatDockState(a: {
  live: boolean;
  docked: boolean;
  wide: boolean;
  slotOpen: boolean;
  taskDocked: boolean;
}): ChatDockState {
  if (!a.live || !a.docked || !a.wide || a.slotOpen) return "absent";
  return a.taskDocked ? "hidden" : "shown";
}

/**
 * What the toggle does, from what the member SEES. The button is pressed only
 * while the column is `shown`, so a press always does what the button says.
 *
 * - `shown` → undock.
 * - `hidden` → show the chat: close the task that holds the column.
 * - `absent` and wide → dock. Too narrow → open the full slot, and leave the
 *   stored choice alone.
 * - The `ai-chat` slot is open → close the slot. The slot IS the chat, so the
 *   button reads as pressed there, and a press takes the member back to the
 *   board. This case exists since the button moved to the app's top bar
 *   (2026-09-24). The project header it sat in before is not drawn over the
 *   slot, so nobody could press it there. Without this case a press would
 *   store "docked" and change nothing on screen, because a dock beside the
 *   slot is `absent`.
 */
export function toggleAction(
  state: ChatDockState,
  wide: boolean,
  slotOpen = false,
): "dock" | "undock" | "show" | "open-slot" | "close-slot" {
  if (slotOpen) return "close-slot";
  if (state === "shown") return "undock";
  if (state === "hidden") return "show";
  return wide ? "dock" : "open-slot";
}

/**
 * What one press of the top-bar button changes. Each field left `undefined`
 * stays as it is.
 *
 * - `app` — the Projects destination: `"ai-chat"` opens the slot, `null`
 *   leaves it.
 * - `docked` — the stored dock choice. The page sets the state AND writes it.
 * - `closeTask` — close the task panel that holds the right-hand column.
 */
export interface AssistantPress {
  app?: "ai-chat" | null;
  docked?: boolean;
  closeTask?: boolean;
}

/**
 * The top-bar assistant button, whole: whether it is pressed, its tooltip,
 * and what a press changes. The page reads this and nothing else, so every
 * decision is a pure function a test can reach (`vitest` runs without a DOM).
 *
 * ⚠️ **Closing the slot also undocks.** A member who docked the chat and then
 * opened "AI chat" from the tree has `docked` stored as true. If a press
 * only left the slot, the dock would come straight back, still pressed, and
 * the member would have to press a second time (review of PR #467). One
 * press closes the assistant, wherever it is.
 *
 * The tooltip is neutral on purpose. The button shows on a space, a folder,
 * Analytics and Reports too, so "about this project" would be wrong there.
 */
export function assistantButton(a: {
  state: ChatDockState;
  wide: boolean;
  slotOpen: boolean;
}): { pressed: boolean; title: string; press: AssistantPress } {
  const pressed = a.slotOpen || a.state === "shown";
  const title = pressed
    ? "Close the assistant"
    : a.state === "hidden"
      ? "Show the assistant (closes the task)"
      : "Ask the assistant";
  const act = toggleAction(a.state, a.wide, a.slotOpen);
  const press: AssistantPress =
    act === "close-slot"
      ? { app: null, docked: false }
      : act === "open-slot"
        ? { app: "ai-chat" }
        : act === "show"
          ? { closeTask: true }
          : { docked: act === "dock" };
  return { pressed, title, press };
}
