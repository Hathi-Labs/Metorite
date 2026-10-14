import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  PANEL_MOTION,
  PANEL_SWAP_CLASS,
  PANEL_SWAP_NAME,
  PANEL_TOKENS,
  formDurationMs,
  isMoving,
  isShown,
  nextPhase,
  pickFocusReturn,
  prefersReducedMotion,
  swapPanelForm,
  type FocusCandidate,
  type PanelPhase,
} from "./panelMotion";

/**
 * The one motion of a side panel (owner, 2026-10-14). The left sidebar's
 * fold is the reference, and the assistant mirrors it from the right. This
 * file is the fence that keeps the two on one set of tokens.
 */

const SRC = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(SRC, rel), "utf-8");
const GLOBALS = read("app/globals.css");
const DOCK = read("lib/shell/AssistantDock.tsx");

/** The body of the first rule whose selector is exactly `selector`. */
function rule(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) return "";
  const open = css.indexOf("{", at);
  return css.slice(open + 1, css.indexOf("}", open));
}

describe("the phases", () => {
  const ALL: PanelPhase[] = ["closed", "opening", "open", "closing"];

  it("an open moves through `opening` to `open`, and a close through `closing` to `closed`", () => {
    expect(nextPhase("closed", "open")).toBe("opening");
    expect(nextPhase("opening", "settled")).toBe("open");
    expect(nextPhase("open", "close")).toBe("closing");
    expect(nextPhase("closing", "settled")).toBe("closed");
  });

  it("a reversal mid-way goes to the other moving phase", () => {
    expect(nextPhase("opening", "close")).toBe("closing");
    expect(nextPhase("closing", "open")).toBe("opening");
  });

  it("a repeat is a no-op, and `settled` changes only a moving phase", () => {
    expect(nextPhase("open", "open")).toBe("open");
    expect(nextPhase("closed", "close")).toBe("closed");
    expect(nextPhase("open", "settled")).toBe("open");
    expect(nextPhase("closed", "settled")).toBe("closed");
  });

  it("reduced motion skips the moving phases from any phase", () => {
    for (const p of ALL) {
      expect(nextPhase(p, "open-now")).toBe("open");
      expect(nextPhase(p, "close-now")).toBe("closed");
    }
  });

  it("only `opening` and `open` are on screen, and only the moving phases animate", () => {
    expect(ALL.filter(isShown)).toEqual(["opening", "open"]);
    expect(ALL.filter(isMoving)).toEqual(["opening", "closing"]);
  });

  it("each form takes its own token's time", () => {
    expect(formDurationMs("dock")).toBe(PANEL_MOTION.durationMs);
    expect(formDurationMs("full")).toBe(PANEL_MOTION.expandMs);
    expect(formDurationMs("sheet")).toBe(PANEL_MOTION.sheetMs);
  });

  it("the full-view switch stays in the brief's window, 200 to 280 ms", () => {
    expect(PANEL_MOTION.expandMs).toBeGreaterThanOrEqual(200);
    expect(PANEL_MOTION.expandMs).toBeLessThanOrEqual(280);
  });
});

