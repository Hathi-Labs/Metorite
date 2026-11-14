/**
 * STU-D19a fence: the pure model of the Gallery block.
 *
 * Mutation: let `coverSrc` skip the `seen` check. "gives no address to a card the observer has
 * not reported" turns red. Mutation: drop `safeAddress` from `coverSrc`. "refuses an address of
 * another site" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import type { FieldSpec } from "./fieldCheck";
import {
  EVERY_CARD, MAX_CARD_FIELDS, cardKey, coverFile, coverSrc, galleryBlock, markSeen, parseGallery, safeAddress,
  statusInput, type GalleryConfig,
} from "./galleryModel";

const FIELDS: FieldSpec[] = [
  { key: "name", type: "text", label: "Asset", required: true },
  { key: "status", type: "select", label: "Status", options: ["Active", "On hold", "Retired"] },
  { key: "owner", type: "person" },
  { key: "photo", type: "file", label: "Photo" },
  { key: "manual", type: "file", label: "Manual" },
  { key: "shots", type: "file", many: true },
  { key: "bought_on", type: "date" },
  { key: "kind", type: "select", options: ["Desk", "Printer"] },
];
const config = (extra: Record<string, unknown> = {}): GalleryConfig => {
  const p = parseGallery({ fields: FIELDS, ...extra });
  if (!p.ok) throw new Error(p.reason);
  return p.config;
};
const ADDRESS = (id: string) => `/pictures/${id}.png`;

describe("parseGallery", () => {
  it("reads the title, the cover, the card fields and the link", () => {
    const c = config({ titleField: "name", coverField: "photo", cardFields: ["status", "owner"], openBase: "?ro=asset&rec=" });
    expect(c.titleField?.key).toBe("name");
    expect(c.coverFields.map((f) => f.key)).toEqual(["photo"]);
    expect(c.cardFields.map((f) => f.key)).toEqual(["status", "owner"]);
    expect(c.openBase).toBe("?ro=asset&rec=");
  });
  it("takes every file field as a cover field when the block names none", () => {
    expect(config().coverFields.map((f) => f.key)).toEqual(["photo", "manual", "shots"]);
  });
  it("refuses a cover field that is not a file field, and one that does not exist", () => {
    for (const coverField of ["status", "nope", 7]) {
      const p = parseGallery({ fields: FIELDS, coverField });
      expect(p.ok, String(coverField)).toBe(false);
      if (!p.ok) expect(p.reason).toMatch(/cover from a file field/);
    }
  });
  it("refuses a node with no fields", () => {
    expect(parseGallery({}).ok).toBe(false);
    expect(parseGallery(null).ok).toBe(false);
  });
  it("keeps a file, the title and a repeat out of the card fields, and caps them", () => {
    const c = config({ titleField: "name", cardFields: ["name", "photo", "status", "status", "owner", "bought_on", "kind"] });
    expect(c.cardFields.map((f) => f.key)).toEqual(["status", "owner", "bought_on"]);
    expect(c.cardFields.length).toBe(MAX_CARD_FIELDS);
  });
  it("names a status field: a single select called status, stage or state", () => {
    expect(config({ cardFields: ["kind", "status"] }).statusField?.key).toBe("status");
    expect(config({ cardFields: ["kind"] }).statusField).toBeNull();
  });
  it("gives no link when openBase is empty", () => {
    expect(config({ openBase: "" }).openBase).toBeNull();
  });
  it("builds a template node named gallery, with its source", () => {
    const node = galleryBlock({ fields: FIELDS }, { action: "object:asset.list" });
    expect(node).toEqual({ type: "template", props: { name: "gallery", source: { action: "object:asset.list" }, data: { fields: FIELDS } } });
  });
});

describe("the cover", () => {
  const c = config();
  it("is the first file of the cover fields, in their order", () => {
    expect(coverFile(c, { photo: { id: "p1" }, manual: { id: "m1" } })?.id).toBe("p1");
    expect(coverFile(c, { manual: { id: "m1" } })?.id).toBe("m1");
    expect(coverFile(c, { shots: [{ id: "s1" }, { id: "s2" }] })?.id).toBe("s1");
  });
  it("is null for a record with no file, and for a field that the door left out (C8)", () => {
    expect(coverFile(c, {})).toBeNull();
    expect(coverFile(c, { photo: null, manual: undefined, shots: [] })).toBeNull();
    expect(coverFile(c, { photo: { id: "" } })).toBeNull();
    expect(coverFile(c, { photo: "p1" })).toBeNull();
  });
  it("keeps to the named cover field", () => {
    expect(coverFile(config({ coverField: "photo" }), { manual: { id: "m1" } })).toBeNull();
  });
});

describe("the lazy load", () => {
  const c = config();
  const row = { id: "a1", photo: { id: "p1" } };
  it("gives no address to a card the observer has not reported", () => {
    expect(coverSrc(c, row, "id:a1", new Set(), ADDRESS)).toBeNull();
    expect(coverSrc(c, row, "id:a1", new Set(["id:a2"]), ADDRESS)).toBeNull();
  });
  it("gives the address once the observer reported the card", () => {
    expect(coverSrc(c, row, "id:a1", new Set(["id:a1"]), ADDRESS)).toBe("/pictures/p1.png");
    expect(coverSrc(c, row, "id:a1", EVERY_CARD, ADDRESS)).toBe("/pictures/p1.png");
  });
  it("gives no address with no door address, or with no cover", () => {
    expect(coverSrc(c, row, "id:a1", EVERY_CARD, null)).toBeNull();
    expect(coverSrc(c, { id: "a1" }, "id:a1", EVERY_CARD, ADDRESS)).toBeNull();
    expect(coverSrc(c, row, "id:a1", EVERY_CARD, () => { throw new Error("x"); })).toBeNull();
  });
  it("refuses an address of another site", () => {
    for (const bad of ["https://example.com/a.png", "//example.com/a.png", "/\\example.com", "javascript:alert(1)",
      "data:text/html;base64,PHA+", "data:image/svg+xml;base64,PHN2Zz4=", "/a b.png", ""]) {
      expect(coverSrc(c, row, "id:a1", EVERY_CARD, () => bad), bad).toBeNull();
    }
  });
  it("takes a path of this site and the data of a raster image", () => {
    expect(safeAddress("/office-objects/desk-computer/south.png")).toBe("/office-objects/desk-computer/south.png");
    expect(safeAddress("data:image/png;base64,iVBORw0KGgo=")).toBe("data:image/png;base64,iVBORw0KGgo=");
    expect(safeAddress(42)).toBeNull();
  });
  it("adds the keys of a report, and keeps the same set when nothing is new", () => {
    const one = markSeen(new Set(), ["id:a1"]);
    expect([...one]).toEqual(["id:a1"]);
    expect(markSeen(one, ["id:a1"])).toBe(one);
    expect([...markSeen(one, ["id:a2", "id:a1"])]).toEqual(["id:a1", "id:a2"]);
  });
  it("keys a card by its id, and a row with no id by its place", () => {
    expect(cardKey({ id: "a1" }, 4)).toBe("id:a1");
    expect(cardKey({ id: 7 }, 4)).toBe("id:7");
    expect(cardKey({}, 4)).toBe("at:4");
  });
});

describe("the status chip", () => {
  const status = FIELDS[1];
  it("gives a status value its place among the options, as a Board lane", () => {
    expect(statusInput(status, "On hold")).toEqual({ name: "On hold", index: 1, total: 3 });
  });
  it("gives no chip input for a value that is no option", () => {
    expect(statusInput(status, "Lost")).toBeNull();
    expect(statusInput(status, null)).toBeNull();
    expect(statusInput(status, "")).toBeNull();
  });
});

describe("the source", () => {
  it("keeps React and a formatter out of the model", () => {
    const src = readFileSync(new URL("./galleryModel.ts", import.meta.url), "utf8");
    expect(src).not.toMatch(/from "react/);
    expect(src).not.toMatch(/Intl\.|toLocale|toFixed/);
  });
});
