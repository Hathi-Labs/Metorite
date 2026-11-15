/**
 * STU-AF-8b-2 fence (source pins): the Pages tab of the Studio IDE.
 *
 * Mutation 1: show the tab, or the Pages branch, with no `APP_FRAME` guard. "shows only under the flag" turns red.
 * Mutation 2: drop `disabled={locked`  from a control. "locks every control" turns red.
 * Mutation 3: add `onKeyDown` or `fetch(` to the tab. "has no key handler and no network call" turns red.
 * Mutation 4: call `removePage(` outside the confirm. "asks before a delete" turns red.
 * Mutation 5: drop an `aria-label` from an icon button. "gives every icon button a name" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const here = new URL("./", import.meta.url);
const read = (rel: string) => readFileSync(new URL(rel, here), "utf8").replace(/\r\n/g, "\n");
const tab = read("components/PagesTab.tsx");
const model = readFileSync(new URL("../../../lib/studio/pagesTabModel.ts", here), "utf8");
const page = read("page.tsx");

describe("PagesTab", () => {
  it("has no key handler and no network call", () => {
    for (const src of [tab, model]) {
      expect(src).not.toMatch(/onKeyDown|keydown|\bfetch\(|XMLHttpRequest/);
    }
  });

  it("uses CommitInput for text and builds no control by hand", () => {
    expect(tab).toContain("<CommitInput");
    expect(tab).not.toMatch(/<Input[\s>]/);
    expect(tab).not.toMatch(/from "@\/components\/ui\/Input"/);
    expect(tab).not.toMatch(/<(button|select|input|textarea|dialog)[\s>]/);
    expect(tab).not.toMatch(/lucide-react/);
  });

  it("uses no colour of its own", () => {
    expect(tab).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(tab).not.toMatch(/\[\d+px\]|\bpx-\[|(?:bg|text|border)-\[/);
  });

  it("gives every icon button a name", () => {
    const icons = tab.match(/<Button[^>]*size="icon-sm"[\s\S]*?\/>/g) ?? [];
    expect(icons.length).toBeGreaterThanOrEqual(8);
    for (const b of icons) expect(b).toContain("aria-label=");
  });

  it("locks every control", () => {
    const controls = tab.match(/<(?:Button|SelectButton|CommitInput)\b[\s\S]*?(?:\/>|<\/Button>)/g) ?? [];
    expect(controls.length).toBeGreaterThanOrEqual(15);
    for (const c of controls) expect(c).toMatch(/disabled=\{locked|disabled: locked/);
    // The empty-state action carries `locked` too.
    expect(tab).toContain("disabled: locked ||");
  });

  it("asks before a delete", () => {
    expect(tab.match(/removePage\(/g)).toHaveLength(1);
    expect(tab.match(/removeSection\(/g)).toHaveLength(1);
    const confirm = tab.slice(tab.indexOf("onConfirm={"), tab.indexOf("onCancel={"));
    expect(confirm).toContain("removePage(");
    expect(confirm).toContain("removeSection(");
    expect(tab).toContain("setAsk({ kind: \"page\"");
  });

  it("sends each act through applyFrame, so a bad request sends nothing", () => {
    expect(tab).toContain("applyFrame(manifest, onChange, fn)");
    expect(tab).not.toMatch(/onChange\(/);
  });

  it("says what the tab does, and what Edit the pages does", () => {
    expect(model).toContain("The pages and the rail of your app. Save, then open the app to see them.");
    expect(model).toContain("Once you edit, a new view no longer joins a page by itself.");
    expect(tab).toContain('label: "Edit the pages"');
    expect(tab).toContain("materializePages");
  });

  it("shows only under the flag", () => {
    expect(page).toContain('const APP_FRAME = process.env.NEXT_PUBLIC_STUDIO_APP_FRAME === "1";');
    expect(page).toContain('...(APP_FRAME ? [{ id: "pages", label: "Pages", icon: "Layers" }] : [])');
    expect(page).toContain("buildOutline(manifest, { frame: APP_FRAME })");
    expect(page).toContain('{APP_FRAME && tab === "pages" ? <PagesTab');
    expect(page).toContain("manifest={manifest} onChange={editor.edit} locked={locked}");
    expect(tab).not.toContain("process.env");
  });

  it("keeps the page in focus in local state, not the URL", () => {
    expect(page).toContain("useState<string | null>(null)");
    expect(page).not.toMatch(/router\.replace\([^)]*focusPage/);
  });
});

// ── AF-8b-3b: the cells editor ──────────────────────────────────────────────
// Mutation 6: add `onChange(` or an `applyFrame` import to CellsEditor. "sends only through act" turns red.
// Mutation 7: call a bare `removeCell(` in CellsEditor. "removes a cell through the count guard" turns red.
// Mutation 8: drop `disabled={locked` from a CellsEditor control. "locks every control of the editor" turns red.
// Mutation 9: add `onKeyDown` to CellsEditor. "names each icon button" turns red.
const cells = read("components/CellsEditor.tsx");

describe("CellsEditor", () => {
  it("is drawn only from PagesTab", () => {
    expect(tab.match(/<CellsEditor\b/g)).toHaveLength(1);
    expect(page).not.toContain("CellsEditor");
  });

  it("sends only through act", () => {
    expect(cells).not.toMatch(/onChange\(|applyFrame/);
    expect(tab).not.toMatch(/onChange\(/);
    expect(tab).toContain("act={act}");
  });

  it("removes a cell through the count guard, and a tab only after the confirm", () => {
    expect(cells).toContain("removeCellGuarded(");
    expect(cells).not.toMatch(/\bremoveCell\(/);
    expect(cells).not.toMatch(/\bremoveTab\(/);
    expect(tab.match(/\bremoveTab\(/g)).toHaveLength(1);
    const confirm = tab.slice(tab.indexOf("onConfirm={"), tab.indexOf("onCancel={"));
    expect(confirm).toContain("removeTab(");
  });

  it("locks every control of the editor", () => {
    const controls = cells.match(/<(?:Button|SelectButton|CommitInput)\b[\s\S]*?(?:\/>|<\/Button>)/g) ?? [];
    expect(controls.length).toBeGreaterThanOrEqual(12);
    for (const c of controls) expect(c).toMatch(/disabled=\{locked/);
  });

  it("names each icon button, and has no key handler, network call or colour", () => {
    const icons = cells.match(/<Button[^>]*size="icon-sm"[\s\S]*?\/>/g) ?? [];
    expect(icons.length).toBeGreaterThanOrEqual(6);
    for (const b of icons) expect(b).toContain("aria-label=");
    expect(cells).not.toMatch(/onKeyDown|keydown|\bfetch\(|XMLHttpRequest/);
    expect(cells).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(cells).not.toMatch(/\[\d+px\]|\bpx-\[|(?:bg|text|border)-\[/);
  });

  it("uses shared controls only", () => {
    expect(cells).toContain("<CommitInput");
    expect(cells).not.toMatch(/<(button|select|input|textarea|dialog)[\s>]/);
    expect(cells).not.toMatch(/lucide-react|components\/ui\/Input/);
  });

  it("reads the caps from the model, not from a literal", () => {
    expect(cells).not.toMatch(/\b(?:12|8)\b/);
  });

  it("moves a cell only through moveCellArgs, with no index arithmetic (AF-8b-3b fix)", () => {
    expect(cells.match(/\bmoveCell\(/g)).toHaveLength(1);
    expect(cells).toContain("moveCellArgs(l.tabId, index, delta)");
    expect(cells).toContain("moveCell(t, pid, a.from, a.to)");
    expect(cells).not.toMatch(/moveCell\([^)]*index\s*[+-]/);
    expect(cells).not.toMatch(/where\([^)]*index\s*[+-]\s*\d/);
  });
});

// ── AF-11b-2: the drag layer ────────────────────────────────────────────────
// Mutation 10: send a drop with a second `act(` in `land`. "a drop is one act" turns red.
// Mutation 11: drop `disabled={locked` from a draggable control. "locks every drag source" turns red.
// Mutation 12: add `onTouchStart` or a second `moveCell(`. "keeps the buttons as the one path for keyboard and touch" turns red.
// Mutation 13: drop the `ring-2 ring-primary/40` highlight. "uses the highlight of a board column" turns red.
describe("CellsEditor drag layer", () => {
  it("a drop is one act, and the pure drop decides it", () => {
    const land = cells.slice(cells.indexOf("const land = "), cells.indexOf("const clearLine"));
    expect(land.match(/\bact\(/g)).toHaveLength(1);
    expect(land).toMatch(/act\(\(m\) => \{\s*const d = drop\(ds, m, pid, loc,/);
    expect(cells).not.toMatch(/\b(?:applyDrop|dropVerdict|canDrop)\(/);
    expect(cells).toContain('from "@/lib/studio/cellDrag"');
  });

  it("locks every drag source", () => {
    const sources = cells.match(/<Button\b(?:(?!\/>|<\/Button>)[\s\S])*\bdraggable\b(?:(?!\/>|<\/Button>)[\s\S])*(?:\/>|<\/Button>)/g) ?? [];
    expect(sources).toHaveLength(2);
    for (const s of sources) expect(s).toMatch(/disabled=\{locked/);
    expect(cells).toContain("if (locked)");
  });

  it("keeps the buttons as the one path for keyboard and touch", () => {
    expect(cells).toContain("Move to tab");
    expect(cells).not.toMatch(/onTouch|onPointer|setPointerCapture|touchstart/);
    expect(cells.match(/pointer-coarse:hidden/g)?.length ?? 0).toBeGreaterThanOrEqual(1);
    expect(cells.match(/\bmoveCell\(/g)).toHaveLength(1);
  });

  it("names each handle, and says each move through a status or alert line", () => {
    expect(cells).toContain("`Drag cell ${c.index + 1} in ${l.title}`");
    expect(cells).toContain('role="status"');
    expect(cells).toContain('role="alert"');
  });

  it("uses the highlight of a board column, with tokens only", () => {
    expect(cells).toContain("ring-2 ring-primary/40");
    expect(cells).toContain("border-t-primary");
    expect(cells).toContain("border-b-primary");
  });

  it("is passed the draft by PagesTab, and sends nothing with it", () => {
    expect(tab).toContain("manifest={manifest} act={act}");
    expect(cells).not.toMatch(/onChange\(|applyFrame/);
  });
});
