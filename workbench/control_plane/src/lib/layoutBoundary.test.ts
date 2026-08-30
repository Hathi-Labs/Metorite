/**
 * The fence for WS-27am item 3 — the per-layout error boundary.
 *
 * Two halves, and it matters which is which:
 *
 * 1. **The arithmetic** (`lib/layoutBoundary.ts`), tested directly. Retry bumps
 *    a key; a catch does not reset it. That is the whole recorded defect —
 *    "Retry must re-mount by bumping a key, not by clearing a flag".
 * 2. **The structure** (a source scan, the `sharedTaskUi.test.ts` idiom): the
 *    component consumes that arithmetic rather than re-deriving it, and every
 *    canvas `app/projects/page.tsx` renders sits inside the boundary. A boundary
 *    that exists and guards nothing is the failure mode nobody notices, because
 *    it is invisible until the day something throws.
 *
 * ⚠️ **What is NOT fenced here, and must not be claimed as fenced.** "A
 * malformed group shape does not blank the app" and "Retry re-mounts rather than
 * re-crashing" require rendering a throwing child. This runner is
 * `environment: "node"` with `include: ["src/**\/*.test.ts"]` — no DOM, no
 * testing-library, and `.tsx` tests are not collected at all. Adding a DOM
 * substrate to fence one component is a substrate decision, not a papercut
 * ticket. Those two claims are **review-only**: throw from a canvas and look.
 *
 * Rooted at `src/` via `import.meta.url` like `sharedTaskUi.test.ts`, never the
 * process cwd — agent worktrees under `.claude/worktrees/` would otherwise be
 * scanned as part of the checkout that contains them.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { caught, clean, describeError, retry } from "./layoutBoundary";

// ── 1. The arithmetic ───────────────────────────────────────────────────────

describe("retry re-mounts by bumping a key", () => {
  it("clears the error AND advances the key", () => {
    const broken = { ...clean(), ...caught(new Error("bad group shape")) };
    const next = retry(broken);
    expect(next.error).toBeNull();
    expect(next.attempt).toBe(broken.attempt + 1);
  });

  it("never re-uses a key across a crash/retry cycle", () => {
    // The defect written as a test: clearing a flag hands React the identity it
    // was already reconciling. Every mount has to be one React has not seen.
    let state = clean();
    const keys = [state.attempt];
    for (let i = 0; i < 5; i++) {
      state = { ...state, ...caught(new Error(`crash ${i}`)) };
      state = retry(state);
      keys.push(state.attempt);
    }
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("is monotone — a retry can never lower the key", () => {
    let state = clean();
    for (let i = 0; i < 20; i++) {
      const before = state.attempt;
      state = retry(state);
      expect(state.attempt).toBeGreaterThan(before);
    }
  });

  it("catching an error does NOT touch the key", () => {
    // `caught` is `getDerivedStateFromError`, whose return value React MERGES.
    // An `attempt` in it would reset the key on every crash, silently turning
    // the key bump back into a flag clear — the exact thing this file fences.
    const patch: Record<string, unknown> = caught(new Error("x"));
    expect(Object.keys(patch)).toEqual(["error"]);
    expect("attempt" in patch).toBe(false);
  });
});

describe("what the boundary shows", () => {
  it("normalises a thrown non-Error so the fallback always has a message", () => {
    // A `throw "boom"` is legal and happens; `error.message` on a string is
    // `undefined`, which renders as a blank explanation under a red heading.
    const { error } = caught("boom");
    expect(error).toBeInstanceOf(Error);
    expect(describeError(error)).toBe("boom");
  });

  it("says something even when the error says nothing", () => {
    expect(describeError(caught(new Error("")).error)).toMatch(/\w/);
    expect(describeError(caught(new Error("   ")).error)).toMatch(/\w/);
    expect(describeError(null)).toMatch(/\w/);
  });

  it("a fresh boundary is healthy and on its first mount", () => {
    expect(clean()).toEqual({ error: null, attempt: 0 });
    // Per instance, not a shared object: two boundaries must not share state.
    expect(clean()).not.toBe(clean());
  });
});

// ── 2. The structure ────────────────────────────────────────────────────────

const SRC = fileURLToPath(new URL("..", import.meta.url));

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry))
        out.push(relative(SRC, full).split(sep).join("/"));
    }
  };
  walk(SRC);
  return out.sort();
}

const FILES = sourceFiles();
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

/** Source with its comments removed — a gate a comment can trip teaches
 *  people not to comment (`sharedTaskUi.test.ts` learned this the hard way). */
