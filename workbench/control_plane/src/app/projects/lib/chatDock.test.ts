import { describe, expect, it } from "vitest";

import {
  CHAT_DOCK_STORAGE_KEY,
  chatDockState,
  readChatDocked,
  toggleAction,
  writeChatDocked,
  type ChatDockStore,
} from "./chatDock";

function memory(initial: Record<string, string> = {}): ChatDockStore & { data: Record<string, string> } {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = v;
    },
  };
}

describe("the stored choice", () => {
  it("round-trips, and anything but 1 reads as closed", () => {
    const store = memory();
    expect(readChatDocked(store)).toBe(false);
    writeChatDocked(true, store);
    expect(store.data[CHAT_DOCK_STORAGE_KEY]).toBe("1");
    expect(readChatDocked(store)).toBe(true);
    writeChatDocked(false, store);
    expect(readChatDocked(store)).toBe(false);
    expect(readChatDocked(memory({ [CHAT_DOCK_STORAGE_KEY]: "yes" }))).toBe(false);
  });

  it("survives a store that throws, and no store at all", () => {
    const broken: ChatDockStore = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readChatDocked(broken)).toBe(false);
    expect(() => writeChatDocked(true, broken)).not.toThrow();
    expect(readChatDocked(null)).toBe(false);
  });
});

describe("the column", () => {
  const on = { live: true, docked: true, wide: true, slotOpen: false, taskDocked: false };

  it("shows when the flag is on, the member docked it and the viewport is wide", () => {
    expect(chatDockState(on)).toBe("shown");
  });

  it("is absent when the flag is off, it is closed, it is narrow, or the slot is open", () => {
    expect(chatDockState({ ...on, live: false })).toBe("absent");
    expect(chatDockState({ ...on, docked: false })).toBe("absent");
    // Narrow mounts nothing, so a hidden rail fetches nothing.
    expect(chatDockState({ ...on, wide: false })).toBe("absent");
    // The ai-chat slot is the chat: a dock beside it would be a second one.
    expect(chatDockState({ ...on, slotOpen: true })).toBe("absent");
  });

  it("hides but stays mounted while a docked task panel holds the column", () => {
    expect(chatDockState({ ...on, taskDocked: true })).toBe("hidden");
  });
});

describe("the toggle does what the member sees", () => {
  it("undocks a shown column", () => {
    expect(toggleAction("shown", true)).toBe("undock");
  });

  it("brings back a column a task hides, rather than closing it", () => {
    expect(toggleAction("hidden", true)).toBe("show");
  });

  it("docks when wide, and opens the slot when narrow", () => {
    expect(toggleAction("absent", true)).toBe("dock");
    expect(toggleAction("absent", false)).toBe("open-slot");
  });
});
