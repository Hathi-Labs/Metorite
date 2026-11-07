// WS-MCP SW-31b — Settings → Agents → Guidance, as pure decisions.
//
// Spec: `project-docs/specs/agent_coordination.md` SW-31.
//
// vitest runs in the node environment here, so this file holds the pure
// helpers of `guidance.ts`, draws the History list with
// `renderToStaticMarkup`, and reads the page source for its wiring.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { buildProjectTree } from "@/lib/accessTree";
import { isKnownIcon } from "@/lib/icons";

import { GuidanceHistory } from "./GuidanceHistory";
import {
  GUIDANCE_MAX,
  HOW_IT_REACHES,
  ORG_SCOPE,
  counter,
  historyKey,
  isTurnedOff,
  projectIdOf,
  saveFailure,
  scopeOptions,
  versionSummary,
  writeRefusal,
  writtenWhen,
  type GuidanceVersion,
} from "./guidance";

const HERE = join(__dirname);
const read = (name: string) => readFileSync(join(HERE, name), "utf-8");

const TREE = buildProjectTree({
  rows: [
    {
      id: "s1",
      name: "Engineering",
      children: [
        { id: "p1", name: "Gateway", children: [] },
        { id: "p2", name: "Old site", archived_at: "2026-01-01T00:00:00Z", children: [] },
      ],
    },
  ],
  personal: [{ id: "me1", name: "My work", children: [] }],
});

describe("the scope picker", () => {
  it("starts with the whole organization, then the tree in order", () => {
    const opts = scopeOptions(TREE);
    expect(opts.map((o) => o.value)).toEqual([ORG_SCOPE, "s1", "p1", "p2"]);
    expect(opts[0]).toMatchObject({ label: "Whole organization", projectId: null });
  });

  it("indents by depth and marks an archived project", () => {
    const opts = scopeOptions(TREE);
    expect(opts.map((o) => o.depth)).toEqual([0, 1, 2, 2]);
    expect(opts[1].label).toBe("Engineering");
    expect(opts[3]).toMatchObject({ archived: true, label: "Old site (archived)" });
  });

  it("leaves the personal tree out, because guidance refuses it", () => {
    expect(scopeOptions(TREE).some((o) => o.value === "me1")).toBe(false);
  });

  it("still offers the whole organization when the tree did not load", () => {
    expect(scopeOptions(undefined).map((o) => o.value)).toEqual([ORG_SCOPE]);
  });

  it("maps the org value to a null project", () => {
    expect(projectIdOf(ORG_SCOPE)).toBeNull();
    expect(projectIdOf("p1")).toBe("p1");
  });
});

describe("the counter", () => {
  it("counts against 8,000", () => {
    expect(GUIDANCE_MAX).toBe(8000);
    expect(counter("abc")).toEqual({ used: 3, over: false, label: "3 of 8,000 characters" });
    expect(counter("x".repeat(8000)).over).toBe(false);
  });

  it("says how far over the limit the text is", () => {
    expect(counter("x".repeat(8012))).toEqual({
      used: 8012,
      over: true,
      label: "12 characters over the limit of 8,000",
    });
  });
});

describe("the summary of a version", () => {
  it("is the first line with text, without a heading mark", () => {
    expect(versionSummary("\n\n## Tests first\nRun them.")).toBe("Tests first");
    expect(versionSummary("- Keep PRs small")).toBe("Keep PRs small");
  });

  it("cuts a long line", () => {
    const s = versionSummary("a".repeat(200));
    expect(s.length).toBe(80);
    expect(s.endsWith("…")).toBe(true);
  });

  it("names a cleared scope", () => {
    expect(versionSummary("   \n ")).toBe("Cleared (no text)");
  });
});

describe("why a member cannot save", () => {
  it("the org level needs the admin capability", () => {
    expect(writeRefusal({ projectId: null, canManageOrg: true })).toBeNull();
    expect(writeRefusal({ projectId: null, canManageOrg: false })).toMatch(/organization admin/);
  });

  it("a project waits for its facts, and shows no refusal while it waits", () => {
    expect(writeRefusal({ projectId: "p1", canManageOrg: false })).toBeNull();
  });

  it("a project needs the write floor of a lead", () => {
    expect(writeRefusal({ projectId: "p1", canManageOrg: true, node: { can_write_grants: true } })).toBeNull();
    expect(writeRefusal({ projectId: "p1", canManageOrg: true, node: { can_write_grants: false } })).toMatch(
      /project lead or a Projects admin/,
    );
  });

  it("an archived project cannot change", () => {
    expect(
      writeRefusal({ projectId: "p2", canManageOrg: true, node: { archived_at: "2026-01-01", can_write_grants: true } }),
    ).toMatch(/archived/);
    expect(writeRefusal({ projectId: "p2", canManageOrg: true, node: { status: "archived" } })).toMatch(/archived/);
  });
});

