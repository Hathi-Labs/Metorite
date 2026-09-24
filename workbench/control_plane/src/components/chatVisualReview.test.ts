/**
 * WS-27bm S8 fix round 2 — the visual review of the Projects chat, as
 * assertions that need no screenshot.
 *
 * Each block names the defect it fences. The layout ones that CSS alone
 * decides (the phone header, the narrow card) are pinned by source, and are
 * weaker than a look. The rest are behaviour.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import GenerativeUINode, { TONE_HUE } from "@/components/GenerativeUINode";
import { displayableState } from "@/components/GenerativeUIPanel";
import { barColor } from "@/components/genUITemplates";
import { ACCENT_HUES } from "@/lib/statusAccent";
import { AA_LARGE_TEXT, AA_NORMAL_TEXT, contrast } from "@/lib/theme/contrast";
import { THEME } from "@/lib/theme/themes";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

// ── Defect 1 — prose that follows the theme ────────────────────────────────

describe("prose follows the theme", () => {
  const files = sources(SRC).map((path) => ({ path, src: readFileSync(path, "utf8") }));

  it("no file uses prose-invert", () => {
    // `prose-invert` hard-codes dark text colours. This app turns light with
    // the `.light` class, not with the OS setting a `dark:` variant reads, so
    // no qualifier makes it right. `.cc-prose` is the answer.
    const offenders = files.filter((f) => /\bprose-invert\b/.test(f.src)).map((f) => f.path);
    expect(offenders).toEqual([]);
  });

  it("every prose class list also carries cc-prose", () => {
    const offenders: string[] = [];
    for (const f of files) {
      for (const [attr] of f.src.matchAll(/className=(?:"[^"]*"|\{`[^`]*`\})/g)) {
        if (/(^|[\s"`])prose(\s|"|`)/.test(attr) && !attr.includes("cc-prose")) {
          offenders.push(`${f.path}: ${attr.slice(0, 60)}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("code in prose is foreground ink, never the accent (fix round 4)", () => {
    // `prose-code:text-accent` drew "make flash" in pale orange on light grey.
    const offenders: string[] = [];
    for (const f of files) {
      for (const [cls] of f.src.matchAll(/prose-(?:code|pre):text-[a-z-]+/g)) {
        const isSize = /:text-(xs|sm|base|lg|\d*xl)$/.test(cls);
        if (!isSize && !cls.endsWith(":text-foreground")) {
          offenders.push(`${f.path}: ${cls}`);
        }
      }
    }
    expect(offenders).toEqual([]);
    const css = read("app/globals.css");
    expect(css).toContain("--tw-prose-code: var(--foreground)");
    expect(css).toContain("--tw-prose-pre-code: var(--foreground)");
  });

  it("inline code in prose has no quote marks (fix round 5)", () => {
    const css = read("app/globals.css");
    expect(css).toMatch(
      /\.cc-prose :where\(code\)::before,\s*\.cc-prose :where\(code\)::after \{\s*content: none;/,
    );
    const pill = css.slice(css.indexOf(".cc-prose :where(:not(pre) > code) {"));
    expect(pill.slice(0, pill.indexOf("}"))).toContain("background: var(--secondary)");
  });

  it("globals.css points the prose colours at tokens", () => {
    const css = read("app/globals.css");
    const block = css.slice(css.indexOf(".cc-prose {"), css.indexOf("}", css.indexOf(".cc-prose {")));
    expect(block).toContain("--tw-prose-body: var(--foreground)");
    expect(block).toContain("--tw-prose-links: var(--primary)");
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b|hsl\(|rgb\(/);
  });
});

// ── Defect 2 — the board keeps its width ───────────────────────────────────

describe("the Projects page wiring", () => {
  const page = read("app/projects/page.tsx");

  it("the board never draws over its neighbours", () => {
    expect(page).toContain('<main className="flex min-w-0 flex-1 flex-col overflow-hidden">');
  });

  it("the page measures the row and provides the answer to the chat", () => {
    expect(page).toContain("<SidePanelFitContext.Provider value={panelFits}>");
    expect(page).toContain("boardMinPx: BOARD_MIN_REM * remPx");
  });

  it("the chat's card falls back to the viewer when the panel does not fit", () => {
    expect(read("components/MessageBubble.tsx")).toMatch(/onOpenInSidePanel=\{\s*panelFits\s*\?/);
    expect(read("app/projects/components/AssistantRail.tsx")).toContain(
      "artifactHandler({ sessionId: activeId, isMobile, panelFits })",
    );
  });
});

// ── Defect 3 — the markdown node is real Markdown ──────────────────────────

describe("the generative-UI markdown node", () => {
  const html = renderToStaticMarkup(
    createElement(GenerativeUINode, {
      spec: {
        type: "markdown",
        props: { text: "- one\n- two\n\n| Task | State |\n|---|---|\n| Keys | done |\n" },
      },
    }),
  );

  it("renders a list with bullets", () => {
    expect(html).toMatch(/<ul class="[^"]*list-disc[^"]*">\s*<li/);
  });

  it("renders a GFM table with cells, in a scroll box", () => {
    expect(html).toContain("<table");
    expect(html).toMatch(/<td[^>]*>Keys<\/td>/);
    expect(html).toMatch(/overflow-x-auto[^>]*><table/);
  });
});

// ── Defect 4 — no empty "Interactive view" fold ────────────────────────────

describe("displayableState", () => {
  it("ignores the chat's own bookkeeping keys", () => {
    expect(displayableState({ segments: [{ id: "s", text: "hi" }] })).toBeNull();
    expect(displayableState({ todos: [] })).toBeNull();
    expect(displayableState({ segments: [], todos: [] })).toBeNull();
    expect(displayableState(undefined)).toBeNull();
  });

  it("keeps real state, without the bookkeeping", () => {
    expect(displayableState({ segments: [], progress: 3 })).toEqual({ progress: 3 });
  });

  it("the fold wears tokens, not the sky palette", () => {
    expect(read("components/GenerativeUIPanel.tsx")).not.toMatch(/\bsky-\d/);
  });
});

// ── Defect 5 — the primitives read in both modes ───────────────────────────

describe("the generative-UI primitives", () => {
  const src = read("components/GenerativeUINode.tsx");

  it("carry no raw palette class and no hex", () => {
    expect(src).not.toMatch(/\b(emerald|sky|amber|red|zinc|blue)-\d{2,3}\b/);
    expect(src).not.toMatch(/#[0-9a-fA-F]{6}\b/);
  });

  it("map every tone onto a status hue", () => {
    for (const hue of Object.values(TONE_HUE)) expect(ACCENT_HUES).toContain(hue);
  });

  it("draw a button with the one Button primitive", () => {
    const html = renderToStaticMarkup(
      createElement(GenerativeUINode, {
        spec: { type: "button", props: { label: "Approve", action: "ok", tone: "primary" } },
        onAction: () => {},
      }),
    );
    expect(html).toContain("cc-control");
    expect(html).toContain("bg-primary");
  });

  it("draw a badge from statusAccent's chip", () => {
    const html = renderToStaticMarkup(
      createElement(GenerativeUINode, { spec: { type: "badge", props: { text: "At risk", tone: "warning" } } }),
    );
    // Fix round 4: the words are foreground ink, the hue is the tint and dot.
    expect(html).toContain("text-foreground bg-warning/10");
    expect(html).toContain("bg-warning\"");
    expect(html).not.toContain("text-warning");
  });

  it("does not draw badge words in a status colour a light card cannot carry", () => {
    // Why: the manifest's own numbers. Warning ink on a light card fails even
    // the large-text threshold, so it must not carry a badge's words.
    const light = THEME.colors.light;
    expect(contrast(light.warning, light.card)!).toBeLessThan(AA_LARGE_TEXT);
    expect(contrast(light.foreground, light.card)!).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    for (const tone of Object.keys(TONE_HUE)) {
      const html = renderToStaticMarkup(
        createElement(GenerativeUINode, { spec: { type: "badge", props: { text: "x", tone } } }),
      );
      expect(html, tone).toContain("text-foreground");
    }
  });
});

// ── Defect 6 — whole timeline dots ─────────────────────────────────────────

describe("the Timeline template", () => {
  it("scrolls on an outer box and draws the line inside it", () => {
    const src = read("components/genUITemplates.tsx");
    const body = src.slice(src.indexOf("function Timeline("), src.indexOf("function TaskChip("));
    expect(body).toContain('<div data-timeline-scroll style={{ maxHeight: 420, overflowY: "auto" }}>');
    // The line's own box must not clip: no overflow on it.
    expect(body).toMatch(/borderLeft: "2px solid var\(--secondary\)", marginLeft: 6, paddingLeft: 14 \}\}>/);
  });
});

// ── Defect 8 — the narrow rail and the phone ───────────────────────────────

describe("narrow surfaces", () => {
  it("the artifact card hides the folder and hash below @md, and wraps the name", () => {
    const src = read("components/ArtifactCard.tsx");
    expect(src).toContain("@container");
    expect(src).toMatch(/hidden @md:inline text-\[10px\] text-muted-foreground\/70 font-mono/);
    expect(src).toContain("break-all line-clamp-2");
  });

  it("the viewer's header wraps its actions to a second row on a phone", () => {
    const src = read("components/ArtifactViewerModal.tsx");
    expect(src).toContain("order-3 flex w-full flex-wrap items-center gap-2 sm:order-2 sm:w-auto");
  });

  it("a chat table keeps words whole and scrolls with a visible bar (fix round 5)", () => {
    const html = renderToStaticMarkup(
      createElement(GenerativeUINode, {
        spec: {
          type: "markdown",
          props: { text: "| Project | Owner |\n|---|---|\n| Firmware | Ravi Kumar Subramanian |\n| App | Asha |\n" },
        },
      }),
    );
    // The box scrolls, shows a bar, and sizes its cells from itself.
    expect(html).toMatch(/class="@container [^"]*overflow-x-auto[^"]*scrollbar-thin/);
    const cells = [...html.matchAll(/<t[hd] class="([^"]*)"/g)].map((m) => m[1]);
    expect(cells.length).toBe(6);
    for (const cls of cells) {
      // Whole words: break-word keeps the min-content width, anywhere does not.
      expect(cls).toContain("break-words");
      expect(cls).not.toContain("wrap-anywhere");
    }
    // Row lines on every body cell, the last column included.
    for (const cls of cells.slice(2)) expect(cls).toContain("border-t");
    expect(read("components/MarkdownMessage.tsx")).not.toMatch(/className="[^"]*wrap-anywhere/);
  });
});

// ── The minor findings ─────────────────────────────────────────────────────

describe("the minor findings", () => {
  it("an unchecked task-list box is an icon, not the browser's control", () => {
    const html = renderToStaticMarkup(
      createElement(GenerativeUINode, { spec: { type: "markdown", props: { text: "- [ ] Review\n- [x] Draft" } } }),
    );
    expect(html).not.toContain('type="checkbox"');
    expect(html).toContain('aria-label="Not done"');
    expect(html).toMatch(/role="img"[^>]*aria-label="Not done"|aria-label="Not done"[^>]*role="img"/);
    expect(html).toContain('aria-label="Done"');
  });

  it("a plain bar takes a categorical slot, never the member's accent", () => {
    expect(barColor(undefined)).toBe("var(--cat-1)");
    expect(barColor("warning")).toBe("var(--warning)");
    expect(barColor("nonsense")).toBe("var(--cat-1)");
  });

  it("adds no second colour-scheme mechanism (fix round 4)", () => {
    // next-themes already sets `color-scheme` on <html> when the mode
    // changes. Round 2 added a CSS rule for it, a second mechanism, and a
    // real light-mode capture showed the date icons were fine without it.
    expect(read("app/globals.css")).not.toMatch(/color-scheme:\s*(light|dark)\s*;/);
  });
});
