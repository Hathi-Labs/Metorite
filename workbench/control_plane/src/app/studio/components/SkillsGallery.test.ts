// The Skills section reads the list as a state (STU-F2b). A 404, a 401 and a
// miss of the network keep the present empty state, with no button and no
// error. Mutation: show an error on a 404, and the first test fails.
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadSkillsView, SKILLS_OFF_COPY, viewOfList } from "@/lib/studio/skillsApi";

afterEach(() => vi.unstubAllGlobals());

function answer(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

describe("the Skills list", () => {
  it("is off on a 404, with no error", async () => {
    answer(404, { detail: "Not found" });
    expect(await loadSkillsView()).toEqual({ kind: "off" });
  });

  it("is off on a 401", async () => {
    answer(401, { error: "Sign in to continue" });
    expect(await loadSkillsView()).toEqual({ kind: "off" });
  });

  it("is off on a 403", async () => {
    answer(403, { detail: "No" });
    expect(await loadSkillsView()).toEqual({ kind: "off" });
  });

  it("is an error, with a retry, on a network miss (status 0)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("fetch failed");
    }));
    expect(await loadSkillsView()).toEqual({ kind: "error", detail: "The skills did not load. Try again." });
  });

  it("lists the rows on a 200", async () => {
    const skill = { id: "s1", name: "A", state: "draft", current_version: 1 };
    answer(200, { skills: [skill] });
    expect(await loadSkillsView()).toEqual({ kind: "ok", skills: [skill] });
  });

  it("is ok and empty on a 200 with no rows, which shows the button", async () => {
    answer(200, { skills: [] });
    expect(await loadSkillsView()).toEqual({ kind: "ok", skills: [] });
  });

  it("is an error, with a retry, on a 500", async () => {
    answer(500, { detail: "boom" });
    expect(await loadSkillsView()).toEqual({ kind: "error", detail: "The skills did not load. Try again." });
    expect(viewOfList({ ok: false, status: 502, detail: "x" }).kind).toBe("error");
  });
});

describe("the off state copy (D179)", () => {
  it("says in plain words that skills are not open for the org yet, and what happens next", () => {
    expect(SKILLS_OFF_COPY.message).toMatch(/not open for your organization yet/i);
    expect(SKILLS_OFF_COPY.hint).toMatch(/turns them on/i);
    expect(`${SKILLS_OFF_COPY.message} ${SKILLS_OFF_COPY.hint}`).not.toMatch(/404|error|not found|undefined/i);
  });

  it("the gallery draws that copy for the off view", () => {
    // Static check: the off branch reads SKILLS_OFF_COPY and keeps no copy of its own.
    const src = readFileSync("src/app/studio/components/SkillsGallery.tsx", "utf-8");
    expect(src).toContain("SKILLS_OFF_COPY.message");
    expect(src).not.toContain("arrive with the skill creator");
  });
});
