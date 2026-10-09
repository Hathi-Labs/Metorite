// The dark look of an email body (owner, 2026-10-10), and the light version.
//
// R7 fences named here:
//   * `email-dark-classifier`: an email with its own dark CSS gets `native`,
//     simple HTML gets `tokens`, and a newsletter with backgrounds gets
//     `invert`. Light mode and the light version get `original`.
//   * `email-dark-reinvert`: the invert look re-inverts each img, picture,
//     video, canvas and svg image. A box with a background picture is a light
//     island: re-inverted, with a white backing that a sender's own colour
//     beats (coordinator decision, 2026-10-10, round 2).
//   * `email-dark-linear`: a crafted mail of 2 MB classifies in under 100 ms,
//     and so does each pattern just under the size limit.
//   * `email-dark-base`: the inverted base lands on the card token, not black.
//   * `email-dark-print`: every rule of a dark look is screen-only, so a print
//     draws the original sheet.
//   * `email-dark-no-content`: no text of the email reaches the CSS.
//   * `email-light-version`: the stored list toggles, caps and survives junk.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { parseColor, type Rgb } from "@/lib/theme/contrast";
import { THEME } from "@/lib/theme/themes";
import {
  BLOCKED_REMOTE_CSS, CLASSIFY_LIMIT, INVERT_FILTER, ISLAND_Z, LAYER_Z, OVERLAY_SPREAD_PX, REINVERT_CSS, bodyLookCss, bodyPalette, chooseBodyLook, classifyEmailHtml,
  cssColour, forceDarkMedia, frameColorScheme, invertBase, invertColour,
} from "./bodyLook";
import { MAX_IDS, parseIds, toggleId } from "./lightVersion";

// A sender's own dark design: a media query in its style block.
const DARK_AWARE = `
<style>
  body { background: #ffffff; color: #111111; }
  @media (prefers-color-scheme: dark) {
    body { background: #121212 !important; color: #eeeeee !important; }
  }
</style>
<p>Your order shipped.</p>`;

// A short note from a person: no colour, no background.
const PLAIN = `<div dir="ltr">Hi Priya,<br><br>The quote is attached.<br><br>Ravi</div>
<blockquote><p>Can you send the quote?</p></blockquote>
<p><a href="https://example.test/q">The quote</a></p><img src="data:image/png;base64,AAAA" alt="">`;

// A newsletter: a grey frame, a white column, a hero image and a coloured button.
const NEWSLETTER = `
<table width="100%" bgcolor="#f4f4f4"><tr><td align="center">
  <table width="600" style="background-color:#ffffff;">
    <tr><td style="background-image:url(https://cdn.example.test/hero.jpg)"><img src="https://cdn.example.test/logo.png"></td></tr>
    <tr><td style="color:#333333;font-size:16px">This week in printing.</td></tr>
    <tr><td><a href="https://example.test" style="background:#1a73e8;color:#ffffff">Read more</a></td></tr>
  </table>
</td></tr></table>`;

