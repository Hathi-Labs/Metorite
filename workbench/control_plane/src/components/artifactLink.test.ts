/**
 * A link to a workspace file in a chat reply opens the app's viewer, and it
 * never opens the raw file in a new tab (owner report, 2026-10-16).
 *
 * THE DEFECT this fences: the agent pasted its `download_url`,
 * `/api/agent/workspace/<sid>/file?path=outputs/report.md`, into the reply.
 * `MarkdownLink` drew every `/api/` link as an external link with
 * `target="_blank"`, and the gateway serves `.md` as `text/markdown` inline.
 * So the browser showed the raw Markdown text.
 *
 * `vitest.config.ts` runs in `node`, with no DOM. So a "click" here walks the
 * element tree that `MarkdownBody` returns, as `markdownImage.test.ts` does.
 * `ArtifactFileLink` holds hooks, so the walk draws its hook-free half,
 * `ArtifactFileAnchor`, with the openers of the test. Then it calls the
 * `onClick` of the real `<a>` that `ControlLink` draws.
 *
 * Mutation: drop the `workspaceFileOfHref` branch from `MarkdownLink`, and
 * the link draws `target="_blank"` to `/api/` again. These tests fail.
 */
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import type { FileEntry } from "@/components/ArtifactSidebar";
import ArtifactFileLink, {
  ArtifactFileAnchor,
  ArtifactOpenContext,
  openFileLink,
  type ArtifactOpeners,
} from "@/components/ArtifactLink";
import { ControlLink } from "@/components/ControlLink";
import { MarkdownBody } from "@/components/MarkdownMessage";
import {
  artifactOpenSurface,
  artifactViewUrl,
  workspaceFileOfHref,
  workspaceFileUrl,
} from "@/lib/artifactKind";

const SID = "0f8fad5b-d9cb-469f-a165-70867728950e";
/** The `download_url` as `write_artifact` returns it: the path not encoded. */
const TOOL_URL = `/api/agent/workspace/${SID}/file?path=outputs/report.md`;
const REPLY = `The report is ready: [report.md](${TOOL_URL})`;
const VIEW = artifactViewUrl(SID, "outputs/report.md");

// ─── The tree walk ─────────────────────────────────────────────────────────

interface Walked {
  /** Each `ArtifactFileLink` that the reply draws, with its props. */
  links: Array<{ file: { sessionId: string; path: string; name: string } }>;
}

/** Expand the function components of a tree, and collect the file links. */
function walk(node: ReactNode, out: Walked = { links: [] }): Walked {
  if (Array.isArray(node)) {
    node.forEach((n) => walk(n, out));
    return out;
  }
  if (!isValidElement(node)) return out;
  const el = node as ReactElement<Record<string, unknown>>;
  if (el.type === ArtifactFileLink) {
    out.links.push(el.props as Walked["links"][number]);
    return out;
  }
  if (typeof el.type === "function") {
    let rendered: ReactNode;
    try {
      rendered = (el.type as (p: unknown) => ReactNode)(el.props);
    } catch {
      return out; // a component with hooks, which this test does not need
    }
    return walk(rendered, out);
  }
  return walk(el.props.children as ReactNode, out);
}

interface FakeClick {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  defaultPrevented: boolean;
  preventDefault: ReturnType<typeof vi.fn>;
  stopPropagation: ReturnType<typeof vi.fn>;
}

function click(over: Partial<FakeClick> = {}): FakeClick {
  return {
    button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
    defaultPrevented: false, preventDefault: vi.fn(), stopPropagation: vi.fn(), ...over,
  };
}

/**
 * Draw the anchor of one file link with these openers, and return the real
 * `<a>` that `ControlLink` draws. `ControlLink` is a `forwardRef`, so its
 * `render` is called by hand.
 */
function anchorOf(
  link: Walked["links"][number],
  openers: ArtifactOpeners | null,
  isMobile: boolean,
  navigate: (href: string) => void,
): ReactElement<{ href: string; target?: string; onClick: (e: FakeClick) => void }> {
  const control = ArtifactFileAnchor({ ...link, openers, isMobile, navigate }) as ReactElement<
    Record<string, unknown>
  >;
  expect(control.type).toBe(ControlLink);
  const render = (ControlLink as unknown as { render: (p: unknown, r: unknown) => ReactElement })
    .render;
  return render(control.props, null) as ReturnType<typeof anchorOf>;
}

function openers(over: Partial<ArtifactOpeners> = {}) {
  return {
    sessionId: SID,
    openModal: vi.fn<(e: FileEntry) => void>(),
    openPanel: vi.fn<(e: FileEntry) => void>(),
    ...over,
  };
}

// ─── The reply ─────────────────────────────────────────────────────────────

