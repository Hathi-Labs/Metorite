import { describe, expect, it } from "vitest";

import {
  CHAT_DOCK_STORAGE_KEY,
  DOCK_MIN_WIDTH,
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
  const on = { live: true, docked: true, chrome: true, taskDocked: false };

  it("shows when the flag is on, the member docked it and the page has a project", () => {
    expect(chatDockState(on)).toBe("shown");
  });

  it("is absent when the flag is off, it is closed, or there is no project chrome", () => {
    expect(chatDockState({ ...on, live: false })).toBe("absent");
    expect(chatDockState({ ...on, docked: false })).toBe("absent");
    // The ai-chat slot is no-chrome: a dock there would be a second chat.
    expect(chatDockState({ ...on, chrome: false })).toBe("absent");
  });

  it("hides but stays mounted while a docked task panel holds the column", () => {
    expect(chatDockState({ ...on, taskDocked: true })).toBe("hidden");
  });
});

describe("the toggle", () => {
  it("docks when wide, and opens the slot when narrow", () => {
    expect(toggleAction(false, DOCK_MIN_WIDTH)).toBe("dock");
    expect(toggleAction(false, DOCK_MIN_WIDTH - 1)).toBe("open-slot");
  });

  it("always undocks an open dock", () => {
    expect(toggleAction(true, 390)).toBe("undock");
    expect(toggleAction(true, 1920)).toBe("undock");
  });
});
