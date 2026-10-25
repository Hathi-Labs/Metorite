/**
 * WS-61 KC-8d: the Add a product / Add a topic form (`knowledge_curation.md`
 * §6). Each case is written so the obvious wrong build fails it.
 *
 *   - The client sends the body the route reads, and a 403 or 404 reads as
 *     one plain line.
 *   - The form offers the spaces the member can write in, and never the "My
 *     notes" choice with no id (the route needs a real `space_id`).
 *   - The acts (the rail `+`, the directory buttons) are absent for a member
 *     with no writable space.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AddEntityForm } from "../components/AddEntityDialog";
import KnowledgeRail from "../components/KnowledgeRail";
import { CANNOT_ADD_LINE, createEntity, type EntityType } from "./entities";
import {
  NAME_MAX,
  canAddEntity,
  defaultEntitySpace,
  entitySpaces,
  sameNameIn,
  splitAliases,
} from "./entityForm";
import type { Space, SpaceKind, SpaceLevel } from "./types";

function space(id: string, level: SpaceLevel, kind: SpaceKind = "team", extra: Partial<Space> = {}): Space {
  return {
    id,
    slug: id,
    name: `Space ${id}`,
    purpose: "",
    icon: "",
    kind,
    owner_email: null,
    archived_at: null,
    created_at: null,
    level,
    page_count: 0,
    ...extra,
  };
}

const read = [space("r1", "read", "company")];
const mixed = [space("co", "read", "company"), space("t1", "write"), space("t2", "manage"), space("t3", "read")];

describe("the spaces the form offers", () => {
  it("are those the member can write in, and no 'My notes' choice with no id", () => {
    expect(entitySpaces(mixed).map((c) => c.value)).toEqual(["t1", "t2"]);
    expect(entitySpaces([])).toEqual([]);
    expect(entitySpaces([space("gone", "write", "team", { archived_at: "2026-01-01" })])).toEqual([]);
  });

  it("start on the company space when it is writable, else the first writable one", () => {
    expect(defaultEntitySpace([space("co", "write", "company"), space("t1", "write")])).toBe("co");
    expect(defaultEntitySpace(mixed)).toBe("t1");
    expect(defaultEntitySpace(read)).toBe("");
  });

  it("make canAddEntity false for a member with no writable space", () => {
    expect(canAddEntity(read)).toBe(false);
    expect(canAddEntity([])).toBe(false);
    expect(canAddEntity(mixed)).toBe(true);
  });
});

describe("the alias words", () => {
  it("split on commas, trim, and keep each once whatever the case", () => {
    expect(splitAliases(" Sapphire ,  sapphire, S  1 ,,")).toEqual(["Sapphire", "S 1"]);
    expect(splitAliases("")).toEqual([]);
  });

  it("finds a taken name or alias, whatever the case", () => {
    const rows = [{ name: "Sapphire Pro", aliases: ["SP"] }];
    expect(sameNameIn(rows, "  sapphire   pro ")?.name).toBe("Sapphire Pro");
    expect(sameNameIn(rows, "sp")?.name).toBe("Sapphire Pro");
    expect(sameNameIn(rows, "Other")).toBeNull();
    expect(sameNameIn(rows, "   ")).toBeNull();
  });
});

describe("createEntity", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the type, name, aliases and space to /entities", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ id: "e-1", type: "product" }), { status: 201 }));
    vi.stubGlobal("fetch", f);
    const made = await createEntity({ type: "product", name: "Sapphire", aliases: ["SP"], space_id: "t1" });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/knowledge/entities");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      type: "product",
      name: "Sapphire",
      aliases: ["SP"],
      space_id: "t1",
    });
    expect(made.id).toBe("e-1");
  });

  it("reads a 403 and a 404 as one plain line, and passes other errors on", async () => {
    for (const status of [403, 404]) {
      vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "No." }), { status })));
      await expect(createEntity({ type: "topic", name: "T", aliases: [], space_id: "x" })).rejects.toThrow(
        CANNOT_ADD_LINE,
      );
    }
    expect(CANNOT_ADD_LINE).toBe("You cannot add to that space.");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Bad name." }), { status: 422 })));
    await expect(createEntity({ type: "topic", name: "T", aliases: [], space_id: "x" })).rejects.toThrow("Bad name.");
  });
});

describe("the form", () => {
  const draw = (spaces: Space[], type: "product" | "topic" = "product") =>
    renderToStaticMarkup(
      createElement(AddEntityForm, { type, spaces, onClose: () => undefined, onCreated: () => undefined }),
    );

  it("draws the name field with the route's length, and a disabled save while the name is empty", () => {
    const html = draw(mixed);
    expect(html).toContain(`maxLength="${NAME_MAX}"`);
    expect(html).toMatch(/<button[^>]*disabled[^>]*>[\s\S]*?Add product/);
    expect(draw(mixed, "topic")).toContain("Add topic");
  });

  it("draws the space picker with the default space, and none of the read-only spaces", () => {
    const html = draw(mixed);
    expect(html).toContain("Space t1");
    expect(html).not.toContain("Space t3");
    expect(html).not.toContain("My notes");
  });
});

describe("the acts", () => {
  const base = {
    spaces: [],
    selectedSpaceId: null,
    selectedPageId: null,
    atHome: false,
    atSearch: false,
    onNavigate: () => undefined,
  };
  const draw = (props: { onAddEntity?: () => void; types?: EntityType[] }) =>
    renderToStaticMarkup(
      createElement(KnowledgeRail, {
        ...base,
        directories: { types: props.types ?? [], selected: null },
        onAddEntity: props.onAddEntity,
      }),
    );

  it("show the rail + on the Directories heading while every directory is empty", () => {
    const html = draw({ onAddEntity: () => undefined });
    expect(html).toContain("Directories");
    expect(html).toContain("Add to Directories");
  });

  it("are absent from the rail for a member with no writable space", () => {
    expect(draw({})).not.toContain("Directories");
    expect(draw({})).not.toContain("Add to Directories");
    expect(draw({ types: ["person"] })).not.toContain("Add to Directories");
  });

  it("are wired in the layout only when canAddEntity holds, and in the directory view the same", () => {
    const read1 = (f: string) => readFileSync(fileURLToPath(new URL(f, import.meta.url)), "utf8");
    const layout = read1("../layout.tsx");
    expect(layout).toMatch(/onAddEntity: canAdd \?/);
    expect(layout).toMatch(/type=\{canAdd \? adding : null\}/);
    const view = read1("../components/DirectoryView.tsx");
    expect(view).toMatch(/addType && canAddEntity/);
    expect(view).toContain("action={add ?? undefined}");
  });
});

describe("the source fence", () => {
  const files = [
    "./entityForm.ts",
    "../components/AddEntityDialog.tsx",
    "../components/DirectoryView.tsx",
    "../components/KnowledgeRail.tsx",
  ];
  const code = (f: string) =>
    readFileSync(fileURLToPath(new URL(f, import.meta.url)), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");

  it("uses no lucide, no base-ui, no inline style and no colour literal", () => {
    for (const f of files) {
      const text = code(f);
      expect(text, f).not.toMatch(/lucide-react|@base-ui/);
      expect(text, f).not.toMatch(/\bstyle=\{\{/);
      expect(text, f).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/);
    }
  });

  it("sends the write through the client only", () => {
    expect(code("../components/AddEntityDialog.tsx")).not.toMatch(/\bfetch\(/);
  });
});
