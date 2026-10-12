/**
 * One logo, two colour modes (`OrgBrandLockup.tsx` BrandMark, OI-1b).
 *
 * BrandMark renders BOTH images and lets CSS pick one, keyed on the `light`
 * class that next-themes puts on <html>. Nothing else ties the two together.
 * If the theme provider moved to a `data-theme` attribute, every surface that
 * does not pin `mode` would show the dark variant on the light sidebar, and a
 * white logo would vanish there. So this file fences both ends.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BrandMark } from "./OrgBrandLockup";
import type { OrgBranding } from "@/lib/orgBranding";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

const PNG = "data:image/png;base64,iVBORw0KGgo=";
const logo = (dataUri: string) => ({ dataUri, mime: "image/png", width: 252, height: 84, byteSize: 100 });
const branding = (b: Partial<OrgBranding>): OrgBranding =>
  ({ logo: logo(PNG), updatedBy: "", updatedAt: "", ...b }) as OrgBranding;

/** Each <img>'s src and class, in order. */
const imgs = (html: string) =>
  [...html.matchAll(/<img\b[^>]*>/g)].map((m) => ({
    src: /src="([^"]*)"/.exec(m[0])?.[1] ?? "",
    cls: (/class="([^"]*)"/.exec(m[0])?.[1] ?? "").replace(/&amp;/g, "&"),
  }));

const render = (b: OrgBranding, mode?: "light" | "dark") =>
  imgs(renderToStaticMarkup(createElement(BrandMark, { branding: b, fallbackCaption: "x", mode })));

/**
 * The shell bar's one-line lockup (owner, 2026-10-09). It is the same
 * BrandMark, so it must keep the same two-image rule, and the D51 name.
 */
describe("the compact lockup in the full-width shell bar", () => {
  const WHITE = "data:image/png;base64,V0hJVEU=";
  const html = (b: OrgBranding | null, orgName = "") =>
    renderToStaticMarkup(
      createElement(BrandMark, { branding: b, fallbackCaption: "Control Plane", orgName, height: 24, maxWidth: 200, compact: true }),
    );

  it("still lets CSS pick the image for the colour mode", () => {
    const [light, dark] = imgs(html(branding({ logoDark: logo(WHITE), darkStyle: "white" })));
    expect(light.src).toBe(PNG);
    expect(light.cls.split(" ")).toEqual(expect.arrayContaining(["hidden", "[.light_&]:block"]));
    expect(dark.src).toBe(WHITE);
    expect(html(branding({ logoDark: logo(WHITE), darkStyle: "white" }))).toContain("[.light_&amp;]:hidden");
  });

  it("draws the logo 24px tall, at its own aspect ratio", () => {
    // 252×84 is 3:1, so 72px wide at 24px.
    expect(html(branding({}))).toMatch(/height:24px;width:72px/);
  });

  it("gives a wide logo the whole slot, with no width kept for a caption", () => {
    // 1200×100 is 12:1, so it wants 288px at 24px and the cap stops it. The
    // cap is the whole `maxWidth` now. It was `maxWidth` less 100 until
    // 2026-10-11, when the caption beside the logo went.
    const wide = { ...logo(PNG), width: 1200, height: 100 };
    expect(html(branding({ logo: wide }))).toMatch(/height:24px;width:200px/);
  });

  it("with no logo: our mark and the organization's own name on one line (D51)", () => {
    const out = html({ logo: null, updatedBy: "", updatedAt: "" } as OrgBranding, "Acme Robotics");
    expect(imgs(out)).toEqual([]);
    expect(out).toContain("Acme Robotics");
    expect(out).not.toContain("Control Plane");
  });

  it("with no organization yet: our mark and the product's name", () => {
    expect(html(null)).toContain("Metorite");
  });
});

/**
 * Owner decision, 2026-10-11: a workspace with no logo shows OUR comet, the
 * same mark that signs the bar's right end. Not the ⌘ glyph it showed before, and not
 * on a `bg-primary` tile, which an accent recoloured.
 */
