/**
 * Projects · the sidebar's own destinations, and the one that is flagged.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §4.3 and §10.1 done-when 4.
 *
 * The flag changes ONE word on ONE entry. Everything else about the list is
 * held equal, because a flag that quietly reordered or renamed a sibling is
 * the kind of drift nobody notices until a screenshot.
 */
import { describe, expect, it } from "vitest";

import {
  PROJECT_APP_SECTIONS,
  chatEnabled,
  projectAppSections,
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
