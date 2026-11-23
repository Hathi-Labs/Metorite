/**
 * STU-D19a fence: the Gallery block.
 *
 * The vitest env has no DOM and takes `*.test.ts` only. So the view draws from props with
 * `renderToStaticMarkup`, as `StudioBoard.test.ts` does. A static render runs no effect, so no
 * observer reports a card, and that is the first markup a browser gets. `GalleryGrid` takes the
 * seen keys as a prop, so a test can play the observer.
 *
 * Mutation: give `coverSrc` the address of every card, seen or not. "holds no image address
 * before the observer reports a card" turns red.
 */
import { existsSync, readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/blocks-preview",
}));

import GenerativeUINode from "@/components/GenerativeUINode";
import StudioGallery, { GalleryGrid } from "@/components/studio/StudioGallery";
import { clearAll } from "@/lib/dataCache";
import { statusAccent } from "@/lib/statusAccent";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type Row, bindsSource, withMeta } from "@/lib/studio/dataDoor";
import { EVERY_CARD, galleryBlock, parseGallery } from "@/lib/studio/galleryModel";
import { recordLinkBase } from "@/lib/studio/recordPageModel";
import {
  NO_PHOTO_EVERY, SAMPLE_ASSET_FIELDS, SAMPLE_PICTURES, makeSampleDoor, sampleAssetRows, sampleFileAddress,
} from "@/lib/studio/sampleDoor";

beforeEach(() => clearAll());

const OPEN = recordLinkBase("", "asset");
const DATA = {
  fields: SAMPLE_ASSET_FIELDS, titleField: "name", coverField: "photo",
  cardFields: ["status", "owner", "bought_on"], openBase: OPEN,
};
const ROWS = sampleAssetRows(200);
const IMG_WITH_SRC = /<img\b[^>]*\bsrc="([^"]*)"/g;

function configOf(data: unknown = DATA) {
  const p = parseGallery(data);
  if (!p.ok) throw new Error(p.reason);
  return p.config;
}
const view = (rows: Row[] = ROWS, data: unknown = DATA) =>
  renderToStaticMarkup(createElement(StudioGallery, { data, rows, address: sampleFileAddress }));
const grid = (seen: ReadonlySet<string> | typeof EVERY_CARD, rows: Row[] = ROWS, address = sampleFileAddress) =>
  renderToStaticMarkup(createElement(GalleryGrid, { config: configOf(), rows, seen, address }));
const srcs = (html: string) => [...html.matchAll(IMG_WITH_SRC)].map((m) => m[1]);
/** The markup of one card, from its key to the next card. */
const card = (html: string, id: string) => {
  const start = html.indexOf(`data-gallery-key="id:${id}"`);
  const end = html.indexOf("data-gallery-key=", start + 1);
  return html.slice(start, end < 0 ? undefined : end);
};

