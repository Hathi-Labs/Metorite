// The model of /studio/mcp-tools (B1b-4). R7 mutations, each run once:
//  - `canApprove` ignores the capability           -> "a member without integrations:manage sees no Approve" fails.
//  - `clip` keeps bidi and zero-width marks        -> "bidi and zero-width marks become spaces" fails.
//  - `approvePath` skips the grammar               -> "a name that breaks the grammar builds no URL" fails.
//  - `mcpTools` treats a missing `changed` as false -> "a missing changed reads as waiting" fails.
import { describe, expect, it } from "vitest";

import {
  CHANGED_AFTER_LOOK,
  LIST_STALE_SENTENCE,
  MAX_DESCRIPTION_CHARS,
  approveOutcome,
  approvePath,
  canApprove,
  clip,
  clipDescription,
  confirmCopy,
  groupByServer,
  hintWords,
  mcpTools,
  refreshPath,
  stateWords,
} from "./mcpToolsModel";

const H = "a".repeat(64);
const row = (over: Record<string, unknown> = {}) => ({
  id: "mcp:docs.search", source: "mcp", server: "docs", mcp_tool: "search", class: "A",
  changed: true, text_hash: H, description: "Finds a page.",
  hints: { readOnlyHint: true, destructiveHint: false }, ...over,
});

describe("the rows", () => {
  it("keeps the MCP rows and nothing else", () => {
    const t = mcpTools([row(), { id: "x", source: "door", server: "docs", mcp_tool: "y" }]);
    expect(t).toHaveLength(1);
    expect(t[0]).toMatchObject({ server: "docs", tool: "search", textHash: H, klass: "A", changed: true });
  });
  it("a missing changed reads as waiting", () => {
    expect(mcpTools([row({ changed: undefined })])[0].changed).toBe(true);
    expect(mcpTools([row({ changed: false })])[0].changed).toBe(false);
  });
  it("a hint that is not a boolean is not said", () => {
    const t = mcpTools([row({ hints: { readOnlyHint: "true" } })])[0];
    expect(t.hints.readOnlyHint).toBeNull();
    expect(hintWords(t.hints)).toEqual([
      "Read only: not said", "Destructive: not said", "Idempotent: not said", "Open world: not said",
    ]);
  });
  it("drops a row whose name breaks the grammar and a hash that is not 64 hex", () => {
    expect(mcpTools([row({ server: "Docs" }), row({ mcp_tool: "a.b" }), row({ mcp_tool: "a/b" })])).toEqual([]);
    expect(mcpTools([row({ text_hash: "ABC" })])[0].textHash).toBe("");
    expect(mcpTools(null)).toEqual([]);
  });
  it("groups by server, sorted", () => {
    const t = mcpTools([row({ server: "zeta" }), row({ server: "alpha" }), row({ server: "zeta", mcp_tool: "b" })]);
    expect(groupByServer(t).map((g) => [g.server, g.tools.length])).toEqual([["alpha", 1], ["zeta", 2]]);
  });
  it("says the state in plain words", () => {
    expect(stateWords({ changed: true })).toBe("Waits for approval");
    expect(stateWords({ changed: false })).toBe("Approved");
  });
});

describe("hostile text", () => {
  it("keeps an <img onerror> as plain text", () => {
    const d = clipDescription('<img src=x onerror="alert(1)">');
    expect(d).toBe('<img src=x onerror="alert(1)">');
  });
  it("bidi and zero-width marks become spaces", () => {
    expect(clip("a\u202eb\u200bc\u2066d\ufeffe")).toBe("a b c d e");
  });
  it("newlines and tabs become spaces, and the description is one line", () => {
    expect(clipDescription("one\n\ntwo\tthree")).toBe("one two three");
  });
  it("cuts 100k characters", () => {
    const d = clipDescription("x".repeat(100_000));
    expect(d.length).toBe(MAX_DESCRIPTION_CHARS);
    expect(d.endsWith("...")).toBe(true);
    expect(mcpTools([row({ description: "y".repeat(100_000) })])[0].description.length).toBe(MAX_DESCRIPTION_CHARS);
  });
  it("a name of 100k characters never reaches a URL", () => {
    expect(approvePath("docs", "t".repeat(100_000))).toBeNull();
  });
});

describe("the paths", () => {
  it("builds the two paths", () => {
    expect(refreshPath("docs")).toBe("/api/studio/mcp/docs/refresh");
    expect(approvePath("docs", "Search_1-a")).toBe("/api/studio/mcp/docs/tools/Search_1-a/approve");
  });
  it("a name that breaks the grammar builds no URL", () => {
    for (const bad of ["", "..", "a/b", "a.b", "a b", "a?b", "a#b", "a%2fb", "a\n", "é", "-a", "x".repeat(65)]) {
      expect(approvePath("docs", bad)).toBeNull();
    }
    for (const bad of ["", "..", "../x", "A", "a_b", "-a", "a-", "a/b", "a\n", "x".repeat(51)]) {
      expect(refreshPath(bad)).toBeNull();
      expect(approvePath(bad, "t")).toBeNull();
    }
  });
});

describe("who sees Approve", () => {
  const t = { changed: true, textHash: H, server: "docs", tool: "search" };
  it("a member with integrations:manage sees it on a waiting tool", () => {
    expect(canApprove(t, ["integrations:manage"])).toBe(true);
  });
  it("a member without integrations:manage sees no Approve", () => {
    expect(canApprove(t, ["integrations:use:*"])).toBe(false);
    expect(canApprove(t, [])).toBe(false);
    expect(canApprove(t, null)).toBe(false);
  });
  it("an approved tool and a row with no shown hash show none", () => {
    expect(canApprove({ ...t, changed: false }, ["integrations:manage"])).toBe(false);
    expect(canApprove({ ...t, textHash: "" }, ["integrations:manage"])).toBe(false);
  });
});

describe("the dialog words and the refusals", () => {
  it("holds the text of the tool and its four hints", () => {
    const c = confirmCopy(mcpTools([row()])[0]);
    expect(c.description).toBe("Finds a page.");
    expect(c.body).toContain("Read only: yes");
    expect(c.body).toContain("Destructive: no");
    expect(c.body).toContain("Open world: not said");
    expect(c.subject).toBe("docs / search");
  });
  it("a changed text reads again, a stale list offers Refresh, the rest shows the sentence", () => {
    expect(approveOutcome(409, "mcp_tool_text_changed", "x")).toEqual({ kind: "reread", message: CHANGED_AFTER_LOOK });
    expect(approveOutcome(409, "mcp_list_stale", "x")).toEqual({ kind: "refresh", message: LIST_STALE_SENTENCE });
    expect(approveOutcome(403, "mcp_approve_not_allowed", "No.")).toEqual({ kind: "show", message: "No." });
    expect(approveOutcome(429, undefined, "Wait 3 seconds.").message).toBe("Wait 3 seconds.");
  });
});
