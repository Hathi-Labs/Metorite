import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  AUTO_FOLD_KEY,
  COLLAPSED_KEY,
  HINT_COUNT_KEY,
  HINT_LIMIT,
  FOLD_CONTROL_ATTR,
  autoFoldEnabled,
  floatingOpen,
  isPlainNavClick,
  isWorkEvent,
  readCollapsed,
  setAutoFoldEnabled,
  shouldFold,
  takeHint,
  writeCollapsed,
  type FoldInput,
} from "./sidebarFold";

/** A plain Map behind the Storage interface. The suite runs in node. */
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

const g = globalThis as { localStorage?: Storage };

describe("sidebarFold storage", () => {
  beforeEach(() => {
    g.localStorage = memoryStorage();
  });
  afterEach(() => {
    delete g.localStorage;
  });

  it("folds by default, and remembers Keep it open", () => {
    expect(autoFoldEnabled()).toBe(true);
    setAutoFoldEnabled(false);
    expect(localStorage.getItem(AUTO_FOLD_KEY)).toBe("off");
    expect(autoFoldEnabled()).toBe(false);
    setAutoFoldEnabled(true);
    expect(autoFoldEnabled()).toBe(true);
  });

  it("remembers the folded state across a reload", () => {
    expect(readCollapsed()).toBe(false);
    writeCollapsed(true);
    expect(localStorage.getItem(COLLAPSED_KEY)).toBe("1");
    expect(readCollapsed()).toBe(true);
    writeCollapsed(false);
    expect(readCollapsed()).toBe(false);
  });

  it("shows the tip on the first three folds, then never", () => {
    const shown = Array.from({ length: HINT_LIMIT + 2 }, () => takeHint());
    expect(shown).toEqual([true, true, true, false, false]);
  });

  it("reads a corrupt tip count as zero, so the tip shows again", () => {
    localStorage.setItem(HINT_COUNT_KEY, "not a number");
    expect(takeHint()).toBe(true);
    expect(localStorage.getItem(HINT_COUNT_KEY)).toBe("1");
  });

  it("survives storage that throws, as a private window does", () => {
    const boom = () => {
      throw new Error("SecurityError");
    };
    g.localStorage = { ...memoryStorage(), getItem: boom, setItem: boom } as Storage;
    expect(autoFoldEnabled()).toBe(true);
    expect(readCollapsed()).toBe(false);
    expect(() => writeCollapsed(true)).not.toThrow();
    expect(() => setAutoFoldEnabled(false)).not.toThrow();
    expect(takeHint()).toBe(true);
  });
});

describe("isPlainNavClick", () => {
  const plain = {
    button: 0,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    defaultPrevented: false,
  };

  it("arms on a plain left click", () => {
    expect(isPlainNavClick(plain)).toBe(true);
  });

  it.each([
    ["a middle click", { button: 1 }],
    ["a ctrl-click", { ctrlKey: true }],
    ["a cmd-click", { metaKey: true }],
    ["a shift-click", { shiftKey: true }],
    ["an alt-click", { altKey: true }],
    ["a prevented click", { defaultPrevented: true }],
  ])("does not arm on %s, because this tab does not navigate", (_, over) => {
    expect(isPlainNavClick({ ...plain, ...over })).toBe(false);
  });
});

describe("isWorkEvent", () => {
  it("counts a primary click and a real key", () => {
    expect(isWorkEvent({ type: "click", button: 0 })).toBe(true);
    expect(isWorkEvent({ type: "keydown", key: "a" })).toBe(true);
    expect(isWorkEvent({ type: "keydown", key: "Enter" })).toBe(true);
    expect(isWorkEvent({ type: "keydown", key: "Tab" })).toBe(true);
  });

  it("ignores a press, a middle click, a modifier alone, Escape and a scroll", () => {
    // A fold on the press moves the target before the release. See the module.
    expect(isWorkEvent({ type: "pointerdown", button: 0 })).toBe(false);
    expect(isWorkEvent({ type: "click", button: 1 })).toBe(false);
    for (const key of ["Shift", "Control", "Alt", "Meta", "Escape"]) {
      expect(isWorkEvent({ type: "keydown", key })).toBe(false);
    }
    expect(isWorkEvent({ type: "wheel" })).toBe(false);
    expect(isWorkEvent({ type: "scroll" })).toBe(false);
  });
});

describe("shouldFold", () => {
  const go: FoldInput = {
    armed: true,
    enabled: true,
    collapsed: false,
    inSidebar: false,
    inMain: true,
    work: true,
  };

  it("folds on work in the app after a sidebar link", () => {
    expect(shouldFold(go)).toBe(true);
  });

  it.each([
    ["no sidebar link armed it", { armed: false }],
    ["the member chose Keep it open", { enabled: false }],
    ["it is folded already", { collapsed: true }],
    ["the press is in the sidebar", { inSidebar: true }],
    ["the press is outside the app, in a dialog", { inMain: false }],
    ["the event is not work", { work: false }],
  ])("does not fold when %s", (_, over) => {
    expect(shouldFold({ ...go, ...over })).toBe(false);
  });
});

describe("floatingOpen", () => {
  /** Just enough of the DOM: a list of nodes, and which ones sit in the rail. */
  const node = (inRail: boolean) => ({ inRail }) as unknown as Element;
  const root = (nodes: Element[]) =>
    ({ querySelectorAll: () => nodes }) as unknown as ParentNode;
  const rail = {
    contains: (el: Element) => (el as unknown as { inRail: boolean }).inRail,
  } as unknown as Element;

  it("waits while a menu, a listbox or a dialog is open in the app", () => {
    expect(floatingOpen(root([node(false)]), rail)).toBe(true);
  });

  it("ignores the sidebar's own expanded section headings", () => {
    expect(floatingOpen(root([node(true), node(true)]), rail)).toBe(false);
  });

  it("folds when nothing floats", () => {
    expect(floatingOpen(root([]), rail)).toBe(false);
  });

  it("with no rail to look in, anything open holds the fold", () => {
    expect(floatingOpen(root([node(false)]), null)).toBe(true);
  });

  // The full-width shell bar (owner, 2026-10-09) puts the fold control
  // OUTSIDE the rail, and it is `aria-expanded="true"` while the rail is open.
  // Read as a menu, it held every fold for ever. Measured in the browser.
  it("ignores the fold's own control in the shell bar", () => {
    const control = {
      inRail: false,
      closest: (sel: string) => (sel === `[${FOLD_CONTROL_ATTR}]` ? {} : null),
    } as unknown as Element;
    expect(floatingOpen(root([control]), rail)).toBe(false);
    // A real menu beside it still holds the fold.
    expect(floatingOpen(root([control, node(false)]), rail)).toBe(true);
  });
});
