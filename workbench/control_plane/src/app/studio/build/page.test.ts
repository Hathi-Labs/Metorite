/**
 * STU-G3a fence: the Studio IDE route is gated, is no pane, and mounts no shell part of its own.
 *
 * Mutation 1: give `/studio/build` a nav entry in `nav.ts`. "is in no nav pane" turns red.
 * Mutation 2: move the route out of `/studio` or map it to no feature. "is gated by the feature of /studio" turns red.
 * Mutation 3: import `AgentChat`, `NotificationBell` or a palette into the page or its parts. "mounts no shell part" turns red.
 * Mutation 4: add a key handler to the page or its parts. "mounts no shell part" turns red.
 * Mutation 5: drop `useAssistantFacts` or `ideFacts` from the page. "publishes the package facts" turns red.
 * Mutation 6: make the page write: add `fetch(`, `PUT` or `POST` to it. "writes only through the one save" turns red.
 *
 * STU-G3c (direct edits) changed two fences and added the rest:
 * Mutation 7: import Monaco in the Code tab or in the Workshop page, not through `CodeEditor`. "has one editor wrapper" turns red.
 * Mutation 8: give the Code tab a `readOnly` option again. "draws an editable editor" turns red.
 * Mutation 9: give the facts the working copy, `editor.shown`, in place of the stored `draft.manifest`. "gives Mira the stored draft" turns red.
 * Mutation 10: drop `useLeaveGuard` from the hook. "asks before a member leaves" turns red.
 * Mutation 11: drop `UnsavedMark` from the bar or the Workshop page. "shares the unsaved mark" turns red.
 * Mutation 12: put a `PUT` in a tab or in the page. "writes only through the one save" turns red.
 * Fix round 1: add `addCommand(` to `CodeEditor`, drop `key={editorKey}`, use a bare `Input` for a label, or call `onRevert` with no question. The fences of P1-b to P2-a turn red.
 */
import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { featureForPath } from "@/lib/access";
import { PANES } from "@/lib/nav";

const here = new URL("./", import.meta.url);
const read = (rel: string) => readFileSync(new URL(rel, here), "utf8").replace(/\r\n/g, "\n");
const page = read("page.tsx");
const parts = readdirSync(new URL("./components/", here)).map((f) => read(`components/${f}`));
const all = [page, ...parts].join("\n");

