/**
 * The one delete confirmation, used by both task apps (2026-09-24).
 *
 * Source tests: `vitest.config.ts` runs in `node`, with no DOM. They pin that
 * the dialog is built on `Modal`, and that each app draws its delete dialog
 * through it rather than a hand-rolled overlay or `window.confirm`.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

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

/** The z-index of every `fixed` class string in the tree, outside Modal. */
const fixedLayers = (): { file: string; z: number }[] => {
  const out: { file: string; z: number }[] = [];
  for (const file of tsxFiles()) {
    if (file === "components/ui/Modal.tsx") continue;
    // Double quotes and template literals, one line at most. Single quotes
    // are skipped: an apostrophe in a comment would pair with the wrong one.
    for (const m of read(file).matchAll(/"([^"\n]*)"|`([^`]*)`/g)) {
      const cls = m[1] ?? m[2];
      if (!/(^|\s)fixed(\s|$)/.test(cls)) continue;
      const z = cls.match(/(?:^|\s)z-(?:\[(\d+)\]|(\d+))(?:\s|$)/);
      if (z) out.push({ file, z: Number(z[1] ?? z[2]) });
    }
  }
  return out;
};

const alertLayer = () => {
  const m = read("components/ui/Modal.tsx").match(/alert:\s*"z-\[(\d+)\]"/);
  expect(m, "Modal's LAYERS.alert moved or changed shape").not.toBeNull();
  return Number(m![1]);
};

describe("a confirmation paints above every overlay", () => {
  it("the scan sees the overlays it has to beat", () => {
    // Guards the check below against going vacuous: My Tasks' focus view is
    // the overlay a delete is most often confirmed from.
    const layers = fixedLayers();
    expect(layers.some((l) => l.file === "app/tasks/components/TaskFocusModal.tsx" && l.z === 80)).toBe(true);
    expect(layers.length).toBeGreaterThan(20);
  });

  it("Modal's alert layer is above the highest fixed overlay in the tree", () => {
    const top = fixedLayers().reduce((a, b) => (b.z > a.z ? b : a));
    expect(
      alertLayer(),
      `${top.file} paints a fixed overlay at z-${top.z}. A confirmation opened ` +
        "from it would sit behind it, focused and invisible. Raise LAYERS.alert.",
    ).toBeGreaterThan(top.z);
  });

  it("ConfirmDialog uses the alert layer, and the default stays z-50", () => {
    expect(read("components/ui/ConfirmDialog.tsx")).toMatch(/layer="alert"/);
    const modal = read("components/ui/Modal.tsx");
    expect(modal).toMatch(/dialog:\s*"z-50"/);
    expect(modal).toMatch(/layer = "dialog"/);
    // Both the scrim and the viewport take the layer, or the scrim hides the popup.
    expect(modal.match(/\$\{LAYERS\[layer\]\}/g) ?? []).toHaveLength(2);
  });

  it("keeps the last words while it closes", () => {
    const src = read("components/ui/ConfirmDialog.tsx");
    expect(src).toMatch(/const words = open \? live : held;/);
    expect(src).not.toMatch(/\{(title|body|note|confirmLabel|subject)\}/);
  });
});

describe("ConfirmDialog", () => {
  it("is built on Modal, with no overlay of its own", () => {
    const src = read("components/ui/ConfirmDialog.tsx");
    expect(src).toMatch(/import Modal from "@\/components\/ui\/Modal";/);
    expect(src).toMatch(/<Modal\b/);
    expect(src).not.toMatch(/fixed inset-0/);
    expect(src).not.toMatch(/@base-ui\/react/);
  });

  it("confirms with the destructive Button and opens focused on it", () => {
    const src = read("components/ui/ConfirmDialog.tsx");
    expect(src).toMatch(/variant="destructive"[\s\S]*?data-confirm=""/);
    expect(src).toMatch(/querySelector<HTMLElement>\("\[data-confirm\]"\)/);
  });

  it.each([
    ["Projects", "app/projects/page.tsx"],
    ["My Tasks", "app/tasks/components/DeleteConfirmModal.tsx"],
  ])("%s draws its delete confirmation through it", (_app, file) => {
    const src = read(file);
    expect(src).toMatch(/import ConfirmDialog from "@\/components\/ui\/ConfirmDialog";/);
    expect(src).toMatch(/<ConfirmDialog\b/);
    expect(src).not.toMatch(/window\.confirm\(/);
  });
});
