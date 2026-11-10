// `j` and `k` on the record page (WS-53 CRM-X4 item 5, spec section 19.8).
import { describe, expect, it } from "vitest";

import { navLabel, navStep, neighbour, RECORD_PAGE_ATTR } from "./recordNav";

const ids = ["a", "b", "c"];

describe("neighbour", () => {
  it("gives the next id and the id before", () => {
    expect(neighbour(ids, "b", 1)).toBe("c");
    expect(neighbour(ids, "b", -1)).toBe("a");
  });

  it("gives null at each end", () => {
    expect(neighbour(ids, "c", 1)).toBeNull();
    expect(neighbour(ids, "a", -1)).toBeNull();
  });

  it("gives null for an id that the list does not hold, and for no list", () => {
    expect(neighbour(ids, "z", 1)).toBeNull();
    expect(neighbour(ids, "z", -1)).toBeNull();
    expect(neighbour([], "a", 1)).toBeNull();
  });
});

describe("navLabel", () => {
  it("says where the record sits in its list", () => {
    expect(navLabel(ids, "b")).toBe("2 of 3");
  });

  it("says nothing for an id that the list does not hold", () => {
    expect(navLabel(ids, "z")).toBeNull();
  });
});

describe("navStep", () => {
  const key = (over: Partial<Parameters<typeof navStep>[0]>) =>
    navStep({ key: "j", metaKey: false, ctrlKey: false, altKey: false, target: null, ...over });

  it("reads j as the next record and k as the one before", () => {
    expect(key({ key: "j" })).toBe(1);
    expect(key({ key: "k" })).toBe(-1);
  });

  it("does nothing with a modifier down, so Cmd-K stays the shell's", () => {
    expect(key({ key: "k", metaKey: true })).toBeNull();
    expect(key({ key: "k", ctrlKey: true })).toBeNull();
    expect(key({ key: "j", altKey: true })).toBeNull();
  });

  it("does nothing while the member types", () => {
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
      expect(key({ key: "j", target: { tagName } })).toBeNull();
    }
    expect(key({ key: "k", target: { tagName: "DIV", isContentEditable: true } })).toBeNull();
  });

  // Review P1: a key on another widget is that widget's key, never a step.
  describe("a key on another widget", () => {
    /** A fake element: attributes, a parent, and `closest`/`querySelector`
     *  for the `[name]` and `[name="value"]` selectors navStep uses. */
    type Fake = {
      tagName: string;
      attrs: Record<string, string>;
      parent: Fake | null;
      children: Fake[];
      getAttribute: (name: string) => string | null;
      closest: (selector: string) => Fake | null;
      querySelector: (selector: string) => Fake | null;
    };
    const matches = (node: Fake, selector: string) =>
      selector.split(",").some((part) => {
        const m = part.trim().match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
        if (!m) throw new Error(`the fake cannot read ${part}`);
        const value = node.attrs[m[1]];
        return m[2] === undefined ? value !== undefined : value === m[2];
      });
    function el(attrs: Record<string, string> = {}, parent: Fake | null = null): Fake {
      const node: Fake = {
        tagName: "DIV",
        attrs,
        parent,
        children: [],
        getAttribute: (name) => attrs[name] ?? null,
        closest: (selector) => {
          for (let at: Fake | null = node; at; at = at.parent) if (matches(at, selector)) return at;
          return null;
        },
        querySelector: (selector) => {
          const walk = (at: Fake): Fake | null => {
            for (const child of at.children) {
              if (matches(child, selector)) return child;
              const hit = walk(child);
              if (hit) return hit;
            }
            return null;
          };
          return walk(node);
        },
      };
      parent?.children.push(node);
      return node;
    }

    /** The record sheet: a dialog that holds the record page. */
    function sheet() {
      const dialog = el({ role: "dialog" });
      const page = el({ [RECORD_PAGE_ATTR]: "" }, dialog);
      return { dialog, page };
    }

    it("steps from a button inside the record sheet", () => {
      expect(key({ target: el({}, sheet().page) })).toBe(1);
      // The close button sits in the sheet's header, outside the page body.
      expect(key({ target: el({}, sheet().dialog) })).toBe(1);
    });

    it("does nothing on a combobox, open or shut", () => {
      const { page } = sheet();
      expect(key({ target: el({ role: "combobox" }, page) })).toBeNull();
      expect(key({ target: el({ role: "combobox", "aria-expanded": "true" }, page) })).toBeNull();
    });

    it("does nothing on a control that holds an open popup", () => {
      expect(key({ target: el({ "aria-expanded": "true" }, sheet().page) })).toBeNull();
    });

    it("does nothing inside a listbox or a menu", () => {
      expect(key({ target: el({}, el({ role: "listbox" }, sheet().page)) })).toBeNull();
      expect(key({ target: el({}, el({ role: "menu" })) })).toBeNull();
    });

    it("does nothing inside a dialog that is not the record sheet", () => {
      expect(key({ target: el({}, el({ role: "dialog" })) })).toBeNull();
      expect(key({ target: el({}, el({ role: "alertdialog" })) })).toBeNull();
    });
  });

  it("ignores every other key", () => {
    for (const other of ["J", "K", "ArrowDown", "Enter", "l"]) {
      expect(key({ key: other })).toBeNull();
    }
  });
});
