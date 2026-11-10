/**
 * WS-55 EA-12d — "Ask an agent" (§12.1, "A member starts a run").
 *
 * Mutations caught, by test:
 * - a read-only connection is offered, so its run stays queued for ever;
 * - the empty state loses its way to the AI agents view;
 * - Send goes out with an empty instruction, with no connection, or with a
 *   reused idempotency key;
 * - the start leaves the one Projects client, or sends another shape;
 * - a 409 hides the server's reason, or offers "Try again";
 * - a second "Ask an agent" shows while the caller has an open run.
 *
 * The click path runs in a real browser in `e2e/agent-runs.spec.ts`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentConnection } from "@/app/integrations/lib/agents";

import type { AgentRun } from "./agentRuns";
import { ProjectsApiError, agentRunsApi } from "./api";
import {
  ASK_COPY,
  CONNECT_AGENT_HREF,
  askableConnections,
  cleanInstruction,
  connectionLabel,
  hasOpenRunOf,
  startFailure,
  startPayload,
} from "./askAgent";

function conn(over: Partial<AgentConnection> = {}): AgentConnection {
  return {
    id: "c1",
    member_email: "you@example.com",
    client_name: "Claude Code",
    client_origin: "https://claude.ai",
    scopes: ["projects:read", "projects:write"],
    pinned_projects: [],
    include_personal: false,
    created_at: "2026-10-10T09:00:00Z",
    last_used_at: null,
    expires_at: "2026-12-10T09:00:00Z",
    ...over,
  };
}

function run(over: Partial<AgentRun> = {}): AgentRun {
  return {
    id: "r1",
    task_id: "t1",
    member_email: "you@example.com",
    client_via: "mcp:claude.ai:claude-code",
    status: "created",
    state: "queued",
    ...over,
  } as AgentRun;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the agents a member can ask", () => {
  it("offers only a connection that may write to Projects", () => {
    const reader = conn({ id: "c2", scopes: ["projects:read"] });
    expect(askableConnections([conn(), reader]).map((r) => r.id)).toEqual(["c1"]);
  });

  it("has nothing to offer when the list is off or empty", () => {
    expect(askableConnections(null)).toEqual([]);
    expect(askableConnections([])).toEqual([]);
  });

  it("sends a member with no agent to the AI agents view", () => {
    expect(CONNECT_AGENT_HREF).toBe("/integrations?view=agents");
    expect(ASK_COPY.empty).toMatch(/no connected agent/i);
    expect(ASK_COPY.connect).toBe("Connect an agent");
  });

  it("labels two connections of one client by their day", () => {
    const a = conn();
    const b = conn({ id: "c2", created_at: "2026-10-12T09:00:00Z" });
    expect(connectionLabel(a)).toBe("Claude Code (claude.ai)");
    expect(connectionLabel(a, [a, b])).toBe("Claude Code (claude.ai), connected 2026-10-10");
    expect(connectionLabel(conn({ client_name: "claude.ai" }))).toBe("claude.ai");
  });
});

describe("the start payload", () => {
  it("needs a connection and an instruction", () => {
    expect(startPayload("", "Fix it", "k")).toBeNull();
    expect(startPayload("c1", "   \n ", "k")).toBeNull();
    expect(startPayload("c1", "x".repeat(20_001), "k")).toBeNull();
  });

  it("trims the instruction and drops a NUL", () => {
    expect(cleanInstruction("  Fix\u0000 it \n")).toBe("Fix it");
    expect(startPayload("c1", "  Write the tests  ", "k1")).toEqual({
      connection_id: "c1",
      body: "Write the tests",
      idempotency_key: "k1",
    });
  });

  it("takes a fresh idempotency key for each send", () => {
    const one = startPayload("c1", "Go");
    const two = startPayload("c1", "Go");
    expect(one?.idempotency_key).toBeTruthy();
    expect(one?.idempotency_key).not.toBe(two?.idempotency_key);
  });
});

describe("agentRunsApi.start", () => {
  it("posts the payload to the task's runs through the Projects proxy", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ run: run(), created: true }), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const payload = { connection_id: "c1", body: "Write the tests", idempotency_key: "k1" };
    const result = await agentRunsApi.start("t1", payload);
    expect(result.run.state).toBe("queued");
    expect(result.created).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/projects/tasks/t1/agent-runs");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual(payload);
  });

  it("shows the server's text for a 409, with no retry", async () => {
    const detail = "The agent has not picked this run up yet. Wait for it to start, then reply.";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail }), { status: 409 })),
    );
    const err = await agentRunsApi
      .start("t1", { connection_id: "c1", body: "Go", idempotency_key: "k" })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProjectsApiError);
    expect((err as ProjectsApiError).status).toBe(409);
    const text = startFailure(409, (err as Error).message);
    expect(text).toBe(detail);
    expect(text).not.toMatch(/Try again/);
  });

  it("asks for a retry on any other failure", () => {
    expect(startFailure(500, "")).toBe("Could not send the request. Try again.");
    expect(startFailure(409, "")).toMatch(/not picked up/);
  });
});

describe("when the task offers Ask an agent", () => {
  it("hides it while the caller has an open run, and only then", () => {
    expect(hasOpenRunOf([run()], "YOU@example.com")).toBe(true);
    expect(hasOpenRunOf([run({ status: "awaiting" })], "you@example.com")).toBe(true);
    expect(hasOpenRunOf([run({ status: "completed" })], "you@example.com")).toBe(false);
    expect(hasOpenRunOf([run({ member_email: "priya@example.com" })], "you@example.com")).toBe(
      false,
    );
    expect(hasOpenRunOf([run()], "")).toBe(false);
  });
});
