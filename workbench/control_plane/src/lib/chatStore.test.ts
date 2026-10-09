/**
 * `releaseLoading` — a loop that gives up its stream gives up the loading
 * state it took, and only that loop (WS-51 S1, `chat_run_continuity.md` §4
 * S1 item 5).
 *
 * The defect: `useAgentChat`'s reattach loop is aborted on unmount, and its
 * catch and finally write nothing once cancelled. `isLoading` stayed true with
 * a dead controller in a store that outlives the component. The nav's run
 * badge then counted a run this tab no longer watched, until a reload.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  getActiveSessionIds,
  getSessionAgent,
  getSessionState,
  releaseLoading,
  setSessionAgent,
  setSessionState,
} from "./chatStore";

describe("releaseLoading", () => {
  it("clears the loading state of the loop that owns it", () => {
    const ctrl = new AbortController();
    setSessionState("own", (p) => ({ ...p, isLoading: true, abortController: ctrl }));
    expect(getActiveSessionIds().has("own")).toBe(true);

    ctrl.abort();
    expect(releaseLoading("own", ctrl)).toBe(true);
    expect(getSessionState("own").isLoading).toBe(false);
    expect(getSessionState("own").abortController).toBeNull();
    expect(getActiveSessionIds().has("own")).toBe(false);
  });

  it("leaves a newer loop's loading state alone", () => {
    const old = new AbortController();
    const newer = new AbortController();
    setSessionState("newer", (p) => ({ ...p, isLoading: true, abortController: newer }));
    expect(releaseLoading("newer", old)).toBe(false);
    expect(getSessionState("newer").isLoading).toBe(true);
    expect(getSessionState("newer").abortController).toBe(newer);
  });

  it("does nothing to an idle session", () => {
    const ctrl = new AbortController();
    setSessionState("idle", (p) => ({ ...p, isLoading: false, abortController: ctrl }));
    expect(releaseLoading("idle", ctrl)).toBe(false);
    expect(releaseLoading("never-seen", ctrl)).toBe(false);
  });
});

describe("the reattach loop releases on every exit that streams nothing", () => {
  const hook = readFileSync(
    fileURLToPath(new URL("../hooks/useAgentChat.ts", import.meta.url)),
    "utf8",
  );

  it("the unmount cleanup releases after it aborts", () => {
    expect(hook).toMatch(
      /cancelled = true;\s*abortCtrl\.abort\(\);[\s\S]{0,300}?releaseLoading\(threadId, abortCtrl\);\s*\};/,
    );
  });

  it("a steered (202) or refused reconnect releases before it returns", () => {
    expect(hook).toMatch(/res\.status === 202\) \{ releaseLoading\(threadId, abortCtrl\); return; \}/);
    expect(hook).toMatch(/!res\.ok \|\| !res\.body\) \{ releaseLoading\(threadId, abortCtrl\); return; \}/);
  });
});

describe("the agent of a session", () => {
  it("records the agent a hook streams", () => {
    setSessionAgent("s1", "task-manager");
    expect(getSessionAgent("s1")).toBe("task-manager");
    setSessionAgent("s1", "");
    expect(getSessionAgent("s1")).toBe("task-manager");
    expect(getSessionAgent("s2")).toBeUndefined();
  });
});
