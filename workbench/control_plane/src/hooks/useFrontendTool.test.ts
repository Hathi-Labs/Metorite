/**
 * The frontend-tool dispatcher (H-164, WS-27bm S6).
 *
 * A skill tool sends one CUSTOM `frontend_tool` event `{id, name, args}`;
 * `AgentChat` hands it to `runFrontendToolEvent`, which runs the handler a
 * page registered. These are its four answers, and the addendum rule.
 */
import { describe, expect, it, vi } from "vitest";

import {
  buildFrontendToolsAddendum,
  registerFrontendTool,
  runFrontendToolEvent,
} from "./useFrontendTool";

describe("runFrontendToolEvent", () => {
  it("runs a registered handler once per event id", async () => {
    const handler = vi.fn(() => "opened");
    const off = registerFrontendTool({ name: "t.open", description: "x", handler, dispatched: true });
    expect(await runFrontendToolEvent({ id: "e1", name: "t.open", args: { task_id: "7" } })).toBe("ran");
    expect(await runFrontendToolEvent({ id: "e1", name: "t.open", args: {} })).toBe("duplicate");
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ task_id: "7" });
    off();
  });

  it("ignores a name no open page registered", async () => {
    expect(await runFrontendToolEvent({ id: "e2", name: "nobody.here", args: {} })).toBe("unregistered");
  });

  it("refuses a malformed event", async () => {
    expect(await runFrontendToolEvent(null)).toBe("invalid");
    expect(await runFrontendToolEvent({ name: "t.open" })).toBe("invalid");
  });

  it("reports a handler that throws, and does not rethrow", async () => {
    const off = registerFrontendTool({
      name: "t.boom",
      description: "x",
      handler: () => {
        throw new Error("boom");
      },
    });
    expect(await runFrontendToolEvent({ id: "e3", name: "t.boom" })).toBe("failed");
    off();
  });
});

describe("buildFrontendToolsAddendum", () => {
  it("leaves out a dispatched tool, which the model cannot call by name", () => {
    const a = registerFrontendTool({ name: "t.direct", description: "direct", handler: () => "" });
    const b = registerFrontendTool({ name: "t.via", description: "via", handler: () => "", dispatched: true });
    const text = buildFrontendToolsAddendum();
    expect(text).toContain("t.direct");
    expect(text).not.toContain("t.via");
    a();
    b();
  });
});
