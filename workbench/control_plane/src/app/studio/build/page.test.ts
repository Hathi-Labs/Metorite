/**
 * STU-G3a fence: the Studio IDE route is gated, is no pane, and mounts no shell part of its own.
 *
 * Mutation 1: give `/studio/build` a nav entry in `nav.ts`. "is in no nav pane" turns red.
 * Mutation 2: move the route out of `/studio` or map it to no feature. "is gated by the feature of /studio" turns red.
 * Mutation 3: import `AgentChat`, `NotificationBell` or a palette into the page or its parts. "mounts no shell part" turns red.
 * Mutation 4: add a key handler to the page or its parts. "mounts no shell part" turns red.
 * Mutation 5: drop `useAssistantFacts` or `ideFacts` from the page. "publishes the package facts" turns red.
 * Mutation 6: make the page write: add `fetch(`, `PUT` or `POST` to it. "writes nothing" turns red.
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

  it("writes nothing", () => {
    expect(all).not.toMatch(/\bfetch\(|method:\s*["'](?:POST|PUT|PATCH|DELETE)/);
  });

  it("leaves Publish out: Mira publishes after the plan card", () => {
    expect(all).not.toMatch(/>\s*Publish\s*</);
  });

  it("has the four tabs and the three phone sections", () => {
    for (const label of ["Preview", "Data", "Views", "Code"]) expect(page).toContain(`label: "${label}"`);
    for (const label of ["Explorer", "Editor", "Problems"]) expect(page).toContain(`label: "${label}"`);
  });

  it("draws a read only editor for the Code tab", () => {
    const code = read("components/CodeTab.tsx");
    expect(code).toContain("readOnly: true");
    expect(code).toContain("useMonacoTheme");
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
});