describe("the grid", () => {
  it("draws 200 cards for 200 sample rows", () => {
    expect(view().match(/data-gallery-key="/g)?.length).toBe(200);
  });
  it("holds no image address before the observer reports a card", () => {
    const out = view();
    expect(srcs(out)).toEqual([]);
    expect(out).not.toMatch(/<img\b/);
  });
  it("gives an address only to the cards that the observer reported", () => {
    const out = grid(new Set(["id:s1", "id:s2"]));
    expect(srcs(out)).toEqual([SAMPLE_PICTURES[1], SAMPLE_PICTURES[2]]);
    expect(card(out, "s1")).toContain(`src="${SAMPLE_PICTURES[1]}"`);
    expect(card(out, "s3")).not.toMatch(/<img\b/);
  });
  it("loads each image lazily, in a box that keeps its space", () => {
    const out = grid(new Set(["id:s1"]));
    expect(out).toMatch(/<img\b[^>]*loading="lazy"/);
    expect(out).toMatch(/<img\b[^>]*class="[^"]*size-full/);
    expect(out).toContain("aspect-[4/3]");
  });
  it("draws the placeholder for a row with no cover, even when seen", () => {
    const out = grid(EVERY_CARD);
    const bare = `s${NO_PHOTO_EVERY}`;
    expect(ROWS.find((r) => r.id === bare)).not.toHaveProperty("photo");
    expect(card(out, bare)).toContain("data-gallery-placeholder");
    expect(card(out, bare)).not.toMatch(/<img\b/);
    expect(out.match(/data-gallery-placeholder/g)?.length).toBe(Math.floor(200 / NO_PHOTO_EVERY));
    expect(srcs(out).length).toBe(200 - Math.floor(200 / NO_PHOTO_EVERY));
  });
  it("draws the placeholder for each card when the door has no file address, as a live door", () => {
    const out = renderToStaticMarkup(createElement(GalleryGrid, { config: configOf(), rows: ROWS, seen: EVERY_CARD, address: null }));
    expect(out).not.toMatch(/<img\b/);
    expect(out.match(/data-gallery-placeholder/g)?.length).toBe(200);
  });
  it("links a card with an id to openBase plus the id", () => {
    const out = view();
    expect(card(out, "s5")).toContain(`href="?ro=asset&amp;rec=s5"`);
    expect(card(out, "s5")).toContain(">Asset 5</a>");
  });
  it("draws a card with no id, and with no link", () => {
    const out = view([{ name: "Loose", photo: { id: "x1" } }]);
    expect(out).toContain('data-gallery-key="at:0"');
    expect(out).toContain("Loose");
    expect(out).not.toContain("href=");
  });
  it("draws no link when the block has no openBase", () => {
    expect(view(ROWS.slice(0, 3), { ...DATA, openBase: undefined })).not.toContain("href=");
  });
  it("draws a status as its chip, with the hue of its Board lane", () => {
    const out = view(ROWS.slice(0, 3));
    const active = statusAccent({ name: "Active", index: 0, total: 3 });
    const hold = statusAccent({ name: "On hold", index: 1, total: 3 });
    expect(card(out, "s1")).toContain(active.soft);
    expect(card(out, "s2")).toContain(hold.soft);
  });
  it("says that records are left out when the door gives a total", () => {
    const rows = withMeta(ROWS.slice(0, 100), { total: 250 });
    expect(view(rows)).toContain("Showing the first 100 of 250 records.");
  });
  it("gives a cover field that is not a file an error state and no cards", () => {
    const out = view(ROWS, { ...DATA, coverField: "status" });
    expect(out).toContain('role="alert"');
    expect(out).not.toContain("data-gallery-key");
  });
  it("draws rows that hold a null", () => {
    const rows = [null, ...ROWS.slice(0, 2)] as unknown as Row[];
    expect(view(rows).match(/data-gallery-key="/g)?.length).toBe(2);
  });
});

describe("the bind path", () => {
  it("binds through the page door as a template named gallery", () => {
    const node = galleryBlock(DATA, { action: "object:asset.list" });
    expect(bindsSource("template", node.props)).toBe(true);
    const out = renderToStaticMarkup(
      createElement(StudioDataContext.Provider, { value: makeSampleDoor(SAMPLE_ASSET_FIELDS, ROWS) },
        createElement(GenerativeUINode, { spec: node })),
    );
    expect(out).toContain('aria-busy="true"');
  });
});

describe("the sample door", () => {
  it("gives the address of a picture that ships in this repo", () => {
    const door = makeSampleDoor(SAMPLE_ASSET_FIELDS, ROWS);
    expect(door.fileAddress?.("00000000-0000-4000-8000-000000000003")).toBe(SAMPLE_PICTURES[3]);
    expect(door.fileAddress?.("no-digit")).toBeNull();
    const pub = new URL("../../../public", import.meta.url);
    for (const p of SAMPLE_PICTURES) {
      expect(p, p).toMatch(/^\/[a-z0-9/-]+\.png$/);
      expect(existsSync(new URL("." + p, pub + "/")), p).toBe(true);
    }
  });
});

describe("the source", () => {
  const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
  const src = read("./StudioGallery.tsx");
  it("draws cards with the shared card parts and the Board's card value", () => {
    expect(src).toContain("<TaskCardShell");
    expect(src).toContain("<TaskCardTitle");
    expect(src).toContain("<CardValue");
    expect(src).toContain("statusAccent(");
  });
  it("guards the observer, and marks the image lazy", () => {
    expect(src).toContain('typeof IntersectionObserver === "undefined"');
    expect(src).toContain('loading="lazy"');
  });
  it("writes nothing and formats nothing of its own", () => {
    expect(src).not.toMatch(/\.run\(/);
    expect(src).not.toMatch(/Intl\.|toLocale|toFixed/);
  });
});
