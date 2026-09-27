/**
 * D-PM-38 — the card shell draws a subtask's "↳ Parent" line.
 *
 * The crumb is a PROP of the shell, not a `shown_fields` chip, so a member
 * cannot hide it. These render the real shell and read the markup.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TaskCardShell } from "./TaskCardShell";

const card = (parent?: Parameters<typeof TaskCardShell>[0]["parent"]) =>
  renderToStaticMarkup(
    createElement(TaskCardShell, {
      parent,
      children: createElement("p", { id: "own-title" }, "Write the release notes"),
    }),
  );

describe("TaskCardShell — the parent crumb", () => {
  it("draws a visible parent above the card's own title", () => {
    const html = card({ id: "p", ref: "#12", title: "Ship v2" });
    expect(html).toContain("#12 Ship v2");
    expect(html).toContain('title="Subtask of #12 Ship v2"');
    expect(html.indexOf("#12 Ship v2")).toBeLessThan(html.indexOf("own-title"));
  });

  it("says only 'Subtask' for a hidden parent", () => {
    const html = card({ hidden: true });
    expect(html).toContain(">Subtask<");
    expect(html).not.toContain("Subtask of");
  });

  it("draws no crumb for a top-level task", () => {
    expect(card(null)).not.toContain("Subtask");
    expect(card(undefined)).not.toContain("Subtask");
  });
});
