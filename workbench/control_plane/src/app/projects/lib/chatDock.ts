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

/** The narrowest viewport that docks. Tailwind's `xl`, which the column uses. */
export const DOCK_MIN_WIDTH = 1280;

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
 * - `absent` — not mounted. The flag is off, the member closed it, or the
 *   page shows no project chrome (a space, a folder, or one of the app's own
 *   destinations — the `ai-chat` slot among them, which would be two chats).
 * - `hidden` — mounted and not shown, because a task panel holds the column.
 * - `shown` — the column, at `xl` and wider.
 */
export type ChatDockState = "absent" | "hidden" | "shown";

export function chatDockState(a: {
  live: boolean;
  docked: boolean;
  chrome: boolean;
  taskDocked: boolean;
}): ChatDockState {
  if (!a.live || !a.docked || !a.chrome) return "absent";
  return a.taskDocked ? "hidden" : "shown";
}

/**
 * What the toggle does. Wide enough: dock or undock. Too narrow: open the
 * full slot, and leave the stored choice alone, so widening the window later
 * does not surprise anybody with a column they never asked for.
 */
export function toggleAction(docked: boolean, viewportWidth: number): "dock" | "undock" | "open-slot" {
  if (docked) return "undock";
  return viewportWidth >= DOCK_MIN_WIDTH ? "dock" : "open-slot";
}