describe("email-dark-classifier", () => {
  it("picks the email's own dark design first", () => {
    expect(classifyEmailHtml(DARK_AWARE)).toBe("native");
    // The CSS property counts too, on a mail with no colours of its own.
    expect(classifyEmailHtml("<style>:root { color-scheme: light dark; }</style><p>x</p>")).toBe("native");
    expect(classifyEmailHtml("<style>:root { supported-color-schemes: light dark; }</style><p>x</p>")).toBe("native");
    // A scheme with no media query does not make dark text on a white box safe.
    expect(classifyEmailHtml('<style>:root{color-scheme:light dark}</style><p style="color:#000;background:#fff">x</p>')).toBe("invert");
    // A meta tag does not count: the sanitizer removes `meta`, and the
    // classifier reads the sanitized markup.
    expect(classifyEmailHtml('<meta name="color-scheme" content="light dark"><p>x</p>')).toBe("tokens");
  });

  it("gives simple HTML the app's tokens", () => {
    expect(classifyEmailHtml(PLAIN)).toBe("tokens");
    // A value that sets nothing is not a colour of the email.
    expect(classifyEmailHtml('<p style="color: inherit; background: transparent">x</p>')).toBe("tokens");
    // A border colour and a light-only scheme do not make it styled.
    expect(classifyEmailHtml('<p style="border-color:#ccc">x</p>')).toBe("tokens");
    expect(classifyEmailHtml('<meta name="color-scheme" content="light"><p>x</p>')).toBe("tokens");
  });

  it("inverts a newsletter with its own backgrounds and colours", () => {
    expect(classifyEmailHtml(NEWSLETTER)).toBe("invert");
    expect(classifyEmailHtml('<font color="red">x</font>')).toBe("invert");
    expect(classifyEmailHtml('<style>.x{background-color:#fff}</style><p class="x">x</p>')).toBe("invert");
  });

  it("draws the original in light mode and in the light version", () => {
    expect(chooseBodyLook({ dark: false, lightVersion: false, html: NEWSLETTER })).toBe("original");
    expect(chooseBodyLook({ dark: true, lightVersion: true, html: NEWSLETTER })).toBe("original");
    expect(chooseBodyLook({ dark: true, lightVersion: false, html: NEWSLETTER })).toBe("invert");
    expect(bodyLookCss("original", bodyPalette())).toBe("");
  });

  it("lets only the native look ask the frame for dark", () => {
    expect(frameColorScheme("native")).toBe("dark");
    for (const look of ["original", "tokens", "invert"] as const) expect(frameColorScheme(look)).toBe("light");
  });
});

describe("email-dark-native", () => {
  it("turns the sender's dark media on for the screen, and its light media off", () => {
    expect(forceDarkMedia("<style>@media (prefers-color-scheme: dark) { p { color: #eee } }</style>"))
      .toBe("<style>@media screen and (min-width: 0px) { p { color: #eee } }</style>");
    expect(forceDarkMedia("<style>@media screen and (prefers-color-scheme:dark){p{}}</style>"))
      .toBe("<style>@media screen and (min-width: 0px) {p{}}</style>");
    expect(forceDarkMedia("<style>@media (prefers-color-scheme: light) { p {} }</style>"))
      .toBe("<style>@media (max-width: -1px) { p {} }</style>");
    // A print query keeps its type, and another query is left as it was.
    expect(forceDarkMedia("<style>@media print and (prefers-color-scheme: dark) { p {} }</style>"))
      .toBe("<style>@media print and (min-width: 0px) { p {} }</style>");
    expect(forceDarkMedia("<style>@media (max-width: 600px) { p {} }</style>"))
      .toBe("<style>@media (max-width: 600px) { p {} }</style>");
    expect(forceDarkMedia('<style media="(prefers-color-scheme: dark)">p{}</style>'))
      .toBe('<style media="screen and (min-width: 0px)">p{}</style>');
  });

  it("makes no tag, whatever the email holds", () => {
    const hostile = '<style>@media (prefers-color-scheme: dark) and (x:"<script>") {}</style><p>@media (prefers-color-scheme: dark){</p>';
    const out = forceDarkMedia(hostile);
    expect(out.match(/</g)?.length).toBe(hostile.match(/</g)?.length);
  });

  it("runs only for the native look, after the sanitizer", () => {
    const src = readFileSync(join(__dirname, "../components/MessageContent.tsx"), "utf-8").replace(/\r\n/g, "\n");
    expect(src).toContain('purified && look === "native" ? { ...purified, clean: forceDarkMedia(purified.clean) } : purified');
    // The look reads what the frame draws: the sanitized markup.
    // Memoized on the markup, so a re-render does not scan the mail again.
    expect(src).toMatch(
      /const look = useMemo\(\s*\(\) => chooseBodyLook\(\{ dark, lightVersion, html: purified\?\.clean \?\? "" \}\),\s*\[dark, lightVersion, purified\],\s*\);/,
    );
  });
});

