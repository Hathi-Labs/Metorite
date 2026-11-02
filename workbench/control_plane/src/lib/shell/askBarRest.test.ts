/**
 * The Ask bar reserves no space (owner, 2026-11-02). It floats over the page
 * in the largest form that hides no control: the field, the "Ask" pill or the
 * corner button (`askBarRest.ts`).
 *
 * ⚠️ The geometry here is pure. What the browser reports under the bar
 * (`controlsUnder`) needs a real page: the `visual-review` skill is that gate.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ASK_CORNER_SIZE,
  ASK_FULL_MAX,
  ASK_GAP,
  ASK_LIFT_GAP,
  ASK_PILL_WIDTH,
  type Box,
  askRest,
  cornerBox,
  cornerLift,
  foldKey,
  inTheWay,
  maxLift,
  readFolds,
  restBoxes,
  scrollClears,
  writeFold,
} from "./askBarRest";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

/** A 1200 x 800 page at the origin. */
const PAGE: Box = { left: 0, top: 0, right: 1200, bottom: 800 };
const box = (left: number, top: number, w: number, h: number): Box => ({ left, top, right: left + w, bottom: top + h });

describe("the three forms", () => {
  it("centres the field and the pill at the foot, and puts the button in the corner", () => {
    const b = restBoxes(PAGE);
    expect(b.full.right - b.full.left).toBe(ASK_FULL_MAX);
    expect((b.full.left + b.full.right) / 2).toBe(600);
    expect(b.full.bottom).toBe(800 - ASK_GAP);
    expect(b.pill.right - b.pill.left).toBe(ASK_PILL_WIDTH);
    expect(b.corner).toEqual({ left: 1200 - ASK_GAP - ASK_CORNER_SIZE, right: 1200 - ASK_GAP, top: 800 - ASK_GAP - ASK_CORNER_SIZE, bottom: 800 - ASK_GAP });
  });

  it("narrows the field on a narrow page", () => {
    const b = restBoxes({ left: 0, top: 0, right: 400, bottom: 800 });
    expect(b.full.right - b.full.left).toBe(400 - 48);
  });
});

describe("a control is in the way when the bar covers its middle or more than a tenth of it", () => {
  const bar = box(300, 734, 600, 46);

  it("counts a covered middle", () => {
    expect(inTheWay(box(580, 740, 40, 28), bar)).toBe(true);
  });

  it("counts the end of a button, where its label sits", () => {
    // Measured 2026-11-02: the pill hid a lane's "+ Add" label while it
    // covered a fifth of the button, and the middle was clear.
    const add = box(840, 862, 260, 26);
    const pill = box(790, 836, 132, 44);
    expect(inTheWay(add, pill)).toBe(true);
  });

  it("lets a control the bar only grazes stay", () => {
    expect(inTheWay(box(205, 740, 100, 28), bar)).toBe(false);
    expect(inTheWay(box(500, 690, 80, 46), bar)).toBe(false);
  });

  it("ignores an empty box", () => {
    expect(inTheWay(box(580, 740, 0, 28), bar)).toBe(false);
  });
});

describe("the bar takes the largest form that hides nothing", () => {
  it("is the field over an empty foot", () => {
    expect(askRest([], PAGE)).toBe("full");
    expect(askRest([box(40, 40, 200, 30)], PAGE)).toBe("full");
  });

  it("is the pill when a control sits under the field's ends", () => {
    // A board column's "+ Add" under the left part of the field.
    expect(askRest([box(320, 744, 272 - 100, 28)], PAGE)).toBe("pill");
  });

  it("is the corner button when a control sits under the middle", () => {
    expect(askRest([box(560, 744, 80, 28)], PAGE)).toBe("corner");
  });
});

describe("content that can scroll clear of the bar is not in the way", () => {
  it("lets a control the scroll can lift past the bar stay", () => {
    expect(scrollClears(400, 870, 834)).toBe(true);
    expect(scrollClears(36 + ASK_LIFT_GAP, 870, 834)).toBe(true);
  });

  it("counts a control near the end of its scroll", () => {
    // Measured 2026-11-02: a lane's "+ Add" with 17 px of scroll left.
    expect(scrollClears(17, 888, 834)).toBe(false);
    expect(scrollClears(0, 870, 834)).toBe(false);
  });
});

