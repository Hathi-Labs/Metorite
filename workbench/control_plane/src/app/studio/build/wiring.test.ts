/**
 * The wiring of G5 in the IDE. `vitest.config.ts` runs in `node` with no DOM and no testing-library,
 * so a component cannot mount here. These source pins hold each wiring point that a pure test cannot
 * reach. The behaviour behind each point has its own pure test (`landedChange.test.ts`, `publish.test.ts`).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8").replace(/\r\n/g, "\n");
const page = read("./page.tsx");
const dialog = read("./components/PublishDialog.tsx");
const hook = read("../../../lib/studio/useLandedChange.ts");
const bar = read("./components/ChangeBar.tsx");

describe("G5 wiring", () => {
  it("marks the page's own save as You saved before it adopts the draft", () => {
    expect(page).toMatch(/adopt: \(next: PackageDraft\) => \{ markOwn\("you", next\.manifestHash\); rawWatch\.adopt\(next\); \}/);
  });
  it("sends the notes and the frozen hash to the one publish call", () => {
    expect(dialog).toMatch(/publishDraft\(pkg, notes, seen\.hash\)/);
    expect(dialog).toMatch(/value=\{notes\}/);
  });
  it("closes the dialog only when no publish runs", () => {
    expect(dialog).toMatch(/onClose=\{\(\) => guardedClose\(busy, p\.onClose\)\}/);
    expect(dialog).not.toMatch(/setBusy\(false\);\n\s*setSnap/);
  });
  it("Undo goes through the tracker and the one save, and not through a second path", () => {
    expect(hook).toMatch(/runUndo\(pkg, draft, landed\)/);
    expect(hook).toMatch(/if \(!draft \|\| !landed \|\| undoing \|\| liveBlock\) return;/);
    expect(hook).not.toMatch(/fetch\(/);
  });
  it("locks the tabs and the Code tab while an Undo runs", () => {
    expect(page).toMatch(/const locked = editor\.locked \|\| change\.undoing;/);
    expect(page).toMatch(/readOnly=\{editor\.state\.saving \|\| change\.undoing\}/);
  });
  it("says Draft for an app with no live version, as the library does", () => {
    expect(page).toMatch(/Live v\$\{draft\.liveVersion\}` : "Draft"/);
  });
  it("shows the short list again for a new change, and passes the live block to Undo", () => {
    expect(bar).toMatch(/if \(landed !== seen\) \{ setSeen\(landed\); setAll\(false\); \}/);
    expect(bar).toMatch(/liveDrop: liveBlock/);
    expect(page).toMatch(/liveBlock=\{change\.liveBlock\}/);
  });
});