describe("email-dark-reinvert", () => {
  const css = bodyLookCss("invert", bodyPalette());

  it("inverts the document and re-inverts each picture element", () => {
    expect(css).toContain(`html { filter: ${INVERT_FILTER};`);
    for (const line of REINVERT_CSS.split("\n")) expect(css).toContain(line);
    const rule = REINVERT_CSS.split("\n")[0];
    for (const sel of ["img", "picture", "video", "canvas", "svg image"]) expect(rule, sel).toContain(sel);
    // Each island paints above the multiply layer, so it keeps the colours
    // of light mode (verifier F5).
    expect(rule).toContain(`{ filter: ${INVERT_FILTER}; position: relative; z-index: ${ISLAND_Z}; }`);
    expect(ISLAND_Z).toBeGreaterThan(LAYER_Z);
    expect(css).toContain(`mix-blend-mode: multiply; pointer-events: none; z-index: ${LAYER_Z}; }`);
  });

  it("makes a box with a background picture a light island", () => {
    // `<div style="background-image:url(x)"><p>Your invoice is attached</p></div>`,
    // `<td style="background:url(x)">` and `<table background="x">`.
    const [filter, backing] = REINVERT_CSS.split("\n");
    for (const sel of ['[style*="background-image" i]', '[style*="background" i][style*="url(" i]', "[background]"]) {
      expect(filter, sel).toContain(sel);
    }
    // The white backing, with no !important: an inline background-color of
    // the sender wins, and a bgcolor attribute is left alone.
    // Each alternative skips a box with its own `bgcolor` (fix round 3, P2-b).
    expect(backing).toBe(
      '[style*="background-image" i]:not([bgcolor]), [style*="background" i][style*="url(" i]:not([bgcolor]), ' +
        `[background]:not([bgcolor]) { background-color: ${cssColour({ r: 1, g: 1, b: 1 })}; }`,
    );
    expect(REINVERT_CSS).not.toContain("!important");
  });

  it("leaves a blocked remote picture alone, so its alt text stays light", () => {
    expect(bodyLookCss("invert", bodyPalette(), { remoteBlocked: true })).toContain(BLOCKED_REMOTE_CSS);
    expect(bodyLookCss("invert", bodyPalette(), { remoteBlocked: false })).not.toContain(BLOCKED_REMOTE_CSS);
    expect(BLOCKED_REMOTE_CSS).toBe('img[src^="http" i] { filter: none; }');
  });

  it("does not invert a picture or a box twice inside a re-inverted box", () => {
    const box = '[style*="background-image" i], [style*="background" i][style*="url(" i], [background]';
    expect(REINVERT_CSS.split("\n")[2]).toBe(
      `:is(${box}, picture) :is(img, picture, video, canvas, svg image, ${box}) { filter: none; }`,
    );
  });

  const drift = (c: { r: number; g: number; b: number }) => {
    const back = invertColour(invertColour(c));
    return Math.abs(back.r - c.r) + Math.abs(back.g - c.g) + Math.abs(back.b - c.b);
  };

  it("the filter is its own inverse, so a re-inverted picture looks as sent", () => {
    // Greys, skin tones and muted colours: the usual content of a photo or a logo.
    for (const c of [{ r: 0.5, g: 0.5, b: 0.5 }, { r: 0.8, g: 0.62, b: 0.5 }, { r: 0.3, g: 0.45, b: 0.55 }, { r: 0.1, g: 0.1, b: 0.12 }]) {
      expect(drift(c)).toBeLessThan(0.02);
    }
  });

  it("records the known limit: a vivid colour clips in the hue-rotate", () => {
    // The browser clamps each filter step to 0..1, and a hue-rotate of a vivid
    // red leaves that range. So a vivid colour in a photo shifts a little.
    // The light version is the member's way out (spec §16, recorded risk).
    expect(drift({ r: 0.9, g: 0.2, b: 0.1 })).toBeGreaterThan(0.02);
  });
});