describe("/studio/build", () => {
  it("is gated by the feature of /studio", () => {
    const studio = featureForPath("/studio");
    expect(studio).toBeTruthy();
    expect(featureForPath("/studio/build")).toBe(studio);
  });

  it("is in no nav pane", () => {
    expect(PANES.some((p) => p.href.startsWith("/studio/build"))).toBe(false);
  });

  it("mounts no shell part of its own", () => {
    expect(all).not.toMatch(/NotificationBell|AgentChat|AssistantRail|SearchPalette|CommandPalette|keydown|onKeyDown|isOpenShortcut/);
    expect(all).not.toMatch(/addEventListener\(\s*["']key/);
  });

  it("publishes the package facts, and opens the dock through the shell", () => {
    expect(page).toContain("useAssistantFacts(");
    expect(page).toContain("ideFacts(");
    expect(page).toContain("openAssistant()");
  });

  it("uses the app bar with a way back to Studio", () => {
    expect(page).toContain("<AppTopBar");
    expect(page).toContain('href: "/studio"');
  });

  it("shows the off state as an empty state, not an error", () => {
    expect(page).toContain("AI Studio may be off for your workspace, or the app is not yours.");
    expect(page).toContain("Go back to Studio");
    expect(page).toContain("watch.off");
  });

  it("writes only through the one save", () => {
    // No tab and no page calls the network. The one PUT is in `draftSave.ts`, to the draft route of the package.
    expect(all).not.toMatch(/\bfetch\(|method:\s*["'](?:POST|PUT|PATCH|DELETE)/);
    const lib = (f: string) => readFileSync(new URL(`../../../lib/studio/${f}`, here), "utf8");
    const save = lib("draftSave.ts");
    expect(save.match(/method:\s*["'](?:POST|PUT|PATCH|DELETE)["']/g)).toEqual(['method: "PUT"']);
    expect(save).toContain("/draft?based_on=");
    // The hook and the state module reach the network through it and nowhere else.
    expect(lib("useDraftEditor.ts")).not.toMatch(/\bfetch\(/);
    expect(lib("draftEditor.ts")).toContain("saveDraft(");
    expect(page).toContain("useDraftEditor(");
  });

  it("leaves Publish out: Mira publishes after the plan card", () => {
    expect(all).not.toMatch(/>\s*Publish\s*</);
  });

  it("has the four tabs and the three phone sections", () => {
    for (const label of ["Preview", "Data", "Views", "Code"]) expect(page).toContain(`label: "${label}"`);
    for (const label of ["Explorer", "Editor", "Problems"]) expect(page).toContain(`label: "${label}"`);
  });

  it("draws an editable editor for the Code tab", () => {
    const code = read("components/CodeTab.tsx");
    // Read only only while a save is in flight (fix round 1, P1-b), never by itself.
    expect(code).toContain("readOnly={readOnly}");
    expect(code).not.toContain("readOnly: true");
    expect(code).toContain("<CodeEditor");
    expect(code).toContain("onChange={onText}");
  });

  it("has one editor wrapper, used by the Studio IDE and by the App Workshop", () => {
    const wrapper = readFileSync(new URL("../../../components/CodeEditor.tsx", here), "utf8");
    expect(wrapper).toContain('import("@monaco-editor/react")');
    expect(wrapper).toContain("useMonacoTheme()");
    const workshop = readFileSync(new URL("../../build/apps/[slug]/edit/page.tsx", here), "utf8");
    for (const user of [read("components/CodeTab.tsx"), workshop]) {
      expect(user).toContain("<CodeEditor");
      expect(user).not.toContain("@monaco-editor/react");
      expect(user).not.toContain("useMonacoTheme");
    }
  });

  it("shares the unsaved mark with the App Workshop", () => {
    const workshop = readFileSync(new URL("../../build/apps/[slug]/edit/page.tsx", here), "utf8");
    expect(workshop).toContain("<UnsavedMark />");
    expect(read("components/EditBar.tsx")).toContain("<UnsavedMark label />");
  });

  it("gives Mira the stored draft and its hash, never the edit that is not saved", () => {
    expect(page).toMatch(/ideFacts\(\{ id: draft\.id, name: draft\.name \}, draft\.manifest, draft\.manifestHash\)/);
    expect(page).not.toMatch(/ideFacts\([^)]*editor\.shown/);
  });

  it("asks before a member leaves with unsaved edits", () => {
    const hook = readFileSync(new URL("../../../lib/studio/useDraftEditor.ts", here), "utf8");
    expect(hook).toContain("useLeaveGuard(dirty");
  });

  it("opens the Problems panel for a refusal of the server check", () => {
    expect(page).toContain("server.length > 0");
    expect(page).toContain("[...server, ...checkDraft(manifest)]");
  });

  it("hands the held brief to the shell only after the facts, and sends it as an ask", () => {
    expect(page).toContain("sendHeldBrief(");
    expect(page.indexOf("useAssistantFacts(")).toBeLessThan(page.indexOf("sendHeldBrief("));
    expect(page).toContain("openAssistant({ text, fresh: true })");
  });

  it("publishes no facts when the app is gone (404), so Mira never saves to a deleted app", () => {
    expect(page).toMatch(/useAssistantFacts\(\s*draft && !watch\.off/);
  });

  it("folds the app nav while it is open and gives it back on leave", () => {
    expect(page).toContain("useOptionalSidebarFold");
    expect(page).toContain("foldForWork()");
    expect(page).toContain("foldOnOpen(");
    expect(page).toContain("autoFoldEnabled()");
    expect(page).toContain("restoreOnLeave(");
    expect(page).toContain("dockFoldsNav(");
    expect(page).toMatch(/now\.toggle\(\)/);
    // A strict-mode remount must not give the nav back: the give-back waits one tick and the effect cancels it.
    expect(page).toMatch(/leaveTimer\.current = setTimeout/);
    expect(page).toMatch(/clearTimeout\(leaveTimer\.current\)/);
  });

  it("gives the phone Problems panel a toggle that works", () => {
    expect(page).not.toContain("onToggle={() => {}}");
    expect(page).toContain("phoneProblemsOpen");
  });

  it("reveals an Action, a Workflow or the Skill in the Code tab", () => {
    expect(page).toContain("reveal");
    expect(read("components/CodeTab.tsx")).toContain("findLine(");
    expect(read("components/CodeTab.tsx")).toContain("revealLineInCenter");
  });

  it("shows a pending state on the Build it buttons of the library", () => {
    const gallery = readFileSync(new URL("../components/AppsGallery.tsx", here), "utf8");
    expect(gallery).toContain("loading={building}");
  });

  // ── Fix round 1 ──
  it("binds the save key per editor, only when a caller asks, and disposes it (P1-d)", () => {
    const wrapper = readFileSync(new URL("../../../components/CodeEditor.tsx", here), "utf8");
    expect(wrapper).not.toContain("addCommand(");
    expect(wrapper).toContain("if (save.current)");
    expect(wrapper).toContain("ed.onKeyDown(");
    expect(wrapper).toContain("bound.current?.dispose()");
    // The Workshop passes no onSave, so its own document handler still gets Ctrl+S.
    const workshop = readFileSync(new URL("../../build/apps/[slug]/edit/page.tsx", here), "utf8");
    expect(workshop).not.toMatch(/<CodeEditor[^>]*onSave/);
    expect(workshop).toContain('(e.metaKey || e.ctrlKey) && e.key === "s"');
    expect(workshop).toContain("if (fileDirty) saveFile()");
  });

  it("reads the save key by the key code, with Ctrl or Cmd (P1-d)", async () => {
    const { isSaveKey } = await import("../../../components/CodeEditor");
    expect(isSaveKey({ ctrlKey: true, metaKey: false, keyCode: 49 }, 49)).toBe(true);
    expect(isSaveKey({ ctrlKey: false, metaKey: true, keyCode: 49 }, 49)).toBe(true);
    expect(isSaveKey({ ctrlKey: false, metaKey: false, keyCode: 49 }, 49)).toBe(false);
    expect(isSaveKey({ ctrlKey: true, metaKey: false, keyCode: 50 }, 49)).toBe(false);
  });

  it("keeps the label controls local until blur or Enter (P1-e)", () => {
    for (const f of ["DataTab.tsx", "ViewsTab.tsx"]) {
      const src = read("components/" + f);
      expect(src).toContain("<CommitInput");
      expect(src).not.toContain("onChange={(e) => onChange(updateField(manifest, name, key, { label");
      expect(src).not.toContain("onChange={(e) => onChange(updateView(manifest, id, { title");
      expect(src).not.toContain("onChange={(e) => onChange(renameObject(");
    }
    const commit = read("components/CommitInput.tsx");
    expect(commit).toContain("onBlur={commit}");
    expect(commit).toContain("onSubmit=");
  });

  it("locks the tabs and the Code editor while a save is in flight (P1-b)", () => {
    expect(page).toContain("const locked = editor.locked");
    expect(page).toContain("readOnly={editor.state.saving}");
  });

  it("asks before Revert and Reload drop an edit, and offers Copy my JSON (P2-a)", () => {
    const bar = read("components/EditBar.tsx");
    expect(bar).toContain("<ConfirmDialog");
    expect(bar).toContain("Copy my JSON");
    expect(bar).toContain('setAsk("revert")');
    expect(bar).not.toContain("onClick={onRevert}");
    expect(bar).not.toContain("onClick={onReload}");
  });

  it("keys the Code editor by the draft, so Undo cannot bring back older text (P1-c)", () => {
    expect(read("components/CodeTab.tsx")).toContain("key={editorKey}");
    expect(page).toContain("editorKey={editor.codeKey}");
    expect(page).toContain("text={editor.codeText}");
  });
});
