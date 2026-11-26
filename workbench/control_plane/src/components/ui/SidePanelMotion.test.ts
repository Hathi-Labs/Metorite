/**
 * One motion for every docked side panel (task 7, owner answer 2026-11-22).
 *
 * The runner has no DOM, so this tests the presence rule as pure functions,
 * the first markup through `renderToStaticMarkup`, and the rest as source
 * fences: the frame reads the assistant's tokens, each docked panel opens in
 * the shared frame, and no panel writes a motion of its own.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import CardPanel from "@/components/CardPanel";
import SidePanelMotion from "@/components/ui/SidePanelMotion";
import {
  PANEL_MOTION,
  SIDE_DOCKED_ATTR,
  isPanelContent,
  nextPhase,
  presenceContent,
  settlesAtOnce,
  type PanelEvent,
  type PanelPhase,
} from "@/lib/panelMotion";
import { TASK_PANEL_WIDTH } from "@/lib/taskPanel";

const SRC = join(__dirname, "..", "..");
// A Windows checkout has CRLF line ends. The fences read LF, as CI does.
const read = (rel: string) => readFileSync(join(SRC, rel), "utf-8").replace(/\r\n/g, "\n");
/** The code of a file, with its comments gone, so a note cannot pass a fence. */
const code = (rel: string) =>
  read(rel)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");
const GLOBALS = read("app/globals.css");

/** The body of the first rule whose selector is exactly `selector`. */
function rule(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) return "";
  const open = css.indexOf("{", at);
  return css.slice(open + 1, css.indexOf("}", open));
}

/**
 * A host's story, step by step: what it gives (a node or null) and what the
 * phase machine hears. Returns what the frame draws after each step.
 */
function walk(steps: { content: string | null; event?: PanelEvent }[]): (string | null)[] {
  let phase: PanelPhase = "closed";
  let kept: string | null = null;
  return steps.map(({ content, event }) => {
    if (event) phase = nextPhase(phase, event);
    if (content !== null) kept = content;
    const drawn = presenceContent(content, kept, phase);
    if (drawn === null) kept = null;
    return drawn;
  });
}

describe("the presence: a panel stays mounted until its close ends", () => {
  it("only a real node is content: null, undefined and false close the panel", () => {
    expect(isPanelContent("panel")).toBe(true);
    expect(isPanelContent(0)).toBe(true);
    for (const none of [null, undefined, false, true]) expect(isPanelContent(none)).toBe(false);
  });

  it("draws the host's node in every phase while the host gives one", () => {
    for (const phase of ["closed", "opening", "open", "closing"] as PanelPhase[]) {
      expect(presenceContent("now", "before", phase)).toBe("now");
    }
  });

  it("keeps the last node through the close, and drops it once the panel has closed", () => {
    expect(presenceContent(null, "last", "open")).toBe("last");
    expect(presenceContent(null, "last", "closing")).toBe("last");
    expect(presenceContent(null, "last", "closed")).toBeNull();
  });

  it("a whole open and close: mounted on open, still drawn while it closes, gone at the end", () => {
    expect(
      walk([
        { content: "A" }, // the host opens the panel; it mounts closed for two frames
        { content: "A", event: "open" },
        { content: "A", event: "settled" },
        { content: null }, // the host closes it; the effect has not run yet
        { content: null, event: "close" },
        { content: null, event: "settled" },
      ]),
    ).toEqual(["A", "A", "A", "A", "A", null]);
  });

  it("reduced motion: the close is at once, so the panel unmounts with no wait", () => {
    expect(
      walk([
        { content: "A" },
        { content: "A", event: "open-now" },
        { content: null, event: "close-now" },
      ]),
    ).toEqual(["A", "A", null]);
  });

  it("a new node during the close opens the panel again, with the new node", () => {
    expect(
      walk([
        { content: "A", event: "open-now" },
        { content: null, event: "close" },
        { content: "B", event: "open" },
      ]),
    ).toEqual(["A", "A", "B"]);
  });

  it("a frame with no box settles at once: a card over the page has nothing to move", () => {
    expect(settlesAtOnce("contents")).toBe(true);
    expect(settlesAtOnce("none")).toBe(true);
    expect(settlesAtOnce(null)).toBe(true);
    expect(settlesAtOnce("flex")).toBe(false);
  });

  it("the hook runs on the assistant's phase machine and its token, and latches with state", () => {
    const motion = code("lib/panelMotion.ts");
    expect(motion).toContain("usePanelPhase(now !== null, PANEL_MOTION.durationMs)");
    expect(motion).toContain("presenceContent(now, kept, phase)");
    // The frame waits for its own width, then settles. The close of a frame
    // with no box settles in a layout effect.
    const frame = code("components/ui/SidePanelMotion.tsx");
    expect(frame).toContain('e.propertyName !== "width"');
    expect(frame).toContain('if (phase !== "closing") return;');
    expect(frame).toContain('inert={phase === "closing"}');
    expect(PANEL_MOTION.durationMs).toBe(320);
  });
});

