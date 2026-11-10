import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { bindToRun } from "@/lib/runBinding";

type Ev = { type: string; runId?: string; delta?: string };

/** What a reader saves: the text of the events its binding accepts. */
function savedBy(events: Ev[], seed?: string): string {
  const bind = bindToRun(seed);
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

describe("bindToRun with the gateway's run id", () => {
  // Run 2 cancelled run 1 before run 1 yielded RUN_STARTED. Request 1 reads
  // from "0", so the first RUN_STARTED it meets is run 2's.
  const onlyRun2: Ev[] = [
    { type: "RUN_STARTED", runId: "run-2" },
    { type: "TEXT_MESSAGE_CONTENT", delta: "run two reply" },
  ];

  it("a seeded reader refuses a first RUN_STARTED of another run", () => {
    expect(savedBy(onlyRun2, "run-1")).toBe("");
  });

  it("without a seed the old first-seen rule applies", () => {
    expect(savedBy(onlyRun2)).toBe("run two reply");
  });

  it("a seeded reader keeps its own run", () => {
    expect(savedBy(onlyRun2, "run-2")).toBe("run two reply");
  });
});

describe("the chat route uses the binding", () => {
  it("stops its reader on another run's RUN_STARTED", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../app/api/agent/chat/route.ts", import.meta.url)),
      "utf-8",
    );
    expect(src).toContain('from "@/lib/runBinding"');
    expect(src).toContain("binding.accept(");
    expect(src).toContain("bindToRun(runId)");
    expect(src).toContain('streamRes.headers.get("x-run-id")');
  });

  it("checks the supersede flag before it reads again", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../app/api/agent/chat/route.ts", import.meta.url)),
      "utf-8",
    );
    expect(src).toMatch(/if \(superseded\) break;\s+const \{ done, value \} = await reader\.read\(\);/);
  });
});
