/**
 * The pure half of the directories (WS-61 KC-8b, `knowledge_curation.md` §6),
 * and the Directories group of the rail.
 *
 * The client holds no permission rule: the gateway lists only what the owner
 * app allows. The source fence below keeps it so.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseRecord } from "../../crm/lib/urlState";
import KnowledgeRail from "../components/KnowledgeRail";
import {
  ENTITY_TYPES,
  directoryHref,
  directoryTypeOf,
  entityByRef,
  listEntityTypes,
  makeEntityPage,
  ownerHref,
  pageWarning,
  typeLabel,
  type EntityType,
} from "./entities";

describe("the type labels", () => {
  it("names the seven types in the order of section 6.1", () => {
    expect(ENTITY_TYPES.map(typeLabel)).toEqual([
      "Company",
      "Products",
      "People",
      "Customers",
      "Workstreams",
      "Workflows",
      "Topics",
    ]);
  });
});

describe("the directory address", () => {
  it("is /knowledge/directories/<type>", () => {
    expect(directoryHref("customer")).toBe("/knowledge/directories/customer");
  });

  it("reads a good path back to its type, and a bad one to null", () => {
    for (const type of ENTITY_TYPES) expect(directoryTypeOf(directoryHref(type))).toBe(type);
    expect(directoryTypeOf("/knowledge/directories/customer/")).toBe("customer");
    expect(directoryTypeOf("/knowledge/directories/nothing")).toBeNull();
    expect(directoryTypeOf("/knowledge/directories")).toBeNull();
    expect(directoryTypeOf("/knowledge/pages/customer")).toBeNull();
    expect(directoryTypeOf("/knowledge/directories/customer/x")).toBeNull();
    expect(directoryTypeOf(null)).toBeNull();
  });
});

describe("the link to the owner record", () => {
  const row = (type: EntityType, ref_table: string, ref_id = "r-1") => ({ type, ref_table, ref_id });

  it("goes to the app that owns the record", () => {
    expect(ownerHref(row("person", "people"))).toBe("/people/r-1");
    expect(ownerHref(row("workstream", "pm_projects"))).toBe("/projects?project=r-1");
    expect(ownerHref(row("workflow", "workflows"))).toBe("/workflows/r-1");
  });

  it("opens a customer as the organization the CRM sheet reads", () => {
    const href = ownerHref(row("customer", "crm_companies", "c-9"));
    expect(href).toBe("/crm?organization=c-9");
    const query = new URL(href as string, "http://x.test").searchParams;
    expect(parseRecord(query)).toEqual({ param: "organization", entity: "organizations", id: "c-9" });
  });

  it("has none for a company, a product, a topic or a written workflow", () => {
    expect(ownerHref(row("company", "organization"))).toBeNull();
    expect(ownerHref(row("product", "kb_entities"))).toBeNull();
    expect(ownerHref(row("topic", "kb_entities"))).toBeNull();
    expect(ownerHref(row("workflow", "kb_entities"))).toBeNull();
  });
});

describe("the page warning", () => {
  it("is empty for company, product and topic", () => {
    for (const type of ["company", "product", "topic"] as const) expect(pageWarning(type)).toBe("");
  });

  it("names the space readers and the owner app for the other four", () => {
    const apps = { customer: "CRM", workstream: "Projects", person: "People", workflow: "Workflows" } as const;
    for (const [type, app] of Object.entries(apps)) {
      const text = pageWarning(type as EntityType);
      expect(text).toContain("Everyone who can read this space will see this page");
      expect(text).toContain(`can't open`);
      expect(text).toContain(`in ${app}.`);
    }
    expect(pageWarning("customer")).toBe(
      "Everyone who can read this space will see this page and the name of this customer — including people who can't open it in CRM.",
    );
  });

  it("uses no name that only we know", () => {
    for (const type of ENTITY_TYPES) expect(pageWarning(type)).not.toMatch(/KC-|Jev/);
  });
});

describe("the source fence", () => {
  it("holds no permission rule in the client", () => {
    for (const file of ["./entities.ts", "../components/DirectoryView.tsx", "../components/WritePageDialog.tsx"]) {
      const text = readFileSync(fileURLToPath(new URL(file, import.meta.url)), "utf8");
      const code = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
      expect(code, file).not.toContain("feature:");
      expect(code, file).not.toContain("has_permission");
      expect(code, file).not.toContain("hasCapability");
    }
  });
});

describe("the calls", () => {
  afterEach(() => vi.unstubAllGlobals());

  function fake(body: unknown) {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("keeps only the types it knows, in the order given", async () => {
    fake({ types: ["company", "mystery", "person"] });
    expect(await listEntityTypes()).toEqual(["company", "person"]);
  });

  it("posts the type and the record to by-ref", async () => {
    const f = fake({ id: "e-1" });
    await entityByRef("person", "p-1");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/knowledge/entities/by-ref");
    expect(JSON.parse(init.body as string)).toEqual({ type: "person", ref_id: "p-1" });
  });

  it("sends a space only when one is given", async () => {
    const f = fake({ page: { id: "pg" } });
    await makeEntityPage("e-1");
    await makeEntityPage("e-1", "sp-2");
    const bodies = f.mock.calls.map((c) => JSON.parse((c as unknown as [string, RequestInit])[1].body as string));
    expect(bodies).toEqual([{}, { space_id: "sp-2" }]);
  });
});

describe("the Directories group of the rail", () => {
  const base = {
    spaces: [],
    selectedSpaceId: null,
    selectedPageId: null,
    atHome: false,
    atSearch: false,
    onNavigate: () => undefined,
  };
  const draw = (directories?: { types: readonly EntityType[]; selected: EntityType | null }) =>
    renderToStaticMarkup(createElement(KnowledgeRail, { ...base, directories }));

  it("draws no group with no prop, and none with no type", () => {
    expect(draw()).not.toContain("Directories");
    expect(draw({ types: [], selected: null })).not.toContain("Directories");
  });

  it("draws one row for each type given, in the order given", () => {
    const html = draw({ types: ["company", "person"], selected: null });
    expect(html).toContain("Directories");
    const rows = [...html.matchAll(/data-directory-type="([a-z]+)"/g)].map((m) => m[1]);
    expect(rows).toEqual(["company", "person"]);
    expect(html.indexOf("Company")).toBeLessThan(html.indexOf("People"));
    expect(html).not.toContain("Customers");
  });

  it("marks the open directory with aria-current", () => {
    const html = draw({ types: ["company", "customer"], selected: "customer" });
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
    expect(html).toMatch(/aria-current="page"[^>]*>[\s\S]*?Customers/);
  });
});
