/**
 * The chat's Markdown with entity pills, rendered for real (WS-27bm S9, spec
 * §15). The last case is the owner's own answer of 2026-09-24, drawn through
 * `MarkdownMessage` with the tool results that answer came from.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import MarkdownMessage, { LINK_CLASS, MarkdownBody } from "@/components/MarkdownMessage";
import { buildEntityIndex } from "@/lib/entityIndex";
import {
  OWNER_ANSWER,
  OWNER_TOOL_EVENTS,
  PROJECT_ID,
  TASK3_ID,
  TASK5_ID,
} from "@/lib/entityPills.fixture";

function body(content: string, entityPills = true): string {
  return renderToStaticMarkup(
    createElement(MarkdownBody, {
      content,
      entityPills,
      entityIndex: entityPills ? buildEntityIndex(OWNER_TOOL_EVENTS) : undefined,
    }),
  );
}

/** Every anchor's opening tag, so a test can read its attributes. */
const anchors = (html: string) => html.match(/<a\b[^>]*>/g) ?? [];

describe("MarkdownBody with pills on", () => {
  it("draws a pill for a fenced name, and never the marks", () => {
    const html = body("See «Projects/Tasks App».");
    expect(html).toContain(`href="/projects?project=${PROJECT_ID}"`);
    expect(html).not.toMatch(/[«»]/);
  });

  it("draws no pill in code", () => {
    const html = body("Run `«x»` and\n\n```\n«y»\n```");
    expect(html).toContain("«x»");
    expect(html).toContain("«y»");
    expect(html).not.toContain("Open ");
  });

  it("turns an email into a person pill, not a mailto link", () => {
    const html = body("assigned to vjvarada@hathilabs.com");
    expect(html).not.toContain("mailto:");
    expect(html).toContain('title="vjvarada · vjvarada@hathilabs.com"');
  });

  it("puts the space back after a full stop", () => {
    expect(body("today.**Early stages** are done")).toContain("today. <strong");
  });

  it("puts no space between a pill and the punctuation after it (S9 visual review)", () => {
    const html = body("In «Hathi Labs», the task «Board drag and drop». Due: «Zed»: soon");
    // Each pill's closing tag is followed by its punctuation at once.
    expect(html).toMatch(/<\/a>,<\/span> the task/);
    expect(html).toMatch(/<\/a>\.<\/span> Due/);
    expect(html).toMatch(/<\/span>:<\/span> soon/);
    expect(html).not.toMatch(/<\/(?:a|span)>\s+[,.;:!?]/);
    // And the two stay on one line.
    expect(html).toContain('<span class="whitespace-nowrap"><a ');
  });
});

describe("MarkdownBody with pills off", () => {
  it("leaves the marks as text, as a document needs", () => {
    expect(body("See «Projects/Tasks App».", false)).toContain("«Projects/Tasks App»");
  });
});

describe("the link component", () => {
  it("opens an in-app path in this tab", () => {
    const [a] = anchors(body("[the board](/projects?project=abc)"));
    expect(a).toContain('href="/projects?project=abc"');
    expect(a).not.toContain("target=");
  });

  it("opens any other URL in a new tab, without an opener", () => {
    const [a] = anchors(body("[docs](https://example.com/x)"));
    expect(a).toContain('target="_blank"');
    expect(a).toContain('rel="noopener noreferrer"');
  });

  it("treats //host and /api/ as not in-app", () => {
    for (const href of ["//evil.example/x", "/api/agent/workspace/s/file?path=a"]) {
      const [a] = anchors(body(`[x](${href})`));
      expect(a).toContain('target="_blank"');
    }
  });

  it("uses the primary token and no palette blue", () => {
    expect(LINK_CLASS).toContain("text-primary");
    expect(LINK_CLASS).not.toMatch(/blue|#[0-9a-f]{3}/i);
    expect(body("[docs](https://example.com)", false)).not.toMatch(/text-blue/);
  });
});

describe("the owner's answer, end to end", () => {
  const html = renderToStaticMarkup(
    createElement(MarkdownMessage, {
      content: OWNER_ANSWER,
      toolEvents: OWNER_TOOL_EVENTS,
      entityPills: true,
    }),
  );
  // The answer body only. The thinking container above it lists the tools.
  const answer = html.slice(html.indexOf("Here is where"));

  it("links #5 and #3 to their tasks", () => {
    expect(answer).toContain(`href="/projects?task=${TASK5_ID}"`);
    expect(answer).toContain(`href="/projects?task=${TASK3_ID}"`);
    expect(answer).toContain(
      'aria-label="Open task #5 Notification engine for projects in Projects, status To do"',
    );
  });

  it("links the project", () => {
    expect(answer).toContain(`href="/projects?project=${PROJECT_ID}"`);
    expect(answer).toContain('aria-label="Open project Projects/Tasks App in Projects"');
  });

  it("draws the email as a person pill", () => {
    expect(answer).toContain('title="vjvarada · vjvarada@hathilabs.com"');
    expect(answer).toContain(">VJ<");
  });

  it("leaves no marks, no mailto, no bold name and no missing space", () => {
    expect(answer).not.toMatch(/[«»]/);
    expect(answer).not.toContain("mailto:");
    expect(answer).not.toMatch(/<strong[^>]*>[^<]*(Projects\/Tasks App|Notification engine)/);
    expect(answer).toContain("today. <strong");
  });
});
