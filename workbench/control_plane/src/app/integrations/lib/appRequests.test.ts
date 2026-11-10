// The list "Waiting for you" of Integrations, AI agents (WS-MCP MCP-P1b-1,
// fix round 1).
//
// - When no request waits, the list draws no section at all: not while it
//   loads, not on a 404 (the store is dark), not on an empty list, and not
//   when the member answered each row (the coordinator's recommendation,
//   2026-11-04; the owner may change it at the preview).
// - A list that fails to load with nothing to show keeps its error, with a
//   retry.
// - The gateway gives the newest 50 rows and `more`, and the list says how
//   many it leaves out.
//
// Mutations, each one red here:
// - show the section for an empty list: the empty cases fail;
// - drop the answered filter: the answered case fails;
// - drop the count line: the more cases fail.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppRequest } from "@/lib/appRequest";

import { listRequests, moreLine, waitingView } from "./appRequests";

const SRC = fileURLToPath(new URL("..", import.meta.url));

function row(id: string): AppRequest {
  return {
    request_id: id,
    run_id: `r-${id}`,
    state: "awaiting",
    decision: null,
    source: "mcp",
    app: "knowledge",
    subject: null,
    op: "edit_page",
    class: "B",
    client_via: "mcp:claude.ai:claude-code",
    card: { title: "Edit one page." },
    call: {},
    expires_at: null,
    created_at: null,
    decided_at: null,
  };
}

const NONE = new Set<string>();

describe("the section shows only when a request waits", () => {
  it.each<[string, Parameters<typeof waitingView>[0]]>([
    ["while it loads", { data: undefined, error: null }],
    ["while the store is dark (404)", { data: null, error: null }],
    ["for an empty list", { data: { rows: [], more: 0 }, error: null }],
  ])("draws nothing %s", (_name, state) => {
    expect(waitingView(state, NONE).kind).toBe("none");
  });

  it("draws nothing once the member answered each row here", () => {
    const view = waitingView({ data: { rows: [row("a"), row("b")], more: 0 }, error: null }, new Set(["a", "b"]));
    expect(view.kind).toBe("none");
  });

  it("lists the rows that still wait", () => {
    const view = waitingView({ data: { rows: [row("a"), row("b")], more: 3 }, error: null }, new Set(["a"]));
    expect(view.kind).toBe("list");
    expect(view.kind === "list" && view.rows.map((r) => r.request_id)).toEqual(["b"]);
    expect(view.kind === "list" && view.more).toBe(3);
  });

  it("keeps an error with nothing to show, so the member can try again", () => {
    expect(waitingView({ data: undefined, error: "The list did not load." }, NONE).kind).toBe("error");
  });

  // Re-review P3 (M1, keep space for async content). With a cold cache the
  // list appears after its fetch. At the top of the view it pushed the
  // Connect card down, so it sits after the agent lists, where nothing
  // below it moves.
  it("sits after the agent lists, so its arrival moves nothing above it", () => {
    const src = readFileSync(`${SRC}/components/AgentsView.tsx`, "utf8");
    const view = src.slice(src.indexOf('data-testid="agents-view"'));
    const waiting = view.indexOf("<WaitingRequests />");
    expect(waiting).toBeGreaterThan(0);
    for (const before of ["<ConnectCard />", "<DeleteSwitchCard />", 'testId="agents-mine"', 'testId="agents-org"']) {
      expect(view.indexOf(before), before).toBeGreaterThan(0);
      expect(view.indexOf(before), before).toBeLessThan(waiting);
    }
  });

  it("the view holds no empty-state words any more", () => {
    const src = readFileSync(`${SRC}/components/WaitingRequests.tsx`, "utf8");
    expect(src).not.toContain("No agent waits for your approval");
  });
});

describe("the count of the rows the page leaves out", () => {
  it("names how many more requests wait", () => {
    expect(moreLine(0)).toBe("");
    expect(moreLine(1)).toBe("1 more request waits. Answer these first, and it shows here.");
    expect(moreLine(12)).toBe("12 more requests wait. Answer these first, and they show here.");
  });
});

describe("listRequests", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads the rows and the count, and a 404 as a dark store", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ rows: [row("a")], more: 4 })),
    );
    expect(await listRequests()).toEqual({ rows: [row("a")], more: 4 });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Not Found" }, { status: 404 })));
    expect(await listRequests()).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ rows: "bad" })));
    expect(await listRequests()).toEqual({ rows: [], more: 0 });
  });
});
