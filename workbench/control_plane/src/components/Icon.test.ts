/**
 * `Icon` — the paint contract.
 *
 * One shipped bug, one shape: an icon that draws its interior BLACK.
 *
 * Lucide's `Icon` builds its `<svg>` as `{...defaultAttributes, …, ...rest}`.
 * It destructures `color`, `size` and `strokeWidth` out of the props and gives
 * each a `??` default, so passing those as `undefined` is harmless. **`fill` is
 * not destructured.** It rides `...rest`, which is spread after
 * `defaultAttributes` — so `fill: undefined` overrides their `fill: "none"`,
 * React drops the attribute, and the SVG initial value applies: `black`. Every
 * closed path in every icon across the app fills black, in both colour modes,
 * from one always-passed prop key.
 *
 * Rendered to static markup rather than asserted against the props object,
 * because the defect lives in how React and Lucide *compose* those props — a
 * test of what we pass would have gone green while the app drew black icons.
 * `react-dom/server` needs no DOM, so this runs in the node environment the
 * rest of the suite uses.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";

import { resolveIcon } from "@/lib/icons";
import Icon, { themedIcon } from "./Icon";

const draw = (props: Record<string, unknown>) =>
  renderToStaticMarkup(createElement(Icon, props as never));

describe("lucide-react · the behaviour this guards against", () => {
  it("drops fill=none when the fill KEY is present with an undefined value", () => {
    // Characterising the library, not our code. Measured on lucide-react
    // v1.17.0. If an upgrade ever destructures `fill` and gives it a `??`
    // default, this test fails — and it is then safe to pass the key
    // unconditionally again. Until it fails, `Icon` must keep narrowing.
    const Glyph = resolveIcon("Inbox");
    const keyPresent = renderToStaticMarkup(
      createElement(Glyph, { size: 16, fill: undefined } as never)
    );
    const keyAbsent = renderToStaticMarkup(createElement(Glyph, { size: 16 } as never));

    expect(keyAbsent).toContain('fill="none"');
    // No attribute at all — and the SVG initial value for `fill` is black.
    expect(keyPresent).not.toContain("fill=");
  });
});

describe("Icon · the fill attribute", () => {
  it("draws an unfilled glyph when the caller sets no fill", () => {
    // The regression: `fill="none"` must SURVIVE onto the element. Asserting
    // the absence of a black fill is not enough — no attribute at all is
    // exactly the broken state, since black is the SVG default.
    expect(draw({ name: "Inbox" })).toContain('fill="none"');
  });

  it("keeps the caller's fill when one is given", () => {
    // The prop still has a job: a filled star, a solid status dot.
    const svg = draw({ name: "Star", fill: "currentColor" });
    expect(svg).toContain('fill="currentColor"');
    expect(svg).not.toContain('fill="none"');
  });

  it("draws an unfilled glyph through themedIcon too", () => {
    // ~90 call sites reach an icon as a component VALUE rather than inline;
    // they must not be a second paint path.
    const svg = renderToStaticMarkup(createElement(themedIcon("FolderKanban"), {}));
    expect(svg).toContain('fill="none"');
  });

  it("still passes the props that are not paint", () => {
    // A guard on the fix itself: narrowing `fill` must not have narrowed the
    // rest of the spread away.
    const svg = draw({ name: "Plus", size: 24, className: "text-primary" });
    expect(svg).toContain('width="24"');
    expect(svg).toContain("text-primary");
  });
});
