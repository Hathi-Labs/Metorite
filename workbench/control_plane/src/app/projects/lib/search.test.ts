/**
 * WS-27r — the palette's logic.
 *
 * Every claim here is one that only shows up under real typing speed or a slow
 * connection, which is exactly why they are asserted rather than clicked:
 *
 * * **"no results" may only be claimed once.** Shown while a request is in
 *   flight, it flashes between every keystroke and its answer — the single most
 *   common bug in hand-rolled search UIs, and it reads as the search being
 *   broken rather than slow.
 * * **a stale response must not win.** "par" and "parser" are two requests with
 *   no ordering guarantee; a slow "par" landing last replaces the right answers
 *   with old ones and the list changes without a keystroke.
 * * **arrow keys must not reach the input**, or the caret jumps while the
 *   selection moves — two effects from one key.
 * * **a modified key is not a palette action.** `Cmd+Left` is "go to line
 *   start", and stealing it breaks editing inside the palette's own box.
 * * **the highlight needle is escaped.** Searching `a+b` would otherwise throw
 *   a regex syntax error — the browser twin of the LIKE defect this ticket
 *   fixed on the server.
 */

import { describe, expect, it } from "vitest";

import {
  type Hit,
  MIN_QUERY,
  highlight,
  hitContext,
  isCurrent,
  isOpenShortcut,
  isSearchableQuery,
  isTaskNumberQuery,
  moveSelection,
  paletteKey,
  paletteState,
} from "./search";

const hit = (over: Partial<Hit> = {}): Hit => ({
  id: "t1",
  title: "Refactor the parser",
  project_id: "p1",
  project_name: "Ops",
  rank: 1,
  ...over,
});

const state = (over: Partial<Parameters<typeof paletteState>[0]> = {}) =>
  paletteState({
    query: "parser",
    loading: false,
    hits: null,
    truncated: false,
    error: null,
    ...over,
  });

// ── paletteState ────────────────────────────────────────────────────────────

describe("paletteState", () => {
  it("is idle until the query is long enough", () => {
    expect(state({ query: "" }).kind).toBe("idle");
    expect(state({ query: "p" }).kind).toBe("idle");
    expect(state({ query: "  p  " }).kind).toBe("idle");
  });

  it("leaves idle at exactly the server's minimum", () => {
    expect(MIN_QUERY).toBe(3);
    expect(state({ query: "pa", hits: [] }).kind).toBe("idle");
    expect(state({ query: "par", hits: [] }).kind).toBe("empty");
  });

  it("leaves idle for a task number of any length (D-PM-31's exception)", () => {
    expect(state({ query: "#7", hits: [] }).kind).toBe("empty");
    expect(state({ query: "7", hits: [] }).kind).toBe("empty");
    expect(state({ query: "#", hits: [] }).kind).toBe("idle");
  });

  it("never claims 'no results' while a request is in flight", () => {
    // ⚠️ THE palette bug. An empty state flashing between every keystroke and
    // its answer reads as broken rather than slow.
    expect(state({ loading: true, hits: [] }).kind).toBe("searching");
    expect(state({ loading: true, hits: null }).kind).toBe("searching");
  });

  it("keeps the previous results on screen while the next load runs", () => {
    // ⚠️ Blanking and re-filling under the cursor makes the list unusable at
    // typing speed, and moves whatever row was selected.
    const shown = state({ loading: true, hits: [hit()] });
    expect(shown.kind).toBe("results");
    expect(shown.kind === "results" && shown.hits).toHaveLength(1);
  });

  it("says nothing at all before the first response arrives", () => {
    expect(state({ hits: null }).kind).toBe("typing");
  });

  it("claims empty only once a real answer has come back empty", () => {
    expect(state({ hits: [] }).kind).toBe("empty");
  });

  it("carries truncation through so the view can admit it", () => {
    const shown = state({ hits: [hit()], truncated: true });
    expect(shown.kind === "results" && shown.truncated).toBe(true);
  });

  it("shows an error over everything else, including a stale result set", () => {
    // An error under a list of old hits is an error nobody sees.
    expect(state({ hits: [hit()], error: "Request failed (500)" })).toEqual({
      kind: "error",
      message: "Request failed (500)",
    });
  });
});

// ── isCurrent ───────────────────────────────────────────────────────────────

describe("isCurrent", () => {
  it("accepts the response to what is in the box now", () => {
    expect(isCurrent("parser", "parser")).toBe(true);
  });

  it("rejects a slow response to an earlier query", () => {
    // ⚠️ Two requests, no ordering guarantee. A slow "par" landing after a fast
    // "parser" would replace the right answers with stale ones.
    expect(isCurrent("par", "parser")).toBe(false);
  });

  it("ignores whitespace on either side, as the server does", () => {
    expect(isCurrent("parser", "  parser ")).toBe(true);
  });
});

// ── moveSelection ───────────────────────────────────────────────────────────

describe("moveSelection", () => {
  it("steps down and up", () => {
    expect(moveSelection(0, 1, 3)).toBe(1);
    expect(moveSelection(2, -1, 3)).toBe(1);
  });

  it("wraps at both ends", () => {
    // Palettes are used without looking; the hands expect the wrap.
    expect(moveSelection(2, 1, 3)).toBe(0);
    expect(moveSelection(0, -1, 3)).toBe(2);
  });

  it("clamps an index left pointing past a list that shrank", () => {
    // ⚠️ Results change on every keystroke. An index past the end is an Enter
    // that opens nothing.
    expect(moveSelection(9, 1, 3)).toBe(0);
    expect(moveSelection(9, 0, 3)).toBe(2);
  });

  it("survives an empty list without going negative", () => {
    expect(moveSelection(0, -1, 0)).toBe(0);
    expect(moveSelection(3, 1, 0)).toBe(0);
  });

  it("handles a single result, where every move is a no-op", () => {
    expect(moveSelection(0, 1, 1)).toBe(0);
    expect(moveSelection(0, -1, 1)).toBe(0);
  });
});