describe("a pasted workspace file link in a reply", () => {
  it("draws as an in-app link to the rendered view, never to the raw /api/ file", () => {
    const html = renderToStaticMarkup(
      createElement(
        ArtifactOpenContext.Provider,
        { value: openers() },
        createElement(MarkdownBody, { content: REPLY, fences: true }),
      ),
    );
    const anchor = html.match(/<a\b[^>]*>/)?.[0] ?? "";
    expect(anchor).toContain(`href="${VIEW.replace(/&/g, "&amp;")}"`);
    expect(anchor).not.toContain("/api/agent/workspace");
    expect(anchor).not.toContain('target="_blank"');
    expect(anchor).not.toContain("data-external-link");
  });

  it("a plain click on desktop opens the side panel, and does not navigate", () => {
    const { links } = walk(MarkdownBody({ content: REPLY, fences: true }));
    expect(links).toHaveLength(1);
    expect(links[0].file).toEqual({ sessionId: SID, path: "outputs/report.md", name: "report.md" });
    const o = openers();
    const navigate = vi.fn();
    const a = anchorOf(links[0], o, false, navigate);
    expect(a.props.href).toBe(VIEW);
    expect(a.props.target).toBeUndefined();
    const e = click();
    a.props.onClick(e);
    expect(e.preventDefault).toHaveBeenCalled();
    expect(o.openPanel).toHaveBeenCalledWith(
      expect.objectContaining({ path: "outputs/report.md", name: "report.md" }),
    );
    expect(o.openModal).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("a plain click on a phone opens the full viewer", () => {
    const { links } = walk(MarkdownBody({ content: REPLY, fences: true }));
    const o = openers();
    const navigate = vi.fn();
    const e = click();
    anchorOf(links[0], o, true, navigate).props.onClick(e);
    expect(e.preventDefault).toHaveBeenCalled();
    expect(o.openModal).toHaveBeenCalledTimes(1);
    expect(o.openPanel).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("a modified click is left to the browser, which opens the rendered view", () => {
    const { links } = walk(MarkdownBody({ content: REPLY, fences: true }));
    for (const mod of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { button: 1 }]) {
      const o = openers();
      const a = anchorOf(links[0], o, false, vi.fn());
      const e = click(mod);
      a.props.onClick(e);
      expect(e.preventDefault).not.toHaveBeenCalled();
      expect(o.openPanel).not.toHaveBeenCalled();
      expect(o.openModal).not.toHaveBeenCalled();
      expect(a.props.href).toBe(VIEW);
    }
  });

  it("a file of another chat goes to the rendered view, in this tab", () => {
    const { links } = walk(MarkdownBody({ content: REPLY, fences: true }));
    const o = openers({ sessionId: "another-chat" });
    const navigate = vi.fn();
    anchorOf(links[0], o, false, navigate).props.onClick(click());
    expect(navigate).toHaveBeenCalledWith(VIEW);
    expect(o.openPanel).not.toHaveBeenCalled();
    expect(o.openModal).not.toHaveBeenCalled();
  });

  it("a link with no chat around it goes to the rendered view", () => {
    const navigate = vi.fn();
    const file = workspaceFileOfHref(TOOL_URL, "https://app.test")!;
    expect(openFileLink(file, null, false, navigate)).toBe("page");
    expect(navigate).toHaveBeenCalledWith(VIEW);
  });

  it("an HTML or React file opens the panel, and an image the full viewer", () => {
    const o = openers();
    const ref = (path: string) => workspaceFileOfHref(workspaceFileUrl(SID, path), "https://app.test")!;
    expect(openFileLink(ref("outputs/page.html"), o, false, vi.fn())).toBe("panel");
    expect(openFileLink(ref("outputs/App.jsx"), o, false, vi.fn())).toBe("panel");
    expect(openFileLink(ref("outputs/chart.png"), o, false, vi.fn())).toBe("modal");
    expect(openFileLink(ref("outputs/a.md"), { ...o, openPanel: undefined }, false, vi.fn())).toBe("modal");
  });

  it("an ordinary link keeps its old rules", () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownBody, { content: "[site](https://example.com/x)", fences: true }),
    );
    expect(html).toContain('target="_blank"');
    expect(html).not.toContain("data-artifact-link");
  });
});

// ─── The parser ────────────────────────────────────────────────────────────

describe("workspaceFileOfHref", () => {
  const ORIGIN = "https://app.test";

  it("reads the tool's address and the address the app builds", () => {
    expect(workspaceFileOfHref(TOOL_URL, ORIGIN)).toEqual({
      sessionId: SID, path: "outputs/report.md", name: "report.md",
    });
    expect(workspaceFileOfHref(workspaceFileUrl(SID, "outputs/a b (1).md"), ORIGIN)?.path).toBe(
      "outputs/a b (1).md",
    );
    expect(workspaceFileOfHref(`${ORIGIN}${TOOL_URL}`, ORIGIN)?.path).toBe("outputs/report.md");
    expect(workspaceFileOfHref(workspaceFileUrl(SID, "outputs/a.md", { download: true }), ORIGIN)?.path)
      .toBe("outputs/a.md");
  });

  it("refuses a PDF link, another origin, and each odd form", () => {
    for (const href of [
      workspaceFileUrl(SID, "outputs/a.md", { pdf: true }),
      `https://evil.example${TOOL_URL}`,
      `//evil.example${TOOL_URL}`,
      `/\\evil.example${TOOL_URL}`,
      `/api/agent/workspace/${SID}/file`,
      `/api/agent/workspace/${SID}/file?path=`,
      `/api/agent/workspace/a.b/file?path=x.md`,
      `/api/agent/workspace/a%2Fb/file?path=x.md`,
      `/api/agent/workspace/${SID}/files?path=x.md`,
      `/api/agent/workspace/${SID}/file\t?path=x.md`,
      "/projects?task=1",
      undefined,
    ]) {
      expect(workspaceFileOfHref(href, ORIGIN), String(href)).toBeNull();
    }
  });
});

describe("artifactOpenSurface — the card and the link read one rule", () => {
  it("opens a document in the panel on a desktop that has one", () => {
    expect(artifactOpenSurface("a.md", "outputs/a.md", { isMobile: false, panel: true })).toBe("panel");
    expect(artifactOpenSurface("a.md", "outputs/a.md", { isMobile: true, panel: true })).toBe("modal");
    expect(artifactOpenSurface("a.md", "outputs/a.md", { isMobile: false, panel: false })).toBe("modal");
    expect(artifactOpenSurface("a.pdf", "outputs/a.pdf", { isMobile: false, panel: true })).toBe("modal");
  });
});
