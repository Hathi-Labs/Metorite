/**
 * STU-D11b fence: the load line of the preview toolbar says loading and error in plain words.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import RecordsStatus from "@/components/studio/RecordsStatus";

const draw = (loading: boolean, error: string | null) =>
  renderToStaticMarkup(createElement(RecordsStatus, { loading, error, onRetry: () => {} }));

describe("RecordsStatus", () => {
  it("says that the records load", () => {
    const html = draw(true, null);
    expect(html).toContain("Loading the records\u2026");
    expect(html).toContain('role="status"');
  });

  it("says that the records did not load, with a Retry", () => {
    const html = draw(false, "boom");
    expect(html).toContain("The records did not load. Try again.");
    expect(html).toContain('role="alert"');
    expect(html).toMatch(/<button[^>]*>[^<]*Retry/);
    expect(html).not.toContain("boom");
  });

  it("draws nothing when the records are ready", () => {
    expect(draw(false, null)).toBe("");
  });
});
