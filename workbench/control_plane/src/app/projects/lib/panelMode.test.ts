import { describe, expect, it } from "vitest";

import {
  DEFAULT_PANEL_MODE,
  PANEL_MODES,
  PANEL_MODE_ICONS,
  PANEL_MODE_LABELS,
  PANEL_MODE_STORAGE_KEY,
  PANEL_WIDTH_CLASS,
  type PanelMode,
  type PanelModeStore,
  isOverlayMode,
  isPanelMode,
  narrowerPanel,
  panelEscape,
  readPanelMode,
  widerPanel,
  writePanelMode,
} from "./panelMode";

/** A `localStorage` that lives in a variable — no DOM in this runner. */
function fakeStore(seed: Record<string, string> = {}): PanelModeStore & {
  data: Record<string, string>;
} {
  const data = { ...seed };
  return {
    data,
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => {
      data[key] = value;
    },
  };
}

describe("the two stops", () => {
  it("is side to full, narrowest first", () => {
    // The owner cut `peek` on 2026-09-23: "a sidebar which can also open
    // as a full card. The switcher where we change the width of the
    // sidebar is not needed."
    expect(PANEL_MODES).toEqual(["side", "full"]);
  });

  it("a member who last chose `peek` lands on the docked panel", () => {
    // The retired stop is still in somebody's localStorage. It must read
    // as the default, NOT as an unknown mode with no width class - a
    // panel that opens at zero width looks exactly like one that failed.
    expect(isPanelMode("peek")).toBe(false);
    expect(readPanelMode({ getItem: () => "peek", setItem: () => {} }))
      .toBe("side");
  });

  it("defaults to the shipped docked width", () => {
    expect(DEFAULT_PANEL_MODE).toBe("side");
    expect(PANEL_WIDTH_CLASS.side).toBe("max-w-md");
  });

  it("labels, hints, glyphs and widths cover every stop", () => {
    for (const mode of PANEL_MODES) {
      expect(PANEL_MODE_LABELS[mode]).toBeTruthy();
      expect(PANEL_MODE_ICONS[mode]).toBeTruthy();
      expect(PANEL_WIDTH_CLASS[mode]).toBeTruthy();
    }
  });

  it("widens monotonically and stops at the end rather than wrapping", () => {
    expect(widerPanel("side")).toBe("full");
    // The one that matters: a cycling control makes "wider" mean
    // "suddenly tiny" on the next press.
    expect(widerPanel("full")).toBe("full");
  });

  it("narrows monotonically and stops at the start", () => {
    expect(narrowerPanel("full")).toBe("side");
    expect(narrowerPanel("side")).toBe("side");
  });

  it("only the widest stop is an overlay", () => {
    expect(PANEL_MODES.filter(isOverlayMode)).toEqual(["full"]);
  });

  it("recognises its own vocabulary and nothing else", () => {
    for (const mode of PANEL_MODES) expect(isPanelMode(mode)).toBe(true);
    for (const junk of ["", "wide", "SIDE", null, undefined, 2, {}])
      expect(isPanelMode(junk)).toBe(false);
  });
});

describe("persistence", () => {
  it("round-trips through a store", () => {
    const store = fakeStore();
    writePanelMode("full", store);
    expect(store.data[PANEL_MODE_STORAGE_KEY]).toBe("full");
    expect(readPanelMode(store)).toBe("full");
  });

  it("reads the default when nothing is stored", () => {
    expect(readPanelMode(fakeStore())).toBe(DEFAULT_PANEL_MODE);
  });

  it("reads the default when the stored value is not a stop", () => {
    // A newer client's vocabulary, or a devtools edit. A panel with no width
    // class at all is the failure this prevents.
    expect(readPanelMode(fakeStore({ [PANEL_MODE_STORAGE_KEY]: "gigantic" }))).toBe(
      DEFAULT_PANEL_MODE,
    );
  });

  it("survives a store that throws on every call", () => {
    const angry: PanelModeStore = {
      getItem() {
        throw new Error("SecurityError");
      },
      setItem() {
        throw new Error("SecurityError");
      },
    };
    expect(readPanelMode(angry)).toBe(DEFAULT_PANEL_MODE);
    expect(() => writePanelMode("full", angry)).not.toThrow();
  });

  it("does nothing when there is no store (SSR)", () => {
    expect(readPanelMode(null)).toBe(DEFAULT_PANEL_MODE);
    expect(() => writePanelMode("full", null)).not.toThrow();
  });
});

describe("Escape", () => {
  it("closes from anywhere that is not holding text", () => {
    expect(panelEscape({ key: "Escape" }, null)).toBe("close");
    expect(panelEscape({ key: "Escape" }, { tagName: "DIV" })).toBe("close");
    expect(panelEscape({ key: "Escape" }, { tagName: "BUTTON" })).toBe("close");
  });

  it("leaves the field first when a field holds text", () => {
    expect(panelEscape({ key: "Escape" }, { tagName: "TEXTAREA", value: "half a…" })).toBe(
      "blur",
    );
    expect(panelEscape({ key: "Escape" }, { tagName: "INPUT", value: "priya@" })).toBe(
      "blur",
    );
    expect(
      panelEscape({ key: "Escape" }, { tagName: "DIV", isContentEditable: true, value: "x" }),
    ).toBe("blur");
  });

  it("closes from an empty or whitespace-only field", () => {
    expect(panelEscape({ key: "Escape" }, { tagName: "TEXTAREA", value: "" })).toBe("close");
    expect(panelEscape({ key: "Escape" }, { tagName: "TEXTAREA", value: "   " })).toBe(
      "close",
    );
  });

  it("is nothing for any other key, or for Escape with a modifier", () => {
    expect(panelEscape({ key: "Enter" }, null)).toBeNull();
    expect(panelEscape({ key: "Escape", metaKey: true }, null)).toBeNull();
    expect(panelEscape({ key: "Escape", ctrlKey: true }, null)).toBeNull();
    expect(panelEscape({ key: "Escape", altKey: true }, null)).toBeNull();
    expect(panelEscape({ key: "Escape", shiftKey: true }, null)).toBeNull();
  });
});

describe("the width classes are themed, not hand-picked pixels", () => {
  it("every stop is a Tailwind max-w utility", () => {
    for (const mode of PANEL_MODES) {
      const cls: string = PANEL_WIDTH_CLASS[mode as PanelMode];
      expect(cls).toMatch(/^max-w-[a-z0-9]+$/);
    }
  });
});
