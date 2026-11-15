/** STU-AF9b fence: the three ready apps count My and Open from their own views (D219, `ai_studio.md` §5.5). */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { ViewDef } from "./manifest";
import type { PageModel } from "./pageModel";
import { countSourceFor, sourceWithMe } from "./railCounts";

const DIR = resolve(process.cwd(), "../../packages/acb_common/acb_common/studio_templates");
const FILES = { "field_visits": "engineer", "support_desk": "assignee", "purchase_requests": "requester" } as const;

type Doc = {
  id: string;
  objects: { name: string; fields: { key: string; type: string }[] }[];
  actions: { id: string; uses: string }[];
  views: ViewDef[];
  pages: { id: string; title: string; cols: number; cells: { view: string; span?: number }[]; side?: { view: string } }[];
  nav: { rows: { page: string; count?: boolean }[] }[];
};

describe.each(Object.entries(FILES))("%s rail counts", (file, person) => {
  const doc = JSON.parse(readFileSync(resolve(DIR, `${file}.json`), "utf-8")) as Doc;
  const rows = doc.nav.flatMap((s) => s.rows);
  const model = (id: string): PageModel => {
    const p = doc.pages.find((x) => x.id === id)!;
    return { id: p.id, title: p.title, cols: p.cols, tabs: [], cells: p.cells.map((c) => ({ view: c.view, span: c.span ?? 1 })), side: p.side?.view ?? null };
  };
  const obj = doc.objects[0];

  it("counts exactly My and Open, and each count source keeps its filter", () => {
    expect(rows.filter((r) => r.count).map((r) => r.page)).toEqual(["mine", "open"]);
    const mine = countSourceFor(model("mine"), doc.views, doc.objects, doc.actions);
    expect(mine).toEqual({ action: `object:${obj.name}.list`,
      args: { limit: 1, filter: [{ field: person, op: "eq", value: "me" }] } });
    const open = countSourceFor(model("open"), doc.views, doc.objects, doc.actions);
    // The Open filter hides each closed stage and nothing else (purchase requests: rejected and ordered are closed).
    const OPEN_FILTER: Record<string, unknown> = {
      "field-visits": [{ field: "status", op: "ne", value: "done" }],
      "support-desk": [{ field: "status", op: "ne", value: "solved" }],
      "purchase-requests": [{ field: "status", op: "in", value: ["draft", "submitted", "approved"] }],
    };
    expect(open?.args?.filter).toEqual(OPEN_FILTER[doc.id]);
    expect(countSourceFor(model("dashboard"), doc.views, doc.objects, doc.actions)).toBeNull();
  });

  it("turns the me token into the email on the person field", () => {
    const mine = countSourceFor(model("mine"), doc.views, doc.objects, doc.actions)!;
    const out = sourceWithMe(mine, "A.Person@Example.com", obj);
    expect(out?.args?.filter).toEqual([{ field: person, op: "eq", value: "a.person@example.com" }]);
    expect(sourceWithMe(mine, null, obj)).toBeNull();
  });
});
