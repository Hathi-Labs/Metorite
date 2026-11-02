// The Skills section reads the list as a state (STU-F2b). A 404, a 401 and a
// miss of the network keep the present empty state, with no button and no
// error. Mutation: show an error on a 404, and the first test fails.
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadSkillsView, viewOfList } from "@/lib/studio/skillsApi";

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

  it("is off on a network miss", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("fetch failed");
    }));
    expect(await loadSkillsView()).toEqual({ kind: "off" });
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
    expect(await loadSkillsView()).toEqual({ kind: "error", detail: "boom" });
    expect(viewOfList({ ok: false, status: 502, detail: "x" }).kind).toBe("error");
  });
});