describe("email-dark-base", () => {
  const card = parseColor(THEME.colors.dark.card) as Rgb;
  const near = (a: Rgb, b: Rgb) => Math.max(Math.abs(a.r - b.r), Math.abs(a.g - b.g), Math.abs(a.b - b.b));

  it("lands the email's white on the card colour, not on black", () => {
    const base = invertBase(card);
    expect(near(invertColour(base), card)).toBeLessThan(2 / 255);
    // The base is a light sheet, so the email's own black text still inverts to light.
    expect(Math.min(base.r, base.g, base.b)).toBeGreaterThan(0.8);
    // A layer multiplies the page by the base, so white (and each white box
    // of the email) becomes the base before the filter, and then the card.
    expect(bodyLookCss("invert", bodyPalette())).toContain(
      `html::after { content: ""; position: absolute; inset: 0; background: ${cssColour(base)}; ` +
        `box-shadow: 0 0 0 ${OVERLAY_SPREAD_PX}px ${cssColour(base)}; mix-blend-mode: multiply;`,
    );
    const white = { r: 1, g: 1, b: 1 };
    const multiplied = { r: white.r * base.r, g: white.g * base.g, b: white.b * base.b };
    expect(near(invertColour(multiplied), card)).toBeLessThan(2 / 255);
    // Black text stays black under the layer, so it still turns white.
    expect(near(invertColour({ r: 0, g: 0, b: 0 }), white)).toBeLessThan(1 / 255);
  });

  it("reads only the live accent, and falls back to THEME on a value it cannot parse", () => {
    const live = bodyPalette((name) => (name === "--primary" ? "hsl(280 70% 55%)" : "garbage"));
    expect(live.link).toEqual(parseColor("hsl(280 70% 55%)"));
    expect(live.card).toEqual(card);
    expect(bodyPalette(() => "garbage").link).toEqual(parseColor(THEME.colors.dark.primary));
  });

  // Fix round 3, P2-a. Mutation caught: a live read of the card got the LIGHT
  // white during a switch to dark, the base of white is black, and the page
  // drew as a blank white sheet.
  it("never takes the card, the ink or the border from the live page", () => {
    const white = "hsl(0 0% 100%)";
    const p = bodyPalette(() => white);
    expect(p.card).toEqual(card);
    expect(p.ink).toEqual(parseColor(THEME.colors.dark.cardForeground));
    expect(p.border).toEqual(parseColor(THEME.colors.dark.border));
    expect(p.muted).toEqual(parseColor(THEME.colors.dark.mutedForeground));
    // The base of the invert stays light, so the page lands on the card.
    const base = invertBase(p.card);
    expect(Math.min(base.r, base.g, base.b)).toBeGreaterThan(0.8);
  });

  it("gives simple HTML the card, the ink and the link tokens", () => {
    const p = bodyPalette();
    const css = bodyLookCss("tokens", p);
    expect(css).toContain(`html, body { background: ${cssColour(p.card)}; color: ${cssColour(p.ink)}; }`);
    expect(css).toContain(`a { color: ${cssColour(p.link)}; }`);
  });
});

describe("email-dark-print", () => {
  it("puts every rule of a dark look inside @media screen", () => {
    for (const look of ["native", "tokens", "invert"] as const) {
      const css = bodyLookCss(look, bodyPalette());
      expect(css.startsWith("@media screen {\n"), look).toBe(true);
      expect(css.endsWith("\n}"), look).toBe(true);
      // One block: nothing escapes it before the end.
      const depth = [...css].reduce((d, ch, i) => {
        const next = d + (ch === "{" ? 1 : ch === "}" ? -1 : 0);
        if (ch === "}" && next === 0 && i < css.length - 1) throw new Error(`${look} closes early at ${i}`);
        return next;
      }, 0);
      expect(depth).toBe(0);
    }
  });
});

