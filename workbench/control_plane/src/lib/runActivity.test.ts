/**
 * Which app a live run belongs to, and the count on each app's nav entry
 * (WS-51 S1, `chat_run_continuity.md` §4 S1).
 *
 * ONE mapping: the manifest's `agent` field on a `NavPane`. These tests pin
 * the five agents the spec names, the Chat fallback, and the fold that keeps
 * a run on a hidden pane visible on Chat.
 */

import { describe, expect, it } from "vitest";

import { NAV_SECTIONS } from "./nav";
import {
  CHAT_HREF,
  appForAgent,
  badgeText,
  countRunsByApp,
  mergeRuns,
  needsInputLabel,
  runBadge,
  runningLabel,
} from "./runActivity";

describe("one agent → app mapping", () => {
  it.each([
    ["orchestrator", "/chat"],
    ["projects-assistant", "/projects"],
    ["task-manager", "/tasks"],
    ["email-assistant", "/email"],
    ["app-builder", "/build/apps"],
    ["research-agent", "/chat"],
    ["unknown", "/chat"],
  ])("%s runs on %s", (agent, href) => {
    expect(appForAgent(agent)).toBe(href);
  });

  it("an absent agent counts on Chat", () => {
    expect(appForAgent(null)).toBe(CHAT_HREF);
    expect(appForAgent(undefined)).toBe(CHAT_HREF);
    expect(appForAgent("")).toBe(CHAT_HREF);
  });

  it("the first pane that names an agent owns it", () => {
    // `navigation_shell.md` §5.3 gives Calendar the task-manager agent too.
    // Its runs stay on My Tasks, the first pane that names it.
    const sections = [
      { id: "a", label: "A", items: [{ href: "/tasks", agent: "task-manager" }] },
      { id: "b", label: "B", items: [{ href: "/calendar", agent: "task-manager" }] },
    ] as unknown as typeof NAV_SECTIONS;
    expect(appForAgent("task-manager", sections)).toBe("/tasks");
    expect(countRunsByApp([{ threadId: "x", agentName: "task-manager" }], null, sections)).toEqual({
      "/tasks": 1,
    });
  });

  it("the mapping lives in the manifest, and nowhere else", () => {
    // Every agent the manifest names maps to the FIRST pane that names it.
    const first = new Map<string, string>();
    for (const s of NAV_SECTIONS) {
      for (const p of s.items) {
        if (p.agent && !first.has(p.agent)) first.set(p.agent, p.href);
      }
    }
    for (const [agent, href] of first) expect(appForAgent(agent)).toBe(href);
  });
});

describe("the count on each app", () => {
  const runs = [
    { threadId: "a", agentName: "orchestrator" },
    { threadId: "b", agentName: "projects-assistant" },
    { threadId: "c", agentName: "projects-assistant" },
    { threadId: "d", agentName: "task-manager" },
    { threadId: "e", agentName: "app-builder" },
    { threadId: "f", agentName: "unknown" },
  ];

  it("counts each run on its own app", () => {
    expect(countRunsByApp(runs)).toEqual({
      "/chat": 2,
      "/projects": 2,
      "/tasks": 1,
      "/build/apps": 1,
    });
  });

  it("a run on a pane the member cannot see counts on Chat", () => {
    const visible = new Set(["/chat", "/projects", "/tasks"]);
    expect(countRunsByApp(runs, visible)).toEqual({
      "/chat": 3,
      "/projects": 2,
      "/tasks": 1,
    });
  });

  it("counts one thread once", () => {
    expect(
      countRunsByApp([
        { threadId: "a", agentName: "task-manager" },
        { threadId: "a", agentName: "task-manager" },
      ]),
    ).toEqual({ "/tasks": 1 });
  });
});

describe("the server's runs and this tab's runs", () => {
  const local: Record<string, string> = { t2: "email-assistant", t3: "task-manager" };
  const agentOf = (id: string) => local[id];

  it("unions the two, by thread", () => {
    const merged = mergeRuns(
      [{ threadId: "t1", agentName: "orchestrator" }],
      ["t1", "t3"],
      agentOf,
    );
    expect(merged.map((r) => r.threadId).sort()).toEqual(["t1", "t3"]);
  });

  it("the tab names a run the server calls 'unknown'", () => {
    const merged = mergeRuns([{ threadId: "t2", agentName: "unknown" }], [], agentOf);
    expect(merged).toEqual([{ threadId: "t2", agentName: "email-assistant", state: "running" }]);
  });

  it("the server's name wins when it has one", () => {
    const merged = mergeRuns([{ threadId: "t3", agentName: "orchestrator" }], ["t3"], agentOf);
    expect(merged).toEqual([{ threadId: "t3", agentName: "orchestrator", state: "running" }]);
  });

  // WS-51 S2. Mutation: drop `state` from mergeRuns, and this fails.
  it("keeps the server's needs_input, and a tab-only run is running", () => {
    const merged = mergeRuns(
      [{ threadId: "t3", agentName: "task-manager", state: "needs_input" }],
      ["t3", "t9"],
      agentOf,
    );
    expect(merged.find((r) => r.threadId === "t3")?.state).toBe("needs_input");
    expect(merged.find((r) => r.threadId === "t9")?.state).toBeUndefined();
  });
});

describe("a run that needs the member (WS-51 S2)", () => {
  const runs = [
    { threadId: "a", agentName: "orchestrator", state: "needs_input" as const },
    { threadId: "b", agentName: "orchestrator", state: "running" as const },
    { threadId: "c", agentName: "task-manager", state: "needs_input" as const },
    { threadId: "d", agentName: "email-assistant" },
  ];

  // Mutation: drop the `onlyNeedsInput` skip, and this fails.
  it("counts only the runs that wait, on the same fold", () => {
    expect(countRunsByApp(runs, null, NAV_SECTIONS, true)).toEqual({ "/chat": 1, "/tasks": 1 });
    expect(countRunsByApp(runs, new Set(["/chat"]), NAV_SECTIONS, true)).toEqual({ "/chat": 2 });
    expect(countRunsByApp(runs)).toEqual({ "/chat": 2, "/tasks": 1, "/email": 1 });
  });

  // Mutation: test `running` before `needsInput` in runBadge, and this fails.
  it("amber wins over green on the same pane, and counts the runs that wait", () => {
    expect(runBadge(3, 1)).toEqual({ count: 1, tone: "warning", label: "1 assistant needs your answer" });
    expect(runBadge(3, 0)).toEqual({ count: 3, tone: "success", label: "3 assistants running" });
    expect(runBadge(0, 0)).toBeNull();
  });

  it("speaks the count that waits", () => {
    expect(needsInputLabel(1)).toBe("1 assistant needs your answer");
    expect(needsInputLabel(2)).toBe("2 assistants need your answer");
  });
});

describe("the badge's words", () => {
  it("speaks the count", () => {
    expect(runningLabel(1)).toBe("1 assistant running");
    expect(runningLabel(2)).toBe("2 assistants running");
  });

  it("caps the visible count at 9+", () => {
    expect(badgeText(3)).toBe("3");
    expect(badgeText(9)).toBe("9");
    expect(badgeText(10)).toBe("9+");
  });
});
