/**
 * The shared toast clears the phone bottom nav (PR #475 review).
 *
 * `AppShell` pins the phone nav at `fixed bottom-0 z-50`, 3.5rem plus the
 * safe-area inset tall. The toast viewport sat at `bottom-4`, so every toast
 * painted over the nav for its whole life. `.toast-viewport-bottom` lifts it
 * by the nav's own clearance (the `.pb-nav` value) below `sm`, and keeps the
 * 1rem it always had from `sm` up, where there is no bottom nav.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8").replace(/\r\n/g, "\n");

const css = read("../../app/globals.css");
const rule = (selector: string, from: string) => {
  const at = from.indexOf(`${selector} {`);
  expect(at, `${selector} is missing`).toBeGreaterThan(-1);
  return from.slice(at, from.indexOf("}", at));
};

describe("the toast viewport on a phone", () => {
  it("the viewport wears the class, and no fixed bottom of its own", () => {
    const toast = read("Toast.tsx");
    const viewport = toast.match(/<BaseToast\.Viewport className="([^"]+)"/)![1].split(/\s+/);
    expect(viewport).toContain("toast-viewport-bottom");
    expect(viewport.filter((c) => /^bottom-/.test(c))).toEqual([]);
  });

  it("below sm it clears the nav: the nav's height, the safe area, and a gap", () => {
    const base = rule(".toast-viewport-bottom", css);
    expect(base).toMatch(/bottom: calc\(3\.5rem \+ env\(safe-area-inset-bottom, 0px\) \+ 0\.5rem\)/);
    // The same clearance the page content uses for the same nav.
    expect(rule(".pb-nav", css)).toMatch(/calc\(3\.5rem \+ env\(safe-area-inset-bottom, 0px\)\)/);
  });

  it("from sm up (no bottom nav) it keeps the old 1rem", () => {
    const media = css.slice(css.indexOf("@media (min-width: 640px) {\n  .toast-viewport-bottom"));
    expect(media.length, "the sm override is missing").toBeGreaterThan(0);
    expect(rule(".toast-viewport-bottom", media)).toMatch(/bottom: 1rem;/);
  });
});
