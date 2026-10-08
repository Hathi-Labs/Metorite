/**
 * The data fence, read for a person (owner report, 2026-10-07).
 *
 * `fencedText.ts` is the one parser of the «marks» for display, and
 * `FencedText.tsx` draws it. Each rule names the mutation that breaks it.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { EntityIndexContext } from "@/components/ChatEntityPill";
import FencedText from "@/components/FencedText";
import { EMPTY_INDEX } from "@/lib/entityIndex";
import { hasFence, soleName, splitFenced, unfenced } from "@/lib/fencedText";

describe("splitFenced", () => {
  // Mutation caught: a parser that returns the raw text.
  it("turns «x» into a name and keeps the text around it", () => {
    expect(splitFenced("Create 8 tasks in «Email Application»?")).toEqual([
      { kind: "text", text: "Create 8 tasks in " },
      { kind: "name", text: "Email Application" },
      { kind: "text", text: "?" },
    ]);
  });

  it("carries a task number with its name", () => {
    expect(splitFenced("#28 «Fix the cleaner»")).toEqual([
      { kind: "name", text: "Fix the cleaner", number: "#28" },
    ]);
  });

  // Mutation caught: dropping the stray-mark strip.
  it("never keeps a stray mark: unclosed, lone, empty or nested", () => {
    for (const input of ["«abc", "abc»", "a «» b", "«a «b» c»", "««x»»", "«line\nbreak»"]) {
      const out = splitFenced(input);
      for (const p of out) expect(p.text).not.toMatch(/[«»]/);
      expect(unfenced(input)).not.toMatch(/[«»]/);
    }
    // Nested: the inner pair is the name, the outer marks go.
    expect(splitFenced("«a «b» c»")).toEqual([
      { kind: "text", text: "a " },
      { kind: "name", text: "b" },
      { kind: "text", text: " c" },
    ]);
  });

  // Review round 1. Mutation caught: dropping QUOTE_SOURCE, so a French
  // quotation lost its marks in every answer.
  it("keeps a padded quotation as written", () => {
    expect(unfenced("Il a dit « Bonjour » hier.")).toBe("Il a dit « Bonjour » hier.");
    expect(splitFenced("« oui » and «Ops»")).toEqual([
      { kind: "text", text: "« oui » and " },
      { kind: "name", text: "Ops" },
    ]);
  });

  it("reads the whole value as one name only when it is one", () => {
    expect(soleName("«Done»")).toEqual({ text: "Done" });
    expect(soleName(" #7 «Ship it» ")).toEqual({ text: "Ship it", number: "#7" });
    expect(soleName("«a», «b»")).toBeNull();
    expect(soleName("status «Done»")).toBeNull();
    expect(hasFence("plain")).toBe(false);
  });
});

describe("FencedText", () => {
  // Mutation caught: rendering `text` as given.
  it("draws no mark, and a name as a quiet emphasis", () => {
    const html = renderToStaticMarkup(createElement(FencedText, { text: "in «Ops» now" }));
    expect(html).not.toContain("«");
    expect(html).toContain('<span data-fenced-name="" class="font-medium text-foreground">Ops</span>');
  });

  // Mutation caught: building HTML out of the value (an innerHTML sink).
  it("keeps HTML inside a fenced value as text", () => {
    const html = renderToStaticMarkup(
      createElement(FencedText, { text: 'Rename «<img src=x onerror=alert(1)><script>x()</script>»?' }),
    );
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;");
  });

  it("draws the shared pill inside a Projects turn, and no mark", () => {
    const html = renderToStaticMarkup(
      createElement(
        EntityIndexContext.Provider,
        { value: EMPTY_INDEX },
        createElement(FencedText, { text: "under «Hathi Labs Projects»" }),
      ),
    );
    expect(html).not.toContain("«");
    expect(html).not.toContain("data-fenced-name");
    expect(html).toContain("Hathi Labs Projects");
  });
});
