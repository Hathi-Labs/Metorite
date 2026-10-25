// The Assistant pane view draws every state (WS-63 PZ-7). A pure view, so
// `renderToStaticMarkup` is enough: vitest runs in node with no DOM.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AssistantPaneView, NO_HANDLERS, type AssistantPaneViewProps } from "./AssistantPane";
import { CAPS, EMPTY_PROFILE, type Profile } from "./lib/profile";

const base: AssistantPaneViewProps = {
  ...NO_HANDLERS,
  status: "ready",
  profile: EMPTY_PROFILE,
  busy: {},
  errors: {},
  confirmForget: false,
  onCancelForget: () => {},
  onConfirmForget: () => {},
};
const draw = (over: Partial<AssistantPaneViewProps>) =>
  renderToStaticMarkup(createElement(AssistantPaneView, { ...base, ...over }));

const full: Profile = {
  v: 1,
  learning: false,
  address: { name: "Vijay", formality: "casual" },
  style: { length: "short", notes: ["Answer first"] },
  aliases: [{ id: "a1", alias: "Mum", email: "mum@example.com" }],
  facts: [
    { id: "f1", text: "I work in Bengaluru", kind: "work", source: "chat", at: "2026-10-23T11:00:00Z" },
    { id: "f2", text: "I run on Mondays", kind: "routine", source: "settings", at: "2026-10-20T08:00:00Z" },
  ],
};

describe("AssistantPaneView", () => {
  it("draws a skeleton while loading", () => {
    const html = draw({ status: "loading" });
    expect(html).toContain("assistant-loading");
    expect(html).not.toContain("Forget everything");
  });

  it("draws an error with Retry, and never the empty form", () => {
    const html = draw({ status: "error", loadError: "The assistant profile is not available now." });
    expect(html).toContain("Retry");
    expect(html).toContain("not available now");
    expect(html).not.toContain("Add fact");
  });

  it("draws friendly empty lines", () => {
    const html = draw({});
    expect(html).toContain("Nothing yet.");
    expect(html).toContain("No names yet.");
    expect(html).toContain("No notes yet.");
    expect(html).toContain(`0 of ${CAPS.facts} facts`);
  });

  it("draws facts with kind, source and date, and aliases with no source", () => {
    const html = draw({ profile: full });
    expect(html).toContain("I work in Bengaluru");
    expect(html).toContain("Work");
    expect(html).toContain("Learned in chat");
    expect(html).toContain("2026-10-23");
    expect(html).toContain("Added in Settings");
    expect(html).toContain("Mum");
    expect(html).toContain("mum@example.com");
    expect(html).toContain("Answer first");
    expect(html).toContain("2 of 40 facts");
  });

  it("says that off stops writing and the assistant still reads", () => {
    const html = draw({ profile: full });
    expect(html).toContain("stops writing");
    expect(html).toContain("still reads");
  });

  it("shows a row error and a per-row busy state", () => {
    const html = draw({ profile: full, errors: { f1: "That item is gone." }, busy: { f2: true } });
    expect(html).toContain("That item is gone.");
    expect(html).toContain('role="alert"');
  });

  it("disables add at the caps", () => {
    const facts = Array.from({ length: CAPS.facts }, (_, i) => ({
      id: `f${i}`, text: `fact ${i}`, kind: "about" as const,
    }));
    const html = draw({ profile: { ...EMPTY_PROFILE, facts } });
    expect(html).toContain("You have 40 facts. Delete one to add another.");
    const aliases = Array.from({ length: CAPS.aliases }, (_, i) => ({
      id: `a${i}`, alias: `n${i}`, email: `n${i}@example.com`,
    }));
    expect(draw({ profile: { ...EMPTY_PROFILE, aliases } })).toContain("You have 50 names.");
  });

  it("offers the download as JSON and as text, with its own error (PZ-9)", () => {
    const html = draw({ profile: full });
    expect(html).toContain("Download my profile");
    expect(html).toContain("Download as JSON");
    expect(html).toContain("Download as text");
    expect(html).toContain("nothing about anybody else");
    const failed = draw({ errors: { "export-text": "The assistant profile is not available now." } });
    expect(failed).toContain('role="alert"');
    expect(failed).toContain("not available now");
  });

  it("offers the download for an empty profile too", () => {
    expect(draw({})).toContain("Download as JSON");
    expect(draw({ status: "loading" })).not.toContain("Download as JSON");
  });

  it("has no Undo in the markup (the chat Undo card needs PZ-6)", () => {
    expect(draw({ profile: full })).not.toContain("Undo");
  });
});

describe("source scan", () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const files = ["AssistantPane.tsx", "page.tsx", "lib/profile.ts"].map((f) =>
    strip(readFileSync(new URL(`./${f}`, import.meta.url), "utf-8")),
  );
  it("uses no lucide-react, no inline style and no colour literal", () => {
    for (const src of files) {
      expect(src).not.toMatch(/lucide-react/);
      expect(src).not.toMatch(/style=\{?/);
      expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(src).not.toMatch(/\b(?:rgb|hsl)a?\(/);
      expect(src).not.toMatch(/\[#|-\[var\(/);
    }
  });
  it("hand-rolls no select and no switch", () => {
    for (const src of files) {
      expect(src).not.toMatch(new RegExp("<sel" + "ect\\b"));
      expect(src).not.toMatch(/role="switch"/);
    }
  });
});