describe("the corner button lifts above what its corner would hide", () => {
  it("stays at the gap over an empty corner", () => {
    expect(cornerLift([], PAGE)).toBe(ASK_GAP);
  });

  it("stays down when it only clips the end of a wide row", () => {
    const row = box(500, 740, 690, 54);
    expect(cornerLift([row], PAGE)).toBe(ASK_GAP);
  });

  it("sits above a control in its corner", () => {
    const send = box(1130, 740, 40, 32);
    expect(cornerLift([send], PAGE)).toBe(800 - 740 + ASK_LIFT_GAP);
  });

  it("moves from where it is, so the caller can look again at the new height", () => {
    const from = 68;
    const c = cornerBox(PAGE, from);
    expect(c.bottom).toBe(800 - from);
    const above = box(1140, c.top - 10, 30, 30);
    expect(cornerLift([above], PAGE, from)).toBe(800 - above.top + ASK_LIFT_GAP);
  });

  it("never climbs past half the page", () => {
    const tall = box(1132, 380, 48, 410);
    expect(cornerLift([tall], PAGE)).toBe(maxLift(PAGE));
    expect(maxLift(PAGE)).toBe(400);
  });
});

describe("the fold is per app, and a blocked store is no fold", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keys an app by its first path segment", () => {
    expect(foldKey("/projects/42")).toBe("/projects");
    expect(foldKey("/projects")).toBe("/projects");
    expect(foldKey("/")).toBe("/");
    expect(foldKey(null)).toBe("/");
  });

  it("remembers the apps the member folded", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (k: string) => data.get(k) ?? null,
        setItem: (k: string, v: string) => void data.set(k, v),
      },
    });
    writeFold("/projects", true);
    writeFold("/email", true);
    writeFold("/email", false);
    expect([...readFolds()]).toEqual(["/projects"]);
  });

  it("reads a throwing store, or junk in it, as no fold", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => {
          throw new Error("blocked");
        },
        setItem: () => {
          throw new Error("blocked");
        },
      },
    });
    expect(readFolds().size).toBe(0);
    expect(() => writeFold("/projects", true)).not.toThrow();
    vi.stubGlobal("window", { localStorage: { getItem: () => '{"x":1}', setItem: () => undefined } });
    expect(readFolds().size).toBe(0);
  });
});

describe("the bar at the foot reserves no space (source)", () => {
  const bar = read("lib/shell/ChatBar.tsx");

  it("takes its form from the page, and holds it while the member uses it", () => {
    expect(bar).toMatch(/const hold = !folded && \(inside \|\| hover \|\| opened \|\| sheet\);/);
    expect(bar).toMatch(/const \{ rest, lift \} = useAskRest\(wrapRef, app, hold\);/);
    expect(bar).toMatch(/const form: AskRest = folded \? "corner" : hold \? "full" : rest;/);
  });

  it("has a fold button, and ⌘K or a click brings the field back with the cursor in it", () => {
    expect(bar).toContain('aria-label="Fold the Ask bar into the corner"');
    expect(bar).toMatch(/if \(formRef\.current === "full"\) return;\s*expandRef\.current\(/);
    expect(bar).toMatch(/window\.dispatchEvent\(new CustomEvent<FocusAskFieldDetail>\(FOCUS_ASK_FIELD, \{ detail \}\)\);/);
  });

  it("each form is the one box the chat morphs into", () => {
    // `morphAskBox` finds the bar by `data-ask-surface`, in any form.
    expect(bar.match(/data-ask-surface=/g)?.length).toBe(2);
  });

  it("keeps the guide's Ask anchor in every form (D167)", () => {
    expect(bar.match(/data-guide="home\.ask"/g)?.length).toBe(2);
  });

  it("springs a new form in with scale alone, and holds still for reduced motion", () => {
    const css = read("app/globals.css");
    expect(css).toMatch(/@keyframes ask-rest-in \{\s*from \{ transform: translateY\(4px\) scale\(0\.92\); \}\s*\}/);
    const gate = css.indexOf("@media (prefers-reduced-motion: no-preference)", css.indexOf("@keyframes ask-rest-in"));
    expect(css.slice(gate, gate + 200)).toContain("html:not(.ask-morph) [data-ask-form]");
  });
});

describe("a board lane adds from its header (source)", () => {
  it("draws a + in the header of a lane that adds, which opens the box at the top", () => {
    const lane = read("components/board/BoardColumn.tsx");
    expect(lane).toMatch(/onAdd && header \? \(\s*<button[\s\S]{0,200}aria-label=\{addLabel\}/);
    expect(lane).toMatch(/onAdd && addAtTop \?[\s\S]{0,200}<QuickAdd label=\{addLabel\} onAdd=\{onAdd\} startOpen onClose=/);
  });
});
