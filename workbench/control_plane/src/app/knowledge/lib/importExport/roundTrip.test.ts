import { describe, expect, it } from "vitest";

import { collectSources } from "./collect";
import { buildFiles, layoutExport, type ExportNode } from "./exportPages";
import { buildPlan, toDraft } from "./plan";
import { runExport } from "./runExport";
import { runImport, type ImportWrites } from "./runImport";
import type { PageSummary } from "../types";
import { writeZip } from "./zip";

function summary(id: string, title: string, parent: string | null, position: number): PageSummary {
  return {
    id, space_id: "sp", parent_id: parent, position, title, state: "published", author_email: "a@x", reviewers: [],
    review_at: null, source_ref: null, published_at: null, archived_at: null, history_from: null,
    archived_from_state: null, updated_at: null, created_at: null,
  };
}

const PAGES = [
  summary("h", "Handbook", null, 0),
  summary("l", "Leave (2026) / policy", "h", 0),
  summary("c", "Casual leave", "l", 0),
  summary("n", "Notes", null, 1),
  summary("n2", "Notes", null, 2),
];
const BODIES: Record<string, string> = {
  h: "Welcome. See [leave](/knowledge/pages/l) and [casual](/knowledge/pages/c).\n\n## Parts\n\n- one\n- two",
  l: "Back to [the handbook](/knowledge/pages/h). Outside: [elsewhere](/knowledge/pages/other-space-page).",
  c: "```md\n[not a link](/knowledge/pages/h)\n```\n\nText with `code`.",
  n: "First notes.",
  n2: "",
};

describe("an export then an import", () => {
  it("gives the same titles, the same tree and the same bodies", async () => {
    const reads = {
      listPages: async () => PAGES,
      getPage: async (id: string) => ({ id, body_md: BODIES[id] }),
    };
    const out = await runExport({ spaceId: "sp", root: null, withSub: true, format: "md" }, reads, String);
    expect(out.failed).toEqual([]);
    expect(out.files.map((f) => f.path).sort()).toEqual([
      "Handbook.md",
      "Handbook/Leave (2026) - policy.md",
      "Handbook/Leave (2026) - policy/Casual leave.md",
      "Notes 2.md",
      "Notes.md",
    ]);

    const zip = await writeZip(out.files);
    const read = await collectSources([{ name: "export.zip", size: zip.length, read: async () => zip }]);
    const plan = buildPlan(read.entries.map((e) => toDraft(e, () => { throw new Error("md only"); })));

    const made = new Map<string, { id: string; parent: string | null; title: string; body: string }>();
    const writes: ImportWrites = {
      create: async (b) => {
        const id = `new-${made.size + 1}`;
        made.set(id, { id, parent: b.parent_id ?? null, title: b.title, body: b.body_md ?? "" });
        return { id };
      },
      patch: async (id, b) => {
        made.get(id)!.body = b.body_md;
      },
    };
    const report = await runImport(plan, { spaceId: "sp2", parentId: null }, writes);
    expect(report).toMatchObject({ created: 5, failed: [], linksRewritten: 3 });

    const byTitle = (t: string) => [...made.values()].filter((p) => p.title === t);
    const handbook = byTitle("Handbook")[0];
    const leave = byTitle("Leave (2026) / policy")[0]; // the title survives, the file name does not
    const casual = byTitle("Casual leave")[0];
    expect(handbook.parent).toBeNull();
    expect(leave.parent).toBe(handbook.id);
    expect(casual.parent).toBe(leave.id);
    expect(byTitle("Notes")).toHaveLength(2);

    // Bodies match once the old ids are mapped to the new ones.
    expect(handbook.body).toBe(
      BODIES.h.replace("/knowledge/pages/l", `/knowledge/pages/${leave.id}`).replace("/knowledge/pages/c", `/knowledge/pages/${casual.id}`),
    );
    expect(leave.body).toBe(BODIES.l.replace("/knowledge/pages/h", `/knowledge/pages/${handbook.id}`));
    expect(casual.body).toBe(BODIES.c);
    expect(byTitle("Notes").map((p) => p.body).sort()).toEqual(["", "First notes."]);
  });

  it("exports one page with no sub-pages as one file", async () => {
    const reads = { listPages: async () => PAGES, getPage: async (id: string) => ({ id, body_md: BODIES[id] }) };
    const out = await runExport(
      { spaceId: "sp", root: { id: "n", title: "Notes" }, withSub: true, format: "md" },
      reads,
      String,
    );
    expect(out.files).toEqual([{ path: "Notes.md", text: "# Notes\n\nFirst notes.\n" }]);
  });

  it("leaves out sub-pages when the choice is off", async () => {
    const reads = { listPages: async () => { throw new Error("no list needed"); }, getPage: async (id: string) => ({ id, body_md: BODIES[id] }) };
    const out = await runExport({ spaceId: "sp", root: { id: "h", title: "Handbook" }, withSub: false, format: "md" }, reads, String);
    expect(out.files.map((f) => f.path)).toEqual(["Handbook.md"]);
  });

  it("names a page that failed to read, and exports the rest", async () => {
    const reads = {
      listPages: async () => PAGES,
      getPage: async (id: string) => {
        if (id === "l") throw new Error("Not found");
        return { id, body_md: BODIES[id] };
      },
    };
    const out = await runExport({ spaceId: "sp", root: { id: "h", title: "Handbook" }, withSub: true, format: "md" }, reads, String);
    expect(out.failed).toEqual([{ title: "Leave (2026) / policy", reason: "Not found" }]);
    expect(out.files.map((f) => f.path).sort()).toEqual(["Handbook.md", "Handbook/Leave (2026) - policy/Casual leave.md"]);
    // The unused import keeps the type contract of the layout honest.
    const nodes: ExportNode[] = [];
    expect(buildFiles(layoutExport(nodes, "md"), new Map(), "md", String)).toEqual([]);
  });
});