const code = (rel: string) =>
  read(rel)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(?<![:"'/])\/\/[^\n]*/g, "");

const BOUNDARY = "components/LayoutBoundary.tsx";
const PAGE = "app/projects/page.tsx";

/**
 * Every canvas `/projects` can put on screen — `lib/commands.VIEW_MODES` is
 * the five. (`MyWork` was the sixth until 2026-08-31: /tasks is the personal
 * lens, so the entry left with the surface.)
 */
const CANVASES = [
  "TimelineView",
  "CalendarView",
  "TableView",
  "TaskBoard",
  "TaskList",
];

describe("the boundary is the only one, and it is wired", () => {
  it("is declared once", () => {
    const offenders = FILES.filter(
      (f) => f !== BOUNDARY && /(?:export\s+)?class\s+LayoutBoundary\b/.test(read(f)),
    );
    expect(
      offenders,
      "A second error boundary. One shape for a failed canvas, or two surfaces " +
        "fail two different ways and only one of them is themed.",
    ).toEqual([]);
    expect(FILES, `${BOUNDARY} moved`).toContain(BOUNDARY);
  });

  it("runs the arithmetic this file tests, instead of re-deriving it", () => {
    const src = code(BOUNDARY);
    expect(src, "the boundary must import the pure helper").toMatch(
      /from\s+["']@\/lib\/layoutBoundary["']/,
    );
    expect(src, "getDerivedStateFromError must return `caught(...)`").toMatch(
      /getDerivedStateFromError[\s\S]{0,120}caught\(/,
    );
    expect(src, "Retry must go through `retry(...)`").toMatch(/retry\(current\)|retry\(/);
    // The negative half: a hand-written reset beside the tested one is how the
    // component and its fence come to disagree about what Retry does.
    expect(
      src,
      "The boundary clears its error by hand — use `retry()`, which also " +
        "advances the key. Clearing the flag alone is the defect.",
    ).not.toMatch(/error:\s*null/);
  });

  it("keys the guarded subtree on the attempt counter", () => {
    // The arithmetic only means anything if `attempt` is what React reconciles
    // by. A boundary that computes a key and renders `{children}` unkeyed
    // passes every test above and re-crashes on Retry exactly as before.
    expect(code(BOUNDARY)).toMatch(/key=\{this\.state\.attempt\}/);
  });
});

describe("every /projects canvas renders inside the boundary", () => {
  const page = code(PAGE);

  /** The opening `<LayoutBoundary …>` tag, brace-aware so an attribute
   *  containing an arrow function cannot end the tag early. */
  const openTag = (): { tag: string; end: number } => {
    const m = /<LayoutBoundary\b/.exec(page);
    expect(m, `${PAGE} does not render a LayoutBoundary at all`).not.toBeNull();
    let depth = 0;
    let i = m!.index + m![0].length;
    for (; i < page.length; i++) {
      const c = page[i];
      if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    return { tag: page.slice(m!.index, i), end: i + 1 };
  };

  const guarded = () => {
    const { end } = openTag();
    const close = page.indexOf("</LayoutBoundary>", end);
    expect(close, "unclosed <LayoutBoundary>").toBeGreaterThan(-1);
    return page.slice(end, close);
  };

  it("the page imports it from the shared home", () => {
    expect(page).toMatch(
      /import\s*\{\s*LayoutBoundary\s*\}\s*from\s+["']@\/components\/LayoutBoundary["']/,
    );
  });

  it("the boundary IS the scroll region's child, so nothing can be added outside it", () => {
    // Adjacency rather than enumeration: proving the boundary wraps the whole
    // canvas region means a SEVENTH canvas added tomorrow is guarded without
    // anybody remembering to add a row to this test.
    expect(
      page,
      "The canvas scroll region must open straight into <LayoutBoundary>; a " +
        "sibling beside it is a canvas that blanks the app when it throws.",
    ).toMatch(/overflow-auto"\s*>\s*<LayoutBoundary\b/);
    expect(
      page,
      "…and close straight out of it, for the same reason.",
    ).toMatch(/<\/LayoutBoundary>\s*<\/div>/);
  });

  it("is keyed and labelled", () => {
    const { tag } = openTag();
    // Keyed: a crashed canvas must not follow the user to another layout or
    // another project. Labelled: the fallback names which view died.
    expect(tag, "no `key` — the fallback would survive a layout switch").toMatch(/\bkey=/);
    expect(tag, "no `layout` — the fallback cannot name what broke").toMatch(/\blayout=/);
  });

  it.each(CANVASES)("%s is rendered, and only inside the boundary", (canvas) => {
    const tag = new RegExp(`<${canvas}\\b`, "g");
    const everywhere = page.match(tag)?.length ?? 0;
    const inside = guarded().match(tag)?.length ?? 0;
    expect(everywhere, `${canvas} is not rendered by ${PAGE} — drop the row`).toBeGreaterThan(0);
    expect(
      inside,
      `${canvas} is rendered outside <LayoutBoundary>. One malformed row in ` +
        "it takes the whole app's React root down — there is no other " +
        "boundary above it, which is the state this ticket found the tree in.",
    ).toBe(everywhere);
  });
});
