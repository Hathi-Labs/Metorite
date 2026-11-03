import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { bindToRun } from "@/lib/runBinding";

type Ev = { type: string; runId?: string; delta?: string };

/** What a reader saves: the text of the events its binding accepts. */
function savedBy(events: Ev[]): string {
  const bind = bindToRun();
  let text = "";
  for (const ev of events) {
    if (!bind.accept(ev)) break;
    if (ev.type === "TEXT_MESSAGE_CONTENT") text += ev.delta ?? "";
  }
  return text;
}

describe("bindToRun", () => {
  // Two sends within ms: run 1 is cancelled, run 2 resets the shared stream.
  const shared: Ev[] = [
    { type: "RUN_STARTED", runId: "run-1" },
    { type: "TEXT_MESSAGE_CONTENT", delta: "first " },
    { type: "RUN_STARTED", runId: "run-2" },
    { type: "TEXT_MESSAGE_CONTENT", delta: "final reply" },
  ];

  it("keeps the old reader to its own run's words", () => {
    expect(savedBy(shared)).toBe("first ");
  });

  it("lets the new reader keep the final reply, so one row holds it", () => {
    expect(savedBy(shared.slice(2))).toBe("final reply");
  });

  it("accepts a replay of the same run", () => {
    expect(savedBy([
      { type: "RUN_STARTED", runId: "run-1" },
      { type: "TEXT_MESSAGE_CONTENT", delta: "a" },
      { type: "RUN_STARTED", runId: "run-1" },
      { type: "TEXT_MESSAGE_CONTENT", delta: "b" },
    ])).toBe("ab");
  });

  it("follows a stream that carries no run id", () => {
    expect(savedBy([{ type: "TEXT_MESSAGE_CONTENT", delta: "x" }])).toBe("x");
  });
});

describe("the chat route uses the binding", () => {
  it("stops its reader on another run's RUN_STARTED", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../app/api/agent/chat/route.ts", import.meta.url)),
      "utf-8",
    );
    expect(src).toContain('from "@/lib/runBinding"');
    expect(src).toContain("bindToRun()");
    expect(src).toContain("binding.accept(");
  });
});