// ── keys ────────────────────────────────────────────────────────────────────

describe("paletteKey", () => {
  it("claims the arrows, Enter and Escape", () => {
    // ⚠️ Left to the browser, the arrows move the text caret to the start or
    // end of the query — the selection moves AND the cursor jumps.
    expect(paletteKey({ key: "ArrowDown" })).toBe("down");
    expect(paletteKey({ key: "ArrowUp" })).toBe("up");
    expect(paletteKey({ key: "Enter" })).toBe("open");
    expect(paletteKey({ key: "Escape" })).toBe("close");
  });

  it("leaves ordinary typing alone", () => {
    expect(paletteKey({ key: "a" })).toBeNull();
    expect(paletteKey({ key: "ArrowLeft" })).toBeNull();
    expect(paletteKey({ key: "Backspace" })).toBeNull();
  });

  it("is not an action when a modifier is held", () => {
    // ⚠️ `Cmd+Left` is "go to line start". Stealing it breaks editing inside
    // the palette's own input.
    expect(paletteKey({ key: "ArrowDown", metaKey: true })).toBeNull();
    expect(paletteKey({ key: "Enter", ctrlKey: true })).toBeNull();
    expect(paletteKey({ key: "ArrowUp", altKey: true })).toBeNull();
  });
});

describe("isOpenShortcut", () => {
  it("opens on Cmd-K and Ctrl-K", () => {
    expect(isOpenShortcut({ key: "k", metaKey: true })).toBe(true);
    expect(isOpenShortcut({ key: "k", ctrlKey: true })).toBe(true);
  });

  it("survives caps lock", () => {
    expect(isOpenShortcut({ key: "K", metaKey: true })).toBe(true);
  });

  it("does not fire on a bare k, which is a letter somebody typed", () => {
    expect(isOpenShortcut({ key: "k" })).toBe(false);
  });

  it("does not fire on another modified letter", () => {
    expect(isOpenShortcut({ key: "j", metaKey: true })).toBe(false);
  });
});

// ── highlight ───────────────────────────────────────────────────────────────

describe("highlight", () => {
  it("splits a title around the match", () => {
    expect(highlight("Refactor the parser", "parser")).toEqual([
      { text: "Refactor the ", match: false },
      { text: "parser", match: true },
    ]);
  });

  it("matches case-insensitively, as the query itself does", () => {
    expect(highlight("Parser rewrite", "parser")[0]).toEqual({
      text: "Parser",
      match: true,
    });
  });

  it("marks every occurrence, not only the first", () => {
    const parts = highlight("parser calls parser", "parser");
    expect(parts.filter((p) => p.match)).toHaveLength(2);
  });

  it("escapes the needle before it becomes a regex", () => {
    // ⚠️ The browser twin of the LIKE-metacharacter defect. `a+b` unescaped is
    // a quantifier, and `(draft)` is an unbalanced group that THROWS — the
    // palette would go blank on a perfectly ordinary query.
    expect(() => highlight("a+b is fine", "a+b")).not.toThrow();
    expect(highlight("a+b is fine", "a+b")[0]).toEqual({
      text: "a+b",
      match: true,
    });
    expect(() => highlight("the (draft) copy", "(draft)")).not.toThrow();
  });

  it("returns the whole string when the query is empty", () => {
    expect(highlight("Anything", "")).toEqual([
      { text: "Anything", match: false },
    ]);
  });

  it("never loses or duplicates a character", () => {
    // The property that matters: highlighting is presentation, so the text
    // must survive it exactly.
    for (const [text, query] of [
      ["Refactor the parser", "parser"],
      ["parser", "parser"],
      ["nothing here", "zzz"],
      ["a.b.c", "."],
    ] as const) {
      expect(highlight(text, query).map((p) => p.text).join("")).toBe(text);
    }
  });
});

// ── hitContext ──────────────────────────────────────────────────────────────

describe("hitContext", () => {
  it("names the project and the number", () => {
    expect(hitContext(hit({ task_number: 42 }))).toBe("Ops · #42");
  });

  it("drops a part it does not have rather than leaving a dangling dot", () => {
    expect(hitContext(hit({ task_number: null }))).toBe("Ops");
    expect(hitContext(hit({ project_name: null, task_number: 7 }))).toBe("#7");
  });

  it("is empty rather than punctuation when it knows nothing", () => {
    expect(hitContext(hit({ project_name: null, task_number: null }))).toBe("");
  });
});

// ── the minimum, and its one exception (D-PM-31) ────────────────────────────

describe("isTaskNumberQuery — mirrors the gateway's filters.task_number", () => {
  it.each(["#7", "7", " #42 ", "# 42", "##7", "1".repeat(18)])("%j is a task number", (q) => {
    expect(isTaskNumberQuery(q)).toBe(true);
  });

  it.each(["", "#", "4.2", "-1", "42a", "²", "1".repeat(19), "#7 x"])(
    "%j is not",
    (q) => {
      expect(isTaskNumberQuery(q)).toBe(false);
    },
  );
});

describe("isSearchableQuery — the one gate before /projects/search", () => {
  it("needs MIN_QUERY characters of text", () => {
    expect(isSearchableQuery("ab")).toBe(false);
    expect(isSearchableQuery("  ab  ")).toBe(false);
    expect(isSearchableQuery("abc")).toBe(true);
  });

  it("passes a task number at any length", () => {
    expect(isSearchableQuery("#7")).toBe(true);
    expect(isSearchableQuery("7")).toBe(true);
  });
});