describe("the first markup", () => {
  const draw = (children: string | null, docked = false) =>
    renderToStaticMarkup(createElement(SidePanelMotion, { docked }, children));

  it("a closed panel draws nothing", () => {
    expect(draw(null)).toBe("");
  });

  it("an open panel mounts in its closed shape, so the open can move from it", () => {
    const html = draw("the panel");
    expect(html).toContain('class="side-panel"');
    expect(html).toContain('data-panel-phase="closed"');
    expect(html).not.toContain("data-shown");
    expect(html).toContain("the panel");
  });

  it("only a docked host marks the body, and a card marks its own host at the side stop", () => {
    expect(draw("x", true)).toContain(`${SIDE_DOCKED_ATTR}=""`);
    expect(draw("x", false)).not.toContain(SIDE_DOCKED_ATTR);
    const card = (stop: "side" | "full" | "phone") =>
      renderToStaticMarkup(createElement(CardPanel, { stop, label: "Deal: Acme", onClose: () => undefined, children: "b" }));
    expect(card("side")).toContain(`${SIDE_DOCKED_ATTR}=""`);
    expect(card("full")).not.toContain(SIDE_DOCKED_ATTR);
    expect(card("phone")).not.toContain(SIDE_DOCKED_ATTR);
  });
});

describe("the CSS: the assistant's dock form, on its tokens", () => {
  it("the frame moves its width on --panel-duration and --panel-easing, and writes no time of its own", () => {
    const moving = rule(GLOBALS, ".side-panel[data-moving]");
    expect(moving).toContain("transition: width var(--panel-duration) var(--panel-easing);");
    // The assistant's spacer reads the same two tokens.
    expect(rule(GLOBALS, ".assistant-spacer[data-moving]")).toContain(
      "transition: width var(--panel-duration) var(--panel-easing);",
    );
    const start = GLOBALS.indexOf("/* ── A docked side panel");
    const block = GLOBALS.slice(start, GLOBALS.indexOf("/* The side panel to the full view", start));
    expect(start).toBeGreaterThan(0);
    expect(block.replace(/\/\*[\s\S]*?\*\//g, "")).not.toMatch(/\d+m?s\b|cubic-bezier|ease-(?:in|out)/);
  });

  it("a docked frame opens to the width of a docked panel, max-w-sm", () => {
    expect(TASK_PANEL_WIDTH).toBe("max-w-sm");
    expect(rule(GLOBALS, ".side-panel[data-shown]:has([data-side-docked])")).toContain(
      "width: var(--container-sm, 24rem);",
    );
    expect(rule(GLOBALS, ".side-panel:has([data-side-docked]) > [data-side-panel-body]")).toContain(
      "width: var(--container-sm, 24rem);",
    );
    // A frame with no docked panel has no box, so a card over the page keeps
    // its own frame (`absolute`, `fixed`) and nothing wraps it.
    expect(rule(GLOBALS, ".side-panel,\n.side-panel > [data-side-panel-body]")).toContain("display: contents;");
  });

  it("the frame clips the panel only while it moves", () => {
    expect(rule(GLOBALS, '.side-panel:not([data-panel-phase="open"]):has([data-side-docked])')).toContain(
      "overflow: clip;",
    );
  });

  it("reduced motion stops the frame, as it stops the assistant", () => {
    const at = GLOBALS.indexOf("@media (prefers-reduced-motion: reduce)");
    const block = GLOBALS.slice(at, GLOBALS.indexOf("\n}\n", at));
    expect(block).toMatch(/\.side-panel\[data-moving\] \{\s*transition: none;/);
  });
});

describe("each docked side panel opens in the shared frame", () => {
  it("Projects: the docked task panel, and a switch to full drops the frame at once", () => {
    const page = code("app/projects/page.tsx");
    expect(page).toContain('import SidePanelMotion from "@/components/ui/SidePanelMotion";');
    expect(page).toContain("{isOverlayMode(panelMode) ? null : <SidePanelMotion docked>{taskPanel}</SidePanelMotion>}");
  });

  it("the CRM: both record sheets", () => {
    const page = code("app/crm/page.tsx");
    expect(page.match(/<SidePanelMotion>\s*\{view\.(record|moduleRecord) && \(/g)).toHaveLength(2);
    expect(page.match(/<\/SidePanelMotion>/g)).toHaveLength(2);
  });

  it("My Day: the peek card", () => {
    expect(code("lib/shell/MyDayPage.tsx")).toContain("<SidePanelMotion>{peek.panel}</SidePanelMotion>");
  });

  it("the record card marks its side stop, and an inert card hears no Escape", () => {
    const card = code("components/CardPanel.tsx");
    expect(card).toContain('{...(stop === "side" ? { [SIDE_DOCKED_ATTR]: "" } : {})}');
    expect(card).toContain('if (!aside || aside.closest("[inert]")) return;');
  });

  it("no panel writes a motion of its own: no duration, curve, transition or animation", () => {
    for (const file of [
      "app/projects/components/TaskPanel.tsx",
      "components/CardPanel.tsx",
      "app/crm/components/RecordShell.tsx",
      "lib/shell/MyDayPeekPanel.tsx",
      "components/ui/SidePanelMotion.tsx",
    ]) {
      expect(code(file), file).not.toMatch(/duration-|\d+ms\b|cubic-bezier|transition-|animate-|tech-transition/);
    }
  });
});
