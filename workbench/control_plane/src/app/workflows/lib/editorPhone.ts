/**
 * The workflow editor on a phone (`ai_studio.md` §14, row E7a).
 *
 * A desktop draws three panes side by side: the left column (the palette or
 * the Copilot), the canvas and the step inspector. At 390 px the two side
 * panes take 464 px, so the canvas got no width and Test and Publish fell off
 * the toolbar. A phone now draws ONE pane at a time under a segmented tab bar,
 * the shape of the Studio IDE on a phone (`app/studio/build/page.tsx`).
 *
 * `ViewModeProvider` decides the phone: below 768 px. From 768 px up the
 * editor draws the desktop tree, unchanged.
 *
 * This file holds the rules and no markup, so a test holds them
 * (`editorPhone.test.ts`).
 */

/** A pane of the editor. The left column of a desktop holds `palette` or `copilot`. */
export type EditorPane = "palette" | "copilot" | "canvas" | "inspector";

/** The tab of the left column of a desktop. */
export type LeftTab = "palette" | "copilot";

/** A tab of the phone bar. `step` is the inspector of the selected step. */
export type PhoneTab = "palette" | "canvas" | "step" | "copilot";

export const PHONE_TABS: { id: PhoneTab; label: string; icon: string }[] = [
  { id: "palette", label: "Palette", icon: "Blocks" },
  { id: "canvas", label: "Canvas", icon: "Workflow" },
  { id: "step", label: "Step", icon: "SlidersHorizontal" },
  { id: "copilot", label: "Copilot", icon: "Sparkles" },
];

/** A phone opens on the canvas, because the graph is what the member came to see. */
export const DEFAULT_PHONE_TAB: PhoneTab = "canvas";

const PANE_OF_TAB: Record<PhoneTab, EditorPane> = {
  palette: "palette",
  canvas: "canvas",
  step: "inspector",
  copilot: "copilot",
};

/**
 * The panes that draw. A desktop draws all three. A phone draws exactly the
 * one pane of its tab.
 */
export function visiblePanes(isMobile: boolean, tab: PhoneTab, leftTab: LeftTab): EditorPane[] {
  if (!isMobile) return [leftTab, "canvas", "inspector"];
  return [PANE_OF_TAB[tab] ?? "canvas"];
}

/**
 * The left tab after a tap on a phone tab. The Palette and the Copilot tabs
 * set it, so the left column draws that pane. The other tabs keep it.
 */
export function leftTabFor(tab: PhoneTab, current: LeftTab): LeftTab {
  return tab === "palette" || tab === "copilot" ? tab : current;
}

/** An act on a phone that moves the member to another tab. */
export type PhoneEvent = "add" | "arm" | "generate" | "test";

/**
 * The tab that a phone opens after an act.
 *
 * - `add`: a tap on a block adds the step and selects it, so its settings open.
 * - `arm`: a "+" on a wire asks for a block, so the palette opens.
 * - `generate`: the prompt on the canvas sends its words to the Copilot.
 * - `test`: the run shows on the graph, so the canvas opens.
 *
 * A tap on a step on the canvas selects it and keeps the canvas, so a drag
 * of a step never leaves the canvas. The Step tab then holds that step.
 */
export function phoneTabAfter(event: PhoneEvent): PhoneTab {
  switch (event) {
    case "add":
      return "step";
    case "arm":
      return "palette";
    case "generate":
      return "copilot";
    case "test":
      return "canvas";
  }
}

/**
 * Whether the run console is shut. `choice` is null until the member opens or
 * shuts it, or presses Test. Until then a desktop opens it, and a phone keeps
 * it shut, because 192 px of console would take a third of the canvas.
 */
export function runConsoleCollapsed(choice: boolean | null, isMobile: boolean): boolean {
  return choice ?? isMobile;
}
