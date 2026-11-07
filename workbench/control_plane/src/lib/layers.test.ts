/**
 * TC-1 (D172.4, 2026-11-03): a task card never covers the assistant. This
 * fence pins the layer order in `lib/layers.ts` and checks that the card
 * files take their layer from it.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { LAYER, LAYER_CLASS, TASK_CARD_FRAME } from "@/lib/layers";

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf-8");

describe("the layer scale", () => {
  it("keeps every class string in step with its number", () => {
    for (const key of Object.keys(LAYER) as (keyof typeof LAYER)[]) {
      expect([`z-[${LAYER[key]}]`, `z-${LAYER[key]}`]).toContain(LAYER_CLASS[key]);
    }
  });

  it("puts the pill, the bottom bar and the assistant above the task card", () => {
    expect(LAYER.assistantDock).toBeGreaterThan(LAYER.taskCard);
    expect(LAYER.askBar).toBeGreaterThan(LAYER.taskCard);
    expect(LAYER.peekStrip).toBeGreaterThan(LAYER.taskCard);
    expect(LAYER.bottomNav).toBeGreaterThan(LAYER.taskCard);
    expect(LAYER.assistantSheet).toBeGreaterThan(LAYER.taskCard);
  });

  it("draws the card frames at the card layer", () => {
    expect(TASK_CARD_FRAME.desktop).toContain(LAYER_CLASS.taskCard);
    expect(TASK_CARD_FRAME.phone).toContain(LAYER_CLASS.taskCard);
  });

  it("keeps the desktop card in the page column and the phone card above the nav", () => {
    expect(TASK_CARD_FRAME.desktop).toMatch(/\babsolute\b/);
    expect(TASK_CARD_FRAME.desktop).not.toMatch(/\bfixed\b/);
    expect(TASK_CARD_FRAME.phone).toContain("task-card-phone");
    expect(TASK_CARD_FRAME.phone).not.toContain("bottom-[calc(");
  });
});

describe("the surfaces take their layer from the scale", () => {
  it("has no ad-hoc card layer in Projects or the task overlay", () => {
    const projects = read("app/projects/page.tsx");
    expect(projects).toContain("TASK_CARD_FRAME.phone");
    expect(projects).toContain("TASK_CARD_FRAME.desktop");
    expect(projects).not.toContain("fixed inset-0 z-[60]");
    const modal = read("app/tasks/components/TaskFocusModal.tsx");
    expect(modal).toContain("TASK_CARD_FRAME");
    expect(modal).not.toContain("z-[80]");
  });

  it("draws the shell parts from the scale", () => {
    expect(read("components/AppShell.tsx")).toContain("LAYER_CLASS.bottomNav");
    expect(read("lib/shell/ChatBar.tsx")).toContain("LAYER_CLASS.askBar");
    const dock = read("lib/shell/AssistantDock.tsx");
    expect(dock).toContain("LAYER_CLASS.assistantSheet");
    expect(dock).toContain("LAYER_CLASS.peekStrip");
    expect(dock).toContain("LAYER_CLASS.assistantDock");
  });
});

describe("a page's own layers cannot rise over the task card (TC-1 fix)", () => {
  it("contains the /tasks list scrollers and the Calendar grid in their own stacking context", () => {
    for (const f of [
      "app/tasks/components/TaskListGrouped.tsx",
      "app/tasks/components/EngageView.tsx",
      "app/tasks/components/WaitingForView.tsx",
    ]) {
      expect(read(f), f).toMatch(/className="isolate flex-1 overflow-y-auto/);
    }
    expect(read("app/calendar/CalendarView.tsx")).toMatch(
      /className="relative isolate flex h-full min-h-0 flex-col/,
    );
  });
});

describe("the phone card follows the peek strip (TC-1 fix, MOB-9)", () => {
  const css = read("app/globals.css");
  it("pads the card's foot by the strip only, since the card ends at the bar", () => {
    expect(css).toMatch(/\.task-card-phone\s*\{\s*padding-bottom:\s*0;/);
    expect(css).toMatch(
      /:root\[data-assistant-peek\] \.task-card-phone\s*\{\s*padding-bottom:\s*var\(--task-card-peek-h\)/,
    );
    expect(css).not.toContain("--task-card-nav-clear");
  });
});

// MOB-9 (task 2002): the phone's bottom bar always shows. The bar paints above
// every overlay but a confirmation and the toasts. A fixed layer ends at the
// bar's top edge through `globals.css`, so the bar never hides under one.
const SRC = fileURLToPath(new URL("..", import.meta.url));
const tsxFiles = (): string[] => {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx$/.test(entry)) out.push(relative(SRC, full).split(sep).join("/"));
    }
  };
  walk(SRC);
  return out;
};
/** The z-index of every `fixed` class string in the tree, outside Modal and the toasts. */
const fixedLayers = (): { file: string; z: number }[] => {
  const out: { file: string; z: number }[] = [];
  for (const file of tsxFiles()) {
    if (file === "components/ui/Modal.tsx" || file === "components/ui/Toast.tsx") continue;
    for (const m of read(file).matchAll(/"([^"\n]*)"|`([^`]*)`/g)) {
      const cls = m[1] ?? m[2];
      if (!/(^|\s)fixed(\s|$)/.test(cls)) continue;
      const z = cls.match(/(?:^|\s)z-(?:\[(\d+)\]|(\d+))(?:\s|$)/);
      if (z) out.push({ file, z: Number(z[1] ?? z[2]) });
    }
  }
  return out;
};

