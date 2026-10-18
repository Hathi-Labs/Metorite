/**
 * WS-55 EA-13b (`external_agent_access.md` §12.2 item 5, EA3-44) — the
 * timeline is honest, rendered.
 *
 * One timeline row is `Entry` in `TaskBody.tsx`. These tests draw it as the
 * server would, so the words are the drawn words and not only the rule of
 * `activityAttribution`.
 *
 * Mutations caught, by test:
 * - `Entry` drops `attribution.through`, or reads the suffix only on a row
 *   with `meta.agent_kind` (the rule before EA-13b): each agent row;
 * - `Entry` prints `system:github` instead of "GitHub", or draws the row's
 *   own body instead of the merge reason: the GitHub row;
 * - `Entry` puts a suffix on a member's own row: the member row.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/projects",
  useSearchParams: () => new URLSearchParams(),
}));

import type { ActivityRow } from "../lib/api";
import { Entry } from "./TaskBody";

const VIA = "mcp:claude.ai:claude-code";
const RAVI = "ravi@example.com";
const LABELS = new Map([[RAVI, "Ravi"]]);

function row(over: Partial<ActivityRow>): ActivityRow {
  return {
    id: "a1",
    type: "comment",
    body: "",
    meta: {},
    created_by: RAVI,
    created_at: "2026-10-18T10:00:00Z",
    ...over,
  } as ActivityRow;
}

function draw(activity: ActivityRow): string {
  return renderToStaticMarkup(
    createElement(Entry, { activity, fields: [], personLabels: LABELS }),
  )
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

describe("a timeline row names the client of an agent act (EA-13b)", () => {
  it.each([
    ["a comment", row({ type: "comment", body: "Starting on this.", meta: { via: VIA } })],
    ["a status move", row({ type: "status_change", body: "moved this to In review", meta: { via: VIA } })],
    ["an assignment", row({ type: "assignment", meta: { via: VIA, added: [RAVI], removed: [] } })],
    ["a link", row({ type: "link", body: "Linked #12", meta: { via: VIA } })],
  ])("draws %s through Claude Code", (_kind, activity) => {
    expect(draw(activity)).toContain("Ravi through Claude Code");
  });

  it("draws a status move as one sentence with its client", () => {
    const text = draw(row({ type: "status_change", body: "moved this to In review", meta: { via: VIA } }));
    expect(text).toMatch(/^Ravi through Claude Code · .+ moved this to In review$/);
  });

  it("draws a row of the GitHub App as GitHub, with the merge reason", () => {
    const text = draw(
      row({
        type: "system",
        body: "Merged",
        created_by: "system:github",
        meta: {
          via: "github", automation: true, reason: "agent_run", agent_run_id: "r1",
          github: { repo: "owner/repo", number: 12, merged_by: "octo", delivery: "d1" },
        },
      }),
    );
    expect(text).toContain("GitHub ·");
    expect(text).toContain("GitHub moved this to Done after owner/repo#12 merged");
    expect(text).not.toContain("system:github");
    expect(text).not.toContain("through");
  });

  it("draws no suffix on a member's own row", () => {
    const text = draw(row({ type: "status_change", body: "moved this to Doing", meta: {} }));
    expect(text).toMatch(/^Ravi · /);
    expect(text).not.toContain("through");
  });
});
