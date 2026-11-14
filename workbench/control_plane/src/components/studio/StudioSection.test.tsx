/**
 * STU-AF7 fence: one section of a page. The env has no DOM, so the section draws from a fake
 * `window.localStorage` with `renderToStaticMarkup`. A second draw is a remount after a reload.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import StudioSection from "@/components/studio/StudioSection";
import { foldKey, writeFolded } from "@/lib/studio/sectionFold";

function store(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
  } as unknown as Storage;
}

const draw = (scope: string | null = "app") =>
  renderToStaticMarkup(
    <StudioSection scope={scope} pageId="main" id="open" title="Open visits"><p>BODY</p></StudioSection>,
  );

afterEach(() => vi.unstubAllGlobals());

describe("StudioSection", () => {
  it("draws its title as text and starts open", () => {
    vi.stubGlobal("window", { localStorage: store() });
    const html = draw();
    expect(html).toContain("Open visits");
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("BODY");
  });

  it("stays folded after a reload, with its content kept in the page", () => {
    const s = store();
    vi.stubGlobal("window", { localStorage: s });
    writeFolded(s, foldKey("app", "main", "open"), true);
    const html = draw(); // a new mount reads the same storage
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("BODY"); // keepMounted: the table keeps its data
  });

  it("keeps the fold for this app, this page and this section only", () => {
    const s = store();
    vi.stubGlobal("window", { localStorage: s });
    writeFolded(s, foldKey("other-app", "main", "open"), true);
    expect(draw()).toContain('aria-expanded="true"');
  });

  it("draws open and does not crash when the storage throws", () => {
    vi.stubGlobal("window", { localStorage: { getItem: () => { throw new Error("blocked"); } } });
    expect(draw()).toContain('aria-expanded="true"');
  });

  it("draws open when the storage getter itself throws", () => {
    vi.stubGlobal("window", { get localStorage(): Storage { throw new Error("denied"); } });
    expect(draw()).toContain('aria-expanded="true"');
  });

  it("draws open with no scope, and with no window", () => {
    expect(draw(null)).toContain('aria-expanded="true"');
    expect(draw()).toContain('aria-expanded="true"');
  });

  it("draws a title with markup as text", () => {
    const html = renderToStaticMarkup(
      <StudioSection scope="a" pageId="p" id="s" title={"<img src=x onerror=1>"}>x</StudioSection>);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});
