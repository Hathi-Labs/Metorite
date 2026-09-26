/**
 * The chat translator's checkpoint names the agent that ran (WS-27bm S10,
 * `projects_ai_chat.md` §16.2 rules 1 to 3).
 *
 * Without `author_*` keys, the gateway stamps the ROOM's agent on the first
 * checkpoint, and its COALESCE keeps that stamp. The R8 half of this fence is
 * `tests/unit/test_rooms.py::test_a_checkpoint_author_wins_over_the_room_agent`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { assistantCheckpointRow, checkpointIsEmpty } from "@/lib/assistantCheckpoint";

const BASE = {
  threadId: "t1",
  content: "Done.",
  messageId: "m1",
  now: 1_700_000_000_000,
};

describe("assistantCheckpointRow", () => {
  it("names the agent that ran", () => {
    const row = assistantCheckpointRow({ ...BASE, agentName: "projects-assistant" });
    expect(row.author_kind).toBe("agent");
    expect(row.author_email).toBe("projects-assistant");
    expect(row.role).toBe("assistant");
    expect(row.id).toBe("m1");
  });

  it("sends no author when it does not know the agent", () => {
    for (const agentName of [undefined, "", "  "]) {
      const row = assistantCheckpointRow({ ...BASE, agentName });
      expect(row).not.toHaveProperty("author_kind");
      expect(row).not.toHaveProperty("author_email");
    }
  });

  it("keeps the rest of the row shape", () => {
    const row = assistantCheckpointRow({
      ...BASE,
      toolEvents: [{ id: "x" }],
      reasoningBlocks: ["a", "b"],
      progressLines: ["p"],
      todos: [{ id: "1", title: "T", status: "done" }],
      segments: [{ id: "s", text: "hi" }],
      customEvents: [{ name: "artifact_created", value: 1 }],
    });
    expect(row).toMatchObject({
      content: "Done.",
      timestamp: BASE.now,
      tool_events: [{ id: "x" }],
      progress_lines: ["p"],
      reasoning: JSON.stringify(["a", "b"]),
      agent_state: { todos: [{ id: "1", title: "T", status: "done" }], segments: [{ id: "s", text: "hi" }] },
      custom_events: [{ name: "artifact_created", value: 1 }],
    });
    const bare = assistantCheckpointRow({ ...BASE, messageId: undefined });
    expect(bare.agent_state).toBeNull();
    expect(bare.reasoning).toBeNull();
    expect(bare.id).toBe(`assistant-t1-${BASE.now.toString(36)}`);
  });

  it("skips a turn that holds nothing", () => {
    expect(checkpointIsEmpty({ threadId: "t1", content: "  " })).toBe(true);
    expect(checkpointIsEmpty({ threadId: "t1", content: "", todos: [{ id: "1", title: "T", status: "x" }] })).toBe(false);
    expect(checkpointIsEmpty({ threadId: "t1", content: "hi" })).toBe(false);
  });
});

describe("route.ts wires the agent into the live path only", () => {
  const src = readFileSync(
    fileURLToPath(new URL("../app/api/agent/chat/route.ts", import.meta.url)),
    "utf-8",
  ).replace(/\r\n/g, "\n");

  it("builds its row with the shared module", () => {
    expect(src).toContain("assistantCheckpointRow(checkpoint)");
    // A second inline row literal would drift from the tested one.
    expect(src).not.toMatch(/role: "assistant",\s*\n\s*content,/);
  });

  it("passes the agent to both checkpoints", () => {
    expect(src.match(/segments, agentName\)\.catch/g) ?? []).toHaveLength(2);
  });

  it("gives the live stream the resolved agent, and the reconnect stream none", () => {
    const calls = src.match(/await translateAndPersistStream\(\n[\s\S]*?\n\s*\);/g) ?? [];
    expect(calls).toHaveLength(2);
    const reconnect = calls.find((c) => c.includes("reconRes.body!"));
    const live = calls.find((c) => c.includes("streamRes.body!"));
    expect(reconnect).toBeDefined();
    expect(live).toBeDefined();
    expect(reconnect).not.toContain("resolvedAgentName");
    expect(live).toContain("resolvedAgentName");
  });
});
