// CO-13 (`chat_organization.md` §17.5): the continue line under the chat header.
// The runner has no DOM, so each case renders to a string.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ContinuedMark, continuedMarkLine } from "./ContinuedMark";

const noop = () => undefined;
const draw = (mark: Parameters<typeof ContinuedMark>[0]["mark"]) =>
  renderToStaticMarkup(createElement(ContinuedMark, { mark, onOpen: noop }));

describe("ContinuedMark", () => {
  it("names a readable source with a link to it", () => {
    const html = draw({ id: "s1", title: "Pricing deck" });
    expect(html).toContain("Continued from");
    expect(html).toContain("Pricing deck");
    expect(html).toContain("<button");
  });

  it("says an earlier chat, as plain text, when the source cannot be read", () => {
    const html = draw({ id: null, title: null });
    expect(html).toContain("Continued from an earlier chat");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<a ");
  });

  it("draws nothing for a chat that continues nothing", () => {
    expect(draw(null)).toBe("");
    expect(draw(undefined)).toBe("");
  });

  it("keeps a link for a readable source with no title", () => {
    expect(continuedMarkLine({ id: "s1", title: null })).toEqual({
      lead: "Continued from", title: "an earlier chat", id: "s1",
    });
  });

  it("sits in the dock under the header row, in a row of its own", () => {
    const dock = readFileSync(new URL("./AssistantDock.tsx", import.meta.url), "utf8");
    expect(dock).toMatch(/<\/header>\s*\{\/\*[^]*?\*\/\}\s*<ContinuedMark mark=\{active\?\.continuedFrom\}/);
  });
});
