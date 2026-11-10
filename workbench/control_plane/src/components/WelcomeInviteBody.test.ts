/**
 * The founder's welcome buttons (WS-OB GD-5a repair, item 10). Three buttons
 * did not fit one row in the dialog. "Invite my team" wrapped and stood alone
 * on a second row. With the tour button they now stand in ONE column, each the
 * full width, at every window width. Two buttons keep the row.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { InviteBody } from "./WelcomeDialog";

const html = (onTour?: () => void) =>
  renderToStaticMarkup(createElement(InviteBody, { onDone: () => {}, onTour }));

describe("InviteBody layout", () => {
  it("with the tour button, the three buttons stand in one column of full-width buttons", () => {
    const h = html(() => {});
    for (const t of ["Explore on my own first", "Show me around", "Invite my team"]) expect(h).toContain(t);
    const row = h.match(/<div class="([^"]*)"><button/)?.[1] ?? "";
    expect(row).toContain("flex-col");
    // No row layout at any width, so nothing wraps and nothing stands alone.
    expect(row).not.toMatch(/flex-row|flex-wrap/);
    // Every button is the full width of the column.
    const buttons = h.match(/<button[^>]*class="[^"]*"/g) ?? [];
    expect(buttons).toHaveLength(3);
    for (const b of buttons) expect(b).toContain("w-full");
    // The link around "Invite my team" fills the column too.
    expect(h).toMatch(/<a [^>]*class="block"/);
  });

  it("without the tour button, the two keep the row from the small breakpoint up", () => {
    const h = html();
    expect(h).not.toContain("Show me around");
    expect(h).toMatch(/sm:flex-row/);
  });
});
