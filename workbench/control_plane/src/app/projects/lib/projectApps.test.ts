/**
 * Projects · the sidebar's own destinations, and the one that is flagged.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §4.3 and §10.1 done-when 4.
 *
 * The flag changes ONE word on ONE entry. Everything else about the list is
 * held equal, because a flag that quietly reordered or renamed a sibling is
 * the kind of drift nobody notices until a screenshot.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  PROJECT_APP_SECTIONS,
  chatEnabled,
  projectAppSections,
  resolveAppLink,
} from "./projectApps";

const OFF = { NEXT_PUBLIC_PROJECTS_CHAT: "0" };
const ON = { NEXT_PUBLIC_PROJECTS_CHAT: "1" };

function chatItem(env: Record<string, string | undefined>) {
  return projectAppSections(env)
    .flatMap((s) => s.items)
    .find((i) => i.id === "ai-chat");
}

describe("chatEnabled", () => {
  it("is off by default and on only for the three spellings", () => {
    expect(chatEnabled({})).toBe(false);
    expect(chatEnabled({ NEXT_PUBLIC_PROJECTS_CHAT: "" })).toBe(false);
    expect(chatEnabled({ NEXT_PUBLIC_PROJECTS_CHAT: "0" })).toBe(false);
    expect(chatEnabled({ NEXT_PUBLIC_PROJECTS_CHAT: "false" })).toBe(false);
    expect(chatEnabled({ NEXT_PUBLIC_PROJECTS_CHAT: "1" })).toBe(true);
    expect(chatEnabled({ NEXT_PUBLIC_PROJECTS_CHAT: "true" })).toBe(true);
    expect(chatEnabled({ NEXT_PUBLIC_PROJECTS_CHAT: "on" })).toBe(true);
  });
});

describe("the flag reaches the browser", () => {
  it("reads the flag through the literal member expression Next inlines", () => {
    // `env.NEXT_PUBLIC_X` off a defaulted `env = process.env` is NOT inlined
    // and reads undefined in a browser. The literal is the only safe form,
    // and a refactor that "simplifies" it to the default-parameter shape
    // turns the whole feature off with every test still green.
    const source = fs.readFileSync(path.join(__dirname, "projectApps.ts"), "utf8");
    expect(source).toContain("process.env.NEXT_PUBLIC_PROJECTS_CHAT");
    expect(source).not.toMatch(/env: Record<string, string \| undefined> = process\.env/);
  });
});

describe("the AI chat slot", () => {
  it("stays preview when the flag is off", () => {
    expect(chatItem(OFF)?.launch).toBe("preview");
  });

  it("goes live when the flag is on", () => {
    expect(chatItem(ON)?.launch).toBe("live");
  });

  it("is preview in the shape of record", () => {
    // The base list ships dark. A build with no env at all shows the entry
    // disabled and honest, never a button that does nothing.
    const base = PROJECT_APP_SECTIONS.flatMap((s) => s.items).find(
      (i) => i.id === "ai-chat",
    );
    expect(base?.launch).toBe("preview");
  });

  it("changes nothing else about the list", () => {
    const off = projectAppSections(OFF);
    const on = projectAppSections(ON);
    expect(on.map((s) => s.id)).toEqual(off.map((s) => s.id));
    const strip = (sections: typeof off) =>
      sections.flatMap((s) => s.items).map(({ launch: _launch, ...rest }) => rest);
    expect(strip(on)).toEqual(strip(off));
    const others = (sections: typeof off) =>
      sections.flatMap((s) => s.items).filter((i) => i.id !== "ai-chat").map((i) => i.launch);
    expect(others(on)).toEqual(others(off));
  });

  it("does not mutate the shape of record", () => {
    projectAppSections(ON);
    const base = PROJECT_APP_SECTIONS.flatMap((s) => s.items).find(
      (i) => i.id === "ai-chat",
    );
    expect(base?.launch).toBe("preview");
  });
});

describe("WS-27bn R5f: one Reports app", () => {
  const items = PROJECT_APP_SECTIONS.flatMap((s) => s.items);

  it("(a) holds no analytics entry, and Reports is live", () => {
    expect(items.map((i) => i.id as string)).not.toContain("analytics");
    const reports = items.find((i) => i.id === "reports");
    expect(reports?.launch).toBe("live");
    expect(reports?.icon).toBe("FileText");
  });

  it("(b) resolveAppLink sends analytics and reports to reports", () => {
    expect(resolveAppLink("analytics")).toBe("reports");
    expect(resolveAppLink("reports")).toBe("reports");
    expect(resolveAppLink(" Analytics ")).toBe("reports");
  });

  it("(b) resolveAppLink refuses a preview id and an unknown id", () => {
    // The chat slot ships preview, so the shape of record refuses it.
    expect(resolveAppLink("ai-chat")).toBeNull();
    expect(resolveAppLink("../admin")).toBeNull();
    expect(resolveAppLink("")).toBeNull();
    expect(resolveAppLink(null)).toBeNull();
  });

  it("(b) a flagged entry opens when the page passes the resolved list", () => {
    expect(resolveAppLink("ai-chat", projectAppSections(ON))).toBe("ai-chat");
    expect(resolveAppLink("analytics", projectAppSections(ON))).toBe("reports");
  });

  it("the page calls the one map for the link and for the chat", () => {
    const page = fs.readFileSync(path.join(__dirname, "..", "page.tsx"), "utf8");
    expect(page.match(/resolveAppLink\(/g)?.length).toBe(2);
    // A second map would find the entry by hand again.
    expect(page).not.toMatch(/items\)\.find\(\s*\(i\) => i\.id === (appLink|id) &&/);
  });
});