describe("the tokens: one motion for both panels", () => {
  it("globals.css declares every token in :root, with the values the script reads", () => {
    const root = rule(GLOBALS, ":root");
    for (const [name, value] of Object.entries(PANEL_TOKENS)) {
      expect.soft(root, name).toContain(`${name}: ${value};`);
    }
  });

  it("the left sidebar's fold reads the tokens, and writes no time of its own", () => {
    const body = rule(GLOBALS, ".sidebar-rail");
    expect(body).toContain("transition: width var(--panel-duration) var(--panel-easing);");
    expect(body).not.toMatch(/\d+m?s\b|cubic-bezier/);
    expect(read("components/Sidebar.tsx")).toContain("sidebar-rail");
  });

  it("the assistant's open and close read the same two tokens", () => {
    const spacer = rule(GLOBALS, ".assistant-spacer[data-moving]");
    const dock = rule(GLOBALS, '.assistant-panel[data-moving][data-panel-form="dock"]');
    expect(spacer).toContain("transition: width var(--panel-duration) var(--panel-easing);");
    expect(dock).toContain("transition: transform var(--panel-duration) var(--panel-easing);");
    for (const body of [spacer, dock]) expect(body).not.toMatch(/\d+m?s\b|cubic-bezier/);
    expect(DOCK).toContain('className="assistant-spacer h-full shrink-0"');
    expect(DOCK).toContain("className={`assistant-panel ${shape}`}");
  });

  it("the full view and the phone sheet read their tokens, on the same curve family", () => {
    expect(rule(GLOBALS, '.assistant-panel[data-moving][data-panel-form="full"]')).toContain(
      "transition: opacity var(--panel-expand-duration) var(--panel-easing);",
    );
    expect(rule(GLOBALS, "html.panel-swap::view-transition-group(assistant-panel)")).toContain(
      "animation-timing-function: var(--panel-easing);",
    );
    // The phone sheet moves as the shell drawer does: one token for both.
    expect(rule(GLOBALS, ".chat-fade-in")).toContain("var(--sheet-duration) var(--sheet-easing)");
    expect(rule(GLOBALS, '.assistant-panel[data-moving][data-panel-form="sheet"]')).toContain(
      "opacity var(--sheet-duration) var(--sheet-easing)",
    );
  });

  it("the swap's CSS names the same transition name and class as the script", () => {
    expect(GLOBALS).toContain(`html.${PANEL_SWAP_CLASS}::view-transition-group(${PANEL_SWAP_NAME})`);
  });

  it("reduced motion turns off both panels' transitions", () => {
    const at = GLOBALS.indexOf("@media (prefers-reduced-motion: reduce)");
    const block = GLOBALS.slice(at, GLOBALS.indexOf("\n}\n", at));
    expect(block).toMatch(/\.sidebar-rail \{\s*transition: none;/);
    expect(block).toMatch(/\.assistant-spacer\[data-moving\],\s*\.assistant-panel\[data-moving\] \{\s*transition: none;/);
  });
});

describe("focus on a close", () => {
  const el = (over: Partial<FocusCandidate & { name: string }> = {}) => ({
    name: "x",
    isConnected: true,
    getClientRects: () => ({ length: 1 }),
    focus: vi.fn(),
    ...over,
  });

  it("goes back to the opener", () => {
    const opener = el({ name: "opener" });
    const toggle = el({ name: "toggle" });
    expect(pickFocusReturn([opener, toggle], () => false)).toBe(opener);
  });

  it("falls to the toggle when the opener left the page, is hidden, or is inside the panel", () => {
    const toggle = el({ name: "toggle" });
    expect(pickFocusReturn([el({ isConnected: false }), toggle], () => false)).toBe(toggle);
    expect(pickFocusReturn([el({ getClientRects: () => ({ length: 0 }) }), toggle], () => false)).toBe(toggle);
    const inside = el({ name: "inside" });
    expect(pickFocusReturn([inside, toggle], (e) => e === inside)).toBe(toggle);
    expect(pickFocusReturn([null, undefined, toggle], () => false)).toBe(toggle);
  });

  it("finds nothing when no candidate can take focus", () => {
    expect(pickFocusReturn([null, el({ isConnected: false })], () => false)).toBeNull();
  });

  it("the dock tries the opener, then the shell toggle, then the phone's Ask tab", () => {
    expect(DOCK).toMatch(
      /openerRef\.current,\s*document\.querySelector<HTMLElement>\("\[data-assistant-toggle\]"\),\s*document\.querySelector<HTMLElement>\("\[data-ask-tab\]"\),/,
    );
    // Only a focus that the close strands is moved.
    expect(DOCK).toContain("const lost = !at || at === document.body || Boolean(root?.contains(at));");
    // An open moves focus to the composer, without a scroll mid-move.
    expect(DOCK).toContain('root?.querySelector<HTMLTextAreaElement>("textarea")');
    expect(DOCK).toContain("if (box && !box.disabled && !elsewhere) box.focus({ preventScroll: true });");
  });
});

describe("a close never cuts a live run (WS-51 continuity)", () => {
  it("the closed panel stays mounted: hidden and inert, never `hidden` and never unrendered", () => {
    // The latch is the one `return null`: before the first open.
    expect(DOCK.match(/return null;/g)?.length).toBe(1);
    expect(DOCK).toContain("if (!mounted) return null;");
    expect(DOCK).toContain('inert={phase === "closed"}');
    expect(DOCK).not.toMatch(/\?\s*"hidden"/);
    expect(rule(GLOBALS, '.assistant-panel[data-panel-phase="closed"]')).toContain("visibility: hidden;");
  });

  it("the chat is keyed by its session alone, so a form change never remounts it", () => {
    expect(DOCK).toMatch(/<AgentChat\s+key=\{active\.id\}/);
    expect(DOCK).toContain("swapPanelForm(rootRef.current, () => setAssistantMode(next));");
  });
});

describe("the full-view switch", () => {
  const g = globalThis as Record<string, unknown>;
  const saved = { window: g.window, document: g.document };
  afterEach(() => {
    g.window = saved.window;
    g.document = saved.document;
  });

  function fakeDom(reduced: boolean, withApi: boolean) {
    const classes = new Set<string>();
    const start = vi.fn((update: () => void) => {
      update();
      return { finished: Promise.resolve() };
    });
    g.window = { matchMedia: () => ({ matches: reduced }) };
    g.document = {
      documentElement: {
        classList: { add: (c: string) => classes.add(c), remove: (c: string) => classes.delete(c) },
      },
      ...(withApi ? { startViewTransition: start } : {}),
    };
    const props = new Map<string, string>();
    const panel = {
      style: {
        setProperty: (k: string, v: string) => props.set(k, v),
        removeProperty: (k: string) => props.delete(k),
      },
    } as unknown as HTMLElement;
    return { start, classes, props, panel };
  }

  it("runs inside a view transition, names the panel, and cleans up after", async () => {
    const dom = fakeDom(false, true);
    const update = vi.fn();
    expect(swapPanelForm(dom.panel, update)).toBe(true);
    expect(dom.start).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledTimes(1);
    expect(dom.props.get("view-transition-name")).toBe(PANEL_SWAP_NAME);
    expect(dom.classes.has(PANEL_SWAP_CLASS)).toBe(true);
    await Promise.resolve();
    await Promise.resolve();
    expect(dom.props.has("view-transition-name")).toBe(false);
    expect(dom.classes.has(PANEL_SWAP_CLASS)).toBe(false);
  });

  it("is instant with reduced motion", () => {
    const dom = fakeDom(true, true);
    const update = vi.fn();
    expect(prefersReducedMotion()).toBe(true);
    expect(swapPanelForm(dom.panel, update)).toBe(false);
    expect(dom.start).not.toHaveBeenCalled();
    expect(update).toHaveBeenCalledTimes(1);
    expect(dom.classes.size).toBe(0);
  });

  it("is instant where the browser has no view transitions", () => {
    const dom = fakeDom(false, false);
    const update = vi.fn();
    expect(swapPanelForm(dom.panel, update)).toBe(false);
    expect(update).toHaveBeenCalledTimes(1);
  });

  it("a second press mid-swap keeps its name: the first swap's end cleans up nothing", async () => {
    const dom = fakeDom(false, true);
    const ends: (() => void)[] = [];
    dom.start.mockImplementation((update: () => void) => {
      update();
      return { finished: new Promise<void>((r) => ends.push(r)) };
    });
    swapPanelForm(dom.panel, vi.fn());
    swapPanelForm(dom.panel, vi.fn());
    ends[0]();
    await Promise.resolve();
    expect(dom.props.get("view-transition-name")).toBe(PANEL_SWAP_NAME);
    expect(dom.classes.has(PANEL_SWAP_CLASS)).toBe(true);
    ends[1]();
    await Promise.resolve();
    expect(dom.props.has("view-transition-name")).toBe(false);
    expect(dom.classes.has(PANEL_SWAP_CLASS)).toBe(false);
  });
});
