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

  it("no two panes name one agent, so the mapping has one answer", () => {
    const owners = new Map<string, string[]>();
    for (const s of NAV_SECTIONS) {
      for (const p of s.items) {
        if (!p.agent) continue;
        owners.set(p.agent, [...(owners.get(p.agent) ?? []), p.href]);
      }
    }
    const doubled = [...owners].filter(([, hrefs]) => hrefs.length > 1);
    expect(doubled).toEqual([]);
  });

  it("the mapping lives in the manifest, and nowhere else", () => {
    // Every agent the manifest names maps back to its own pane.
    for (const s of NAV_SECTIONS) {
      for (const p of s.items) {
        if (p.agent) expect(appForAgent(p.agent)).toBe(p.href);
      }
    }
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
    expect(merged).toEqual([{ threadId: "t2", agentName: "email-assistant" }]);
  });

  it("the server's name wins when it has one", () => {
    const merged = mergeRuns([{ threadId: "t3", agentName: "orchestrator" }], ["t3"], agentOf);
    expect(merged).toEqual([{ threadId: "t3", agentName: "orchestrator" }]);
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
