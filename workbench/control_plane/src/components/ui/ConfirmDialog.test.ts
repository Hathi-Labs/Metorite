/**
 * The one delete confirmation, used by both task apps (2026-09-24).
 *
 * Source tests: `vitest.config.ts` runs in `node`, with no DOM. They pin that
 * the dialog is built on `Modal`, and that each app draws its delete dialog
 * through it rather than a hand-rolled overlay or `window.confirm`.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

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
