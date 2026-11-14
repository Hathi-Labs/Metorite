/**
 * PKG-10e-2 fence: the bar draws from the cached lifetime, uses the shared Button and no local colour.
 * The click flow has its own fence in `packageLifetime.test.ts` (`createLifetimeFlow`).
 *
 * Mutation 1: draw a bar for `kind: "none"`. "draws nothing" turns red.
 * Mutation 2: swap `Button` for a raw `<button`. "uses the shared Button" turns red.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import PackageLifetimeBar from "@/components/studio/PackageLifetimeBar";
import { clearAll, put } from "@/lib/dataCache";
import { type LifetimeInstall, lifetimeKey } from "@/lib/studio/packageLifetime";

beforeEach(() => clearAll());

const install = (over: Partial<LifetimeInstall> = {}): LifetimeInstall => ({
  state: "active", ends_at: "2026-12-11T12:00:00Z", archived_at: null, restore_until: null,
  now: "2026-11-11T12:00:00Z", ...over,
});
const draw = () => renderToStaticMarkup(createElement(PackageLifetimeBar, { pkg: "p1" }));

describe("PackageLifetimeBar", () => {
  it("draws nothing before the read", () => {
    expect(draw()).toBe("");
  });
  it("draws nothing for none", () => {
    put(lifetimeKey("p1"), { kind: "none" });
    expect(draw()).toBe("");
  });
  it("draws nothing for a paused install", () => {
    put(lifetimeKey("p1"), { kind: "bar", install: install({ state: "paused" }) });
    expect(draw()).toBe("");
  });
  it("draws the days and Keep for an active install", () => {
    put(lifetimeKey("p1"), { kind: "bar", install: install() });
    const html = draw();
    expect(html).toContain("Ends in 30 days");
    expect(html).toContain("Keep");
    expect(html).not.toContain("Restore");
    expect(html).not.toContain('role="alert"');
  });
  it("draws the date and Restore for an archived install", () => {
    put(lifetimeKey("p1"), { kind: "bar", install: install({ state: "archived", restore_until: "2026-12-05T10:00:00Z" }) });
    const html = draw();
    expect(html).toContain("Archived. You can restore it until ");
    expect(html).toContain("2026");
    expect(html).toContain("Restore");
    expect(html).not.toContain("Keep");
  });
});

describe("the source", () => {
  const src = readFileSync(new URL("./PackageLifetimeBar.tsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  it("uses the shared Button", () => {
    expect(src).toContain('import Button from "@/components/ui/Button"');
    expect(src).not.toMatch(/<button/);
  });
  it("holds no hex colour", () => {
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
  it("shows an error in the destructive token", () => {
    expect(src).toContain("text-destructive");
  });
});