describe("email-dark-no-content", () => {
  it("builds the CSS from the look and the tokens only", () => {
    // The CSS function never sees the HTML: its signature has no slot for it.
    expect(bodyLookCss.length).toBe(2);
    const src = readFileSync(join(__dirname, "../components/MessageContent.tsx"), "utf-8").replace(/\r\n/g, "\n");
    expect(src).toContain("const lookCss = bodyLookCss(look, palette, { remoteBlocked: !showImages });");
    // The palette is a dependency of the frame's document.
    expect(src).toContain("}, [sanitized, showImages, quoted, look, palette]);");
    expect(src).toContain("</style>${lookStyle}</head><body>${sanitized.clean}</body></html>");
    // The sandbox and the sanitizer stay as they were.
    expect(src).toContain('sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"');
    expect(src).toContain("return sanitizeEmailHtml(html, showImages);");
  });
});

describe("email-light-version", () => {
  it("toggles one id, keeps the newest, and reads junk as none", () => {
    expect(toggleId([], "a")).toEqual(["a"]);
    expect(toggleId(["a", "b"], "a")).toEqual(["b"]);
    const full = Array.from({ length: MAX_IDS }, (_, i) => `m${i}`);
    const next = toggleId(full, "new");
    expect(next).toHaveLength(MAX_IDS);
    expect(next[0]).toBe("m1");
    expect(next.at(-1)).toBe("new");
    expect(parseIds(null)).toEqual([]);
    expect(parseIds("{not json")).toEqual([]);
    expect(parseIds('{"a":1}')).toEqual([]);
    expect(parseIds('["a", 2, "b"]')).toEqual(["a", "b"]);
  });

  it("wraps each storage read and write in a try block", () => {
    const src = readFileSync(join(__dirname, "lightVersion.ts"), "utf-8");
    const reads = src.match(/window\.localStorage\.\w+Item/g) ?? [];
    expect(reads).toHaveLength(2);
    for (const call of ["window.localStorage.getItem", "window.localStorage.setItem"]) {
      const at = src.indexOf(call);
      const tryAt = src.lastIndexOf("try {", at);
      // The call sits in the try block, and its catch follows close behind.
      expect(tryAt, call).toBeGreaterThan(-1);
      expect(src.slice(tryAt, at).includes("}"), call).toBe(false);
      expect(src.indexOf("} catch", at) - at, call).toBeLessThan(160);
    }
  });
});

describe("email-dark-linear", () => {
  const time = (fn: () => unknown) => {
    const start = performance.now();
    fn();
    return performance.now() - start;
  };
  const fill = (unit: string, bytes: number) => unit.repeat(Math.ceil(bytes / unit.length)).slice(0, bytes);
  const MB2 = 2 * 1024 * 1024;
  const UNDER = CLASSIFY_LIMIT - 1024;

  it("classifies 2 MB of crafted CSS in under 100 ms", () => {
    for (const unit of ["color-scheme: x ", "@media x "]) {
      const html = fill(unit, MB2);
      let rule = "";
      expect(time(() => (rule = classifyEmailHtml(html))), unit).toBeLessThan(100);
      // Over the limit the style scan is skipped, and the mail is inverted.
      expect(rule).toBe("invert");
    }
  });

  it("scans each pattern in linear time just under the limit", () => {
    for (const unit of ["color-scheme: x ", "@media x ", "<a x ", "color   ", "background:", "<style x "]) {
      const html = fill(unit, UNDER);
      expect(time(() => classifyEmailHtml(html)), unit).toBeLessThan(100);
    }
  });

  it("rewrites 2 MB of crafted media queries in under 100 ms", () => {
    for (const unit of ["@media x ", "<style media=x ", "@media (prefers-color-scheme: dark) "]) {
      const html = fill(unit, MB2);
      expect(time(() => forceDarkMedia(html)), unit).toBeLessThan(100);
    }
  });
});
