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
