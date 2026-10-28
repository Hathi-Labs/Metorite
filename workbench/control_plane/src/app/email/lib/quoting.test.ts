// #891 — a blockquote used for indentation is not a quoted reply.
//
// The GeM Cybersecurity Advisory indents with 217 bare <blockquote> tags and
// carries no reply marker. The last-resort rule took the first one as a quote
// and hid the whole advisory behind the toggle, under "Dear Sir/Madam,".
//
// R7 fences named here:
//   * `quote-bare-blockquote-needs-attribution`: a bare blockquote folds only
//     when "On … wrote:" or a From and Sent header comes directly before it.
//     `isAttribution` is that rule. The DOM walk that feeds it is checked in a
//     real browser (PR #891 evidence), since this suite runs with no DOM.
//   * `quote-toggle-words`: the toggle says "Show trimmed content" in words.
//   * `quote-one-image-choice`: one "Show images" choice serves the main part
//     and the quoted part.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { isAttribution } from "./quoting";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");

describe("quote-bare-blockquote-needs-attribution", () => {
  it("a greeting before an indented advisory is not an attribution", () => {
    expect(isAttribution("Dear Sir/Madam,")).toBe(false);
    expect(isAttribution("  GeM  Dear Sir/Madam, ")).toBe(false);
    expect(isAttribution("")).toBe(false);
  });

  it("a reply attribution is one", () => {
    expect(isAttribution("Thanks.\nOn Mon, 27 Oct 2026 at 10:00, Ana <ana@x.com> wrote:")).toBe(true);
    expect(isAttribution("On Mon, Ana wrote: ")).toBe(true);
    expect(isAttribution("From: Ana <ana@x.com>\nSent: Monday, 27 October 2026 10:00\nTo: Ops")).toBe(true);
    expect(isAttribution("From: Ana\nDate: 27 Oct 2026")).toBe(true);
    expect(isAttribution("Fyi\n---------- Forwarded message ---------")).toBe(true);
  });

  it("the bare blockquote rule asks isAttribution, and no unconditional fallback is left", () => {
    const src = read("./quoting.ts");
    expect(src).toMatch(/if \(isAttribution\(textBefore\(bq\)\)\) return bq;/);
    expect(src).not.toMatch(/if \(bq && bq !== body\.firstElementChild\) return bq;/);
  });
});

describe("quote-toggle-words", () => {
  it("draws its label as text, not only the dots", () => {
    const src = read("../components/MessageContent.tsx");
    const toggle = src.slice(src.indexOf("function QuoteToggle"), src.indexOf("function HtmlFrame"));
    expect(toggle).toContain('"Show trimmed content"');
    expect(toggle).toMatch(/<span>\{label\}<\/span>/);
  });
});

describe("quote-one-image-choice", () => {
  it("passes one choice to the main frame and the quoted frame", () => {
    const src = read("../components/MessageContent.tsx");
    const msg = src.slice(src.indexOf("function HtmlMessage"), src.indexOf("function TextMessage"));
    expect(msg.match(/<HtmlFrame [^>]*images=\{images\}/g)).toHaveLength(3);
    // A new message asks again.
    expect(msg).toMatch(/setShowQuoted\(false\);\s*setShowImages\(false\);/);
  });
});