describe("with no logo, the comet", () => {
  const NONE = { logo: null, updatedBy: "", updatedAt: "" } as OrgBranding;
  const out = (p: Record<string, unknown>) =>
    renderToStaticMarkup(createElement(BrandMark, { branding: NONE, fallbackCaption: "Control Plane", orgName: "Acme", ...p }));
  const arrangements = [
    { compact: true, height: 24, maxWidth: 176 },
    { compact: false, height: 28, maxWidth: 152 },
  ];

  it("draws MetoriteMark in the bar and in the head, with no glyph and no accent fill", () => {
    for (const a of arrangements) {
      const html = out(a);
      expect(html).toContain("data-metorite-mark");
      expect(html).not.toMatch(/lucide-command|bg-primary|text-primary-foreground/);
    }
  });

  it("keeps the tile's square box, so the bar does not shift", () => {
    expect(out({ compact: true, height: 24 })).toMatch(/data-our-mark="" class="[^"]*" style="height:24px;width:24px"/);
    expect(out({ compact: false, height: 28 })).toMatch(/data-our-mark="" class="[^"]*" style="height:28px;width:28px"/);
  });

  it("keeps the organization's name beside it", () => {
    expect(out({ compact: true, height: 24 })).toContain("Acme");
  });

  it("follows a pinned mode, for the Settings preview", () => {
    expect(out({ compact: true, height: 24, mode: "light" })).toContain("[stop-color:#07133c]");
    expect(out({ compact: true, height: 24, mode: "light" })).not.toContain("[.light_&amp;]");
  });
});

/**
 * Owner decision, 2026-10-11: a customer's logo carries nothing of ours, in
 * the bar, in the sidebar's head or in the phone menu's head. Our comet signs
 * the sidebar's foot since 2026-10-12 (`MetoriteFoot.tsx`).
 */
describe("no caption beside a customer's logo", () => {
  const all = (b: OrgBranding) =>
    [
      { compact: true, height: 24, maxWidth: 176 },
      { compact: false, height: 28, maxWidth: 152 },
      { compact: false, height: 28, maxWidth: 180 },
    ].map((p) => renderToStaticMarkup(createElement(BrandMark, { branding: b, fallbackCaption: "Control Plane", orgName: "Acme", ...p })));

  it("draws the logo and no words, in every arrangement", () => {
    for (const out of all(branding({}))) {
      expect(out).not.toMatch(/powered by/i);
      expect(out).not.toContain("Metorite");
      // No text node at all: only the images and their wrappers.
      expect(out.replace(/<[^>]*>/g, "")).toBe("");
    }
  });

  it("no source file spells the old caption", () => {
    const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
    for (const rel of ["components/OrgBrandLockup.tsx", "lib/orgBranding.ts", "app/settings/organization/BrandingTab.tsx", "components/AccountSwitcher.tsx"]) {
      expect(strip(read(rel)), rel).not.toMatch(/powered by|POWERED_BY|CAPTION_RESERVE/i);
    }
  });
});

describe("the theme provider and BrandMark agree on the class", () => {
  it("next-themes writes the mode as a class on the root", () => {
    expect(read("components/Providers.tsx")).toMatch(/<ThemeProvider\s[^>]*attribute="class"/);
  });
  it("globals.css defines the light mode under that class", () => {
    expect(read("app/globals.css")).toMatch(/^\.light\s*\{/m);
  });
});

describe("with no mode pinned, CSS picks one image", () => {
  const WHITE = "data:image/png;base64,V0hJVEU=";
  const [light, dark] = render(branding({ logoDark: logo(WHITE), darkStyle: "white" }));

  it("shows the light image only under .light", () => {
    expect(light.src).toBe(PNG);
    expect(light.cls.split(" ")).toEqual(expect.arrayContaining(["hidden", "[.light_&]:block"]));
  });
  it("shows the white version everywhere else, and never under .light", () => {
    expect(dark.src).toBe(WHITE);
  });
  it("hides the dark image's wrapper under .light", () => {
    const html = renderToStaticMarkup(
      createElement(BrandMark, { branding: branding({ logoDark: logo(WHITE), darkStyle: "white" }), fallbackCaption: "x" }),
    );
    expect(html).toContain("[.light_&amp;]:hidden");
  });
});

describe("a pinned mode shows exactly that image", () => {
  it("light", () => {
    const [l] = render(branding({}), "light");
    expect(l.cls.split(" ")).toContain("block");
    expect(l.cls).not.toContain("[.light_&]");
  });
  it("dark, for an old row with no dark image: the logo itself", () => {
    const shown = render(branding({}), "dark");
    expect(shown[0].cls.split(" ")).toContain("hidden");
    expect(shown[1].src).toBe(PNG);
  });
});
