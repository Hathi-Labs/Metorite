import { describe, expect, it } from "vitest";

import {
  CHAT_DOCK_STORAGE_KEY,
  assistantButton,
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

  it("closes the ai-chat slot, whatever the dock says, because the slot is the chat", () => {
    // The dock beside an open slot is always `absent`, so without this a
    // press would store "docked" and change nothing on screen.
    expect(toggleAction("absent", true, true)).toBe("close-slot");
    expect(toggleAction("absent", false, true)).toBe("close-slot");
    expect(toggleAction("absent", true, false)).toBe("dock");
  });
});

describe("the top-bar button, whole (assistantButton)", () => {
  const base = { state: "absent" as const, wide: true, slotOpen: false };

  it("one press over the slot closes the slot AND undocks, so the dock does not come back", () => {
    // A member who docked the chat, then opened "AI chat" from the tree.
    const b = assistantButton({ ...base, slotOpen: true });
    expect(b.press).toEqual({ app: null, docked: false });
    // Replay the press: the next render has no slot and no stored dock.
    const docked = b.press.docked ?? true;
    const after = chatDockState({
      live: true, docked, wide: true, slotOpen: b.press.app === "ai-chat", taskDocked: false,
    });
    expect(after).toBe("absent");
    expect(assistantButton({ state: after, wide: true, slotOpen: false }).pressed).toBe(false);
  });

  it("is pressed over the slot and while the dock is shown, and nowhere else", () => {
    expect(assistantButton({ ...base, slotOpen: true }).pressed).toBe(true);
    expect(assistantButton({ ...base, state: "shown" }).pressed).toBe(true);
    expect(assistantButton({ ...base, state: "hidden" }).pressed).toBe(false);
    expect(assistantButton(base).pressed).toBe(false);
  });

  it("says what a press will do, in words that fit a space, a folder and an app", () => {
    expect(assistantButton(base).title).toBe("Ask the assistant");
    expect(assistantButton({ ...base, slotOpen: true }).title).toBe("Close the assistant");
    expect(assistantButton({ ...base, state: "shown" }).title).toBe("Close the assistant");
    expect(assistantButton({ ...base, state: "hidden" }).title).toBe(
      "Show the assistant (closes the task)",
    );
    for (const s of ["absent", "hidden", "shown"] as const) {
      expect(assistantButton({ ...base, state: s }).title).not.toMatch(/project/);
    }
  });

  it("docks, undocks, shows and opens the slot as toggleAction says", () => {
    expect(assistantButton(base).press).toEqual({ docked: true });
    expect(assistantButton({ ...base, state: "shown" }).press).toEqual({ docked: false });
    expect(assistantButton({ ...base, state: "hidden" }).press).toEqual({ closeTask: true });
    expect(assistantButton({ ...base, wide: false }).press).toEqual({ app: "ai-chat" });
  });
});
