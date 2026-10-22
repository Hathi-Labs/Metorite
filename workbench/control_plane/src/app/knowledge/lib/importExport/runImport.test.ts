import { describe, expect, it } from "vitest";

import { buildPlan, type PageDraft } from "./plan";
import { runImport, type ImportWrites } from "./runImport";

const draft = (path: string, title: string, body = ""): PageDraft => ({ path, title, body, images: 0 });

function fakeWrites(fail: (title: string) => boolean = () => false) {
  const created: Array<Record<string, unknown>> = [];
  const patches: Array<{ id: string; body: { body_md: string } }> = [];
  const writes: ImportWrites = {
    create: async (body) => {
      if (fail(body.title)) throw new Error(`No write for ${body.title}`);
      created.push(body);
      return { id: `id-${created.length}` };
    },
    patch: async (id, body) => {
      patches.push({ id, body });
    },
  };
  return { writes, created, patches };
}

describe("runImport", () => {
  it("creates parents first, under the chosen parent, in the chosen space", async () => {
    const plan = buildPlan([draft("Top/child.md", "Child", "text"), draft("Top.md", "Top", "# not")]);
    const { writes, created } = fakeWrites();
    const report = await runImport(plan, { spaceId: "sp", parentId: "root" }, writes);
    expect(created).toEqual([
      { space_id: "sp", parent_id: "root", title: "Top", body_md: "# not" },
      { space_id: "sp", parent_id: "id-1", title: "Child", body_md: "text" },
    ]);
    expect(report).toMatchObject({ created: 2, failed: [], cancelled: false, firstId: "id-1" });
  });
  it("puts a page at the space root when no parent is chosen, and omits an empty body", async () => {
    const { writes, created } = fakeWrites();
    await runImport(buildPlan([draft("a.md", "A")]), { spaceId: "sp", parentId: null }, writes);
    expect(created).toEqual([{ space_id: "sp", title: "A" }]);
  });
  it("skips the children of a page that failed, and the others go on", async () => {
    const plan = buildPlan([draft("P/c1.md", "C1"), draft("P/d/c2.md", "C2"), draft("P.md", "P"), draft("ok.md", "OK")]);
    const { writes, created } = fakeWrites((t) => t === "P");
    const report = await runImport(plan, { spaceId: "sp", parentId: null }, writes);
    expect(created.map((c) => c.title)).toEqual(["OK"]);
    expect(report.failed).toEqual([{ title: "P", reason: "No write for P" }]);
    expect(report.skippedChildren.map((s) => s.title).sort()).toEqual(["C1", "C2", "d"]);
    expect(report.created).toBe(1);
  });
  it("reports progress, and stops after the current page on cancel", async () => {
    const plan = buildPlan([draft("a.md", "A"), draft("b.md", "B"), draft("c.md", "C")]);
    const { writes, created } = fakeWrites();
    const seen: string[] = [];
    let stop = false;
    const report = await runImport(plan, { spaceId: "sp", parentId: null }, writes, {
      onProgress: (done, total) => {
        seen.push(`${done} of ${total}`);
        if (done === 1) stop = true;
      },
      shouldCancel: () => stop,
    });
    expect(seen).toEqual(["0 of 3", "1 of 3"]);
    expect(created).toHaveLength(1);
    expect(report).toMatchObject({ cancelled: true, notStarted: 2, created: 1 });
  });
  it("rewrites links in a second pass with a PATCH", async () => {
    const plan = buildPlan([
      draft("Home.md", "Home", "See [sub](Home/Sub%20page.md) and [web](https://x.example)."),
      draft("Home/Sub page.md", "Sub page", "Back to [home](../Home.md)."),
      draft("Plain.md", "Plain", "No links."),
    ]);
    const { writes, patches } = fakeWrites();
    const report = await runImport(plan, { spaceId: "sp", parentId: null }, writes);
    expect(patches).toEqual([
      { id: "id-1", body: { body_md: "See [sub](/knowledge/pages/id-3) and [web](https://x.example)." } },
      { id: "id-3", body: { body_md: "Back to [home](/knowledge/pages/id-1)." } },
    ]);
    expect(report.linksRewritten).toBe(2);
  });
  it("counts a failed link write and goes on", async () => {
    const plan = buildPlan([draft("A.md", "A", "[b](B.md)"), draft("B.md", "B", "[a](A.md)")]);
    const { writes } = fakeWrites();
    const bad: ImportWrites = { ...writes, patch: async () => { throw new Error("no"); } };
    const report = await runImport(plan, { spaceId: "sp", parentId: null }, bad);
    expect(report).toMatchObject({ created: 2, linkFailures: 2, linksRewritten: 0 });
  });
});