describe("the phone's bottom bar always shows (MOB-9)", () => {
  it("paints above the highest fixed overlay of the tree and the anchored panels", () => {
    const layers = fixedLayers();
    expect(layers.length, "the scan went vacuous").toBeGreaterThan(20);
    const top = layers.reduce((a, b) => (b.z > a.z ? b : a));
    expect(
      LAYER.bottomNav,
      `${top.file} paints a fixed overlay at z-${top.z}, over the bottom bar. Raise LAYER.bottomNav, or lower the overlay.`,
    ).toBeGreaterThan(top.z);
    const panels = read("components/ui/AnchoredPanel.tsx");
    const panelTop = Number(panels.match(/top:\s*"z-\[(\d+)\]"/)?.[1]);
    expect(LAYER.bottomNav).toBeGreaterThan(panelTop);
    expect(LAYER.bottomNav).toBeGreaterThan(70); // the shell drawer
  });

  it("paints below a confirmation and the toasts", () => {
    const alert = Number(read("components/ui/Modal.tsx").match(/alert:\s*"z-\[(\d+)\]"/)?.[1]);
    const toast = Number(read("components/ui/Toast.tsx").match(/fixed right-4 z-\[(\d+)\]/)?.[1]);
    expect(LAYER.bottomNav).toBeLessThan(alert);
    expect(LAYER.bottomNav).toBeLessThan(toast);
    // The guided tour dims the bar and rings its Ask tab, so it sits between.
    const guide = Number(read("components/ui/AnchoredPanel.tsx").match(/guide:\s*"z-\[(\d+)\]"/)?.[1]);
    expect(guide).toBeGreaterThan(LAYER.bottomNav);
    expect(guide).toBeLessThan(alert);
  });

  it("marks the bar, sets the attribute with the phone layout, and sizes the bar", () => {
    const shell = read("components/AppShell.tsx");
    expect(shell).toMatch(/data-phone-nav-bar=""[^>]*h-\[var\(--phone-bar-h\)\][^>]*pb-\[var\(--phone-bar-foot\)\]/);
    // Square top corners, in the glass look too: a sheet ends on the bar's top
    // edge, and round corners left two gaps where it seemed to run on behind
    // the bar (owner, 2026-11-07).
    const bar = shell.match(/data-phone-nav-bar=""[^>]*>/)?.[0] ?? "";
    expect(bar).not.toMatch(/rounded-t/);
    // The hairline stays in the glass look, so a list a sheet cuts off reads
    // as ending at the bar, not as running on behind it.
    expect(bar).toMatch(/border-t border-border/);
    expect(bar).not.toMatch(/glass:border-t-0/);
    expect(shell).toMatch(/<PhoneNavFlag \/>/);
    expect(shell).toMatch(/setAttribute\("data-phone-nav", ""\)[\s\S]*removeAttribute\("data-phone-nav"\)/);
  });

  it("sizes the bar in one place, with a foot that never drops under 0.75rem", () => {
    // Owner, 2026-11-07: "on iPhone the corners are quite rounded ... you might
    // need to have some space at the bottom". The foot is the home-indicator
    // inset, never less than 0.75rem. Every clearance reads `--phone-bar-h`.
    const css = read("app/globals.css");
    expect(css).toMatch(/--phone-bar-foot:\s*max\(env\(safe-area-inset-bottom, 0px\), 0\.75rem\);/);
    expect(css).toMatch(/--phone-bar-h:\s*calc\(3\.5rem \+ var\(--phone-bar-foot\)\);/);
    // Nobody adds 3.5rem and the inset by hand any more: a second sum drifts
    // from the bar the day the foot changes, and a dock then sits on a tab.
    const src = fileURLToPath(new URL("../", import.meta.url));
    const byHand: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
          if (name !== "node_modules") walk(p);
        } else if (/\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
          const text = readFileSync(p, "utf-8");
          if (/3\.5rem[^;"`\n]*safe-area-inset-bottom|safe-area-inset-bottom[^;"`\n]*3\.5rem/.test(text)) {
            byHand.push(relative(src, p).split(sep).join("/"));
          }
        }
      }
    };
    walk(src);
    expect(byHand, "these files add 3.5rem and the inset by hand; read --phone-bar-h").toEqual([]);
  });

  it("ends every full-height fixed layer at the bar's top edge, and never the bar", () => {
    const css = read("app/globals.css");
    expect(css).toMatch(/:root\[data-phone-nav\]\s*\{\s*--phone-nav-h:\s*var\(--phone-bar-h\)/);
    const rule = css.match(/:root\[data-phone-nav\] :is\(([^)]*)\):not\(\[data-phone-nav-bar\]\)\s*\{\s*bottom:\s*var\(--phone-nav-h\)/);
    expect(rule, "the phone-nav rule moved or changed shape").not.toBeNull();
    for (const sel of [".fixed.inset-0", ".fixed.inset-y-0", ".fixed.bottom-0", ".phone-nav-clear"]) {
      expect(rule![1]).toContain(sel);
    }
    // The bar covers the home-indicator inset, so a layer above it drops its own.
    expect(css).toMatch(/\.pb-safe\s*\{\s*padding-bottom:\s*0;/);
  });

  it("closes a Modal on a tap of the bar, which the substrate does not do", () => {
    const modal = read("components/ui/Modal.tsx");
    // MOB-9 fix round: the shell sends one bar-tap signal, and the Modal listens (`phoneBarTap.ts`).
    expect(modal).toContain("useCloseOnBarTap(onClose, open)");
  });

  it("drops the sheet's own safe-area padding, since the bar covers the inset", () => {
    expect(read("lib/shell/AssistantDock.tsx")).not.toMatch(/fixed inset-0 \$\{LAYER_CLASS\.assistantSheet\}[^`]*pb-safe/);
  });
});