describe("the dark router and the words of a failed save", () => {
  it("tells the dark 404 from a hidden project", () => {
    expect(isTurnedOff(404, "Not Found")).toBe(true);
    expect(isTurnedOff(404, "Project not found")).toBe(false);
    expect(isTurnedOff(403, "Not Found")).toBe(false);
  });

  it("keeps the gateway's reason of a refusal", () => {
    expect(saveFailure(403, "Only an organization admin can change it.")).toBe(
      "Only an organization admin can change it.",
    );
    expect(saveFailure(409, "This project is archived, so its agent guidance cannot change.")).toMatch(/archived/);
  });

  it("says what to do, and never shows a raw status", () => {
    for (const status of [0, 404, 422, 500, 502]) {
      const words = saveFailure(status, null);
      expect(words).not.toMatch(/\b(?:HTTP|[45]\d\d)\b/);
      expect(words.length).toBeGreaterThan(20);
    }
    expect(saveFailure(404, "Not Found")).toMatch(/not turned on/);
    expect(saveFailure(422, null)).toMatch(/8,000/);
  });
});

describe("dates and keys", () => {
  it("writes a date that reads the same in India and in the US", () => {
    expect(writtenWhen("2026-11-07T09:05:00Z")).toMatch(/Nov 2026/);
    expect(writtenWhen(null)).toBe("");
    expect(writtenWhen("not a date")).toBe("");
  });

  it("keys each scope's history apart", () => {
    expect(historyKey(null)).toBe("agent-guidance/history");
    expect(historyKey("p1")).toBe("agent-guidance/history?project_id=p1");
  });

  it("the help says the nearest block wins and the tools never change", () => {
    expect(HOW_IT_REACHES).toMatch(/nearest block wins/);
    expect(HOW_IT_REACHES).toMatch(/never changes which tools/);
  });
});

describe("the History list", () => {
  const rows: GuidanceVersion[] = [
    { id: "v2", project_id: null, version: 2, body: "# Ship small\nAlways.", written_by: "a@x.example", written_at: "2026-11-07T09:05:00Z" },
    { id: "v1", project_id: null, version: 1, body: "Old rule", written_by: "b@x.example", written_at: "2026-11-01T09:05:00Z" },
  ];
  const draw = (props: Partial<Parameters<typeof GuidanceHistory>[0]>) =>
    renderToStaticMarkup(createElement(GuidanceHistory, { rows, openId: null, onOpen: () => {}, ...props }));

  it("shows the version, who and the first line", () => {
    const html = draw({});
    expect(html).toContain("Version 2");
    expect(html).toContain("a@x.example");
    expect(html).toContain("Ship small");
  });

  it("opens one version read-only, with Use this text for a writer only", () => {
    expect(draw({ openId: "v1", onUse: () => {} })).toContain("Use this text");
    const reader = draw({ openId: "v1" });
    expect(reader).toContain("read-only");
    expect(reader).not.toContain("Use this text");
  });

  it("says what an empty history means", () => {
    expect(draw({ rows: [] })).toMatch(/No versions yet/);
  });
});

describe("the page's wiring", () => {
  const page = read("page.tsx");
  const history = read("GuidanceHistory.tsx");

  it("reads through the one cache and draws a skeleton on a miss", () => {
    expect(page).toMatch(/useCachedResource\(/);
    expect(page).toMatch(/<Skeleton\b/);
  });

  it("reuses the one tree read of the consent screen", () => {
    expect(page).toMatch(/fetch\("\/api\/projects\/tree"/);
    expect(page).toMatch(/buildProjectTree\(/);
  });

  it("sends each request with a literal path, so the parity fence can read it", () => {
    expect(page).toMatch(/fetch\("\/api\/agent-guidance", \{/);
    expect(page).toMatch(/fetch\(`\/api\/agent-guidance\/history\?/);
  });

  it("names only icons that exist", () => {
    for (const src of [page, history]) {
      for (const [, name] of src.matchAll(/\b(?:name|icon)="([A-Z]\w+)"/g)) {
        expect(isKnownIcon(name), name).toBe(true);
      }
    }
  });
});
