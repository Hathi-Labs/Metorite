/**
 * A chat link: each in-app click announces itself, and a link to another site
 * shows where it goes (review round 1, P2-a and P2-b).
 *
 * Mutations caught: the chat link stops announcing a click; the email reader
 * stops listening; an external link draws with no host; an in-app link draws
 * an external mark; a subject with brackets breaks out of the agent's link.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { MarkdownBody } from "@/components/MarkdownMessage";
import { IN_APP_LINK_EVENT, externalHost } from "./inAppLink";

const md = (content: string) => renderToStaticMarkup(createElement(MarkdownBody, { content }));
const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf-8");

describe("externalHost", () => {
  it("names the host of an http(s) link only", () => {
    expect(externalHost("https://evil.example/x?y=1")).toBe("evil.example");
    expect(externalHost("http://a.test")).toBe("a.test");
    expect(externalHost("/email?email=x")).toBeNull();
    expect(externalHost("mailto:a@b.test")).toBeNull();
    expect(externalHost("javascript:alert(1)")).toBeNull();
    expect(externalHost(undefined)).toBeNull();
  });
});

describe("the chat draws a link by where it goes", () => {
  it("a link to another site shows its host, whatever its words say", () => {
    const html = md("[Open in inbox](https://evil.example/login)");
    expect(html).toContain('data-external-link="evil.example"');
    expect(html).toMatch(/Open in inbox<span[^>]*title="https:\/\/evil\.example\/login"/);
    expect(html).toContain(">evil.example</span>");
  });

  it("a bare URL keeps the icon and does not print its host twice", () => {
    const html = md("<https://a.test/x>");
    expect(html).toContain('data-external-link="a.test"');
    expect(html).not.toContain(">a.test</span>");
  });

  it("an in-app link has no external mark", () => {
    const html = md("[BQ quote](/email?email=0f8fad5b-d9cb-469f-a165-70867728950e)");
    expect(html).not.toContain("data-external-link");
    expect(html).toContain('href="/email?email=0f8fad5b-d9cb-469f-a165-70867728950e"');
  });

  it("an escaped subject stays the words of one link", () => {
    // The shape `_md_link` in the email agent prints.
    const html = md("[Re: \\[Open\\]\\(https://evil.example\\) quote](/email?email=0f8fad5b-d9cb-469f-a165-70867728950e)");
    expect(html).not.toContain("data-external-link");
    expect(html.match(/<a /g)?.length).toBe(1);
  });
});

describe("each in-app click announces itself", () => {
  it("the chat link sends the event before it pushes, and the email reader listens", () => {
    expect(read("../components/MarkdownMessage.tsx")).toMatch(
      /onActivate=\{\(\) => \{ announceInAppLink\(href\); router\.push\(href\); \}\}/);
    const reader = read("../app/email/components/EmailDeepLink.tsx");
    expect(reader).toMatch(/addEventListener\(IN_APP_LINK_EVENT, onLink\)/);
    expect(IN_APP_LINK_EVENT).toBe("cc-in-app-link");
  });
});
