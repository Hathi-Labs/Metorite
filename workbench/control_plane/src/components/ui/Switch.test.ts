/**
 * `Switch` — one switch, two looks (D141, GL-2).
 *
 * `vitest.config.ts` runs in `node`, so these tests render to markup and read
 * the class strings. The pixel claim, that classic is unchanged, is the
 * visual-review capture in the GL-2 PR. This file holds the semantics and the
 * glass contract.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import Switch, { switchClasses, type SwitchClassic } from "./Switch";

const html = (props: Record<string, unknown>) =>
  renderToStaticMarkup(
    createElement(Switch, { label: "Show column", onChange: () => {}, ...props } as never),
  );

const SHAPES: SwitchClassic[] = ["default", "compact", "outlined"];

describe("Switch semantics", () => {
  it("is a switch that names its state and itself", () => {
    const out = html({ checked: true });
    expect(out).toContain('role="switch"');
    expect(out).toContain('aria-checked="true"');
    expect(out).toContain('aria-label="Show column"');
    expect(out).toContain('type="button"');
  });

  it("reports off as aria-checked false", () => {
    expect(html({ checked: false })).toContain('aria-checked="false"');
  });

  it("passes disabled and title to the button", () => {
    const out = html({ checked: false, disabled: true, title: "Why" });
    expect(out).toContain("disabled");
    expect(out).toContain('title="Why"');
  });
});

describe("Switch glass contract", () => {
  it.each(SHAPES)("%s: green when on and muted when off", (shape) => {
    expect(switchClasses(shape, true).track).toContain("glass:bg-system-green");
    expect(switchClasses(shape, false).track).toContain("glass:bg-muted");
    expect(switchClasses(shape, false).track).not.toContain("glass:bg-system-green");
  });

  it.each(SHAPES)("%s: Aroh's 38 by 22 track, 51 by 31 on touch", (shape) => {
    const { track } = switchClasses(shape, true);
    expect(track).toContain("glass:h-[22px]");
    expect(track).toContain("glass:w-[38px]");
    expect(track).toContain("glass:pointer-coarse:h-[31px]");
    expect(track).toContain("glass:pointer-coarse:w-[51px]");
  });

  it.each(SHAPES)("%s: the classic part never names the glass colour", (shape) => {
    for (const checked of [true, false]) {
      const c = switchClasses(shape, checked);
      const bare = `${c.track} ${c.knob}`.split(/\s+/).filter((t) => !t.startsWith("glass:"));
      expect(bare.some((t) => t.includes("system-green"))).toBe(false);
    }
  });
});

describe("Switch classic shapes keep what each copy drew", () => {
  it("default: the Tasks settings modal", () => {
    const on = switchClasses("default", true);
    expect(on.track).toContain(
      "tech-transition relative inline-flex h-5 w-9 shrink-0 items-center rounded-full bg-primary",
    );
    expect(on.knob).toContain(
      "inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform translate-x-[18px]",
    );
    expect(switchClasses("default", false).track).toContain("bg-secondary");
    expect(switchClasses("default", false).knob).toContain("translate-x-[2px]");
  });

  it("compact: the email automation Toggle, 32 by 18", () => {
    const on = switchClasses("compact", true);
    expect(on.track).toContain("h-[18px] w-[32px]");
    expect(on.track).toContain("bg-primary");
    expect(switchClasses("compact", false).track).toContain("bg-muted-foreground/30");
    expect(on.knob).toContain("left-[15px]");
    expect(switchClasses("compact", false).knob).toContain("left-0.5");
  });

  it("outlined: the Meeting copilot toggle", () => {
    const on = switchClasses("outlined", true);
    expect(on.track).toContain("border-primary bg-primary/30");
    expect(switchClasses("outlined", false).track).toContain("border-border bg-secondary");
    expect(on.knob).toContain("left-[calc(100%-1.125rem)] bg-primary");
    expect(switchClasses("outlined", false).knob).toContain("left-0.5 bg-muted-foreground");
  });
});
