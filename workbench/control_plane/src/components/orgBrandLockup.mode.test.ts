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

  it("draws the logo 24px tall, and keeps room for the caption beside it", () => {
    // 252×84 is 3:1, so 72px wide at 24px. The cap is 200 less the reserve.
    const out = html(branding({}));
    expect(out).toMatch(/height:24px;width:72px/);
    expect(out).toContain("powered by Metorite");
    // The caption hides below lg and never truncates.
    expect(out).toMatch(/class="hidden shrink-0 whitespace-nowrap[^"]*lg:inline"/);
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
