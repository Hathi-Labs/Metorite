/**
 * The signals of a run outside its open chat (WS-51 S5,
 * `project-docs/specs/chat_run_continuity.md` §4 S5). The spec names this
 * file as the fence. What it holds (R7):
 *
 * 1. **The unread rule.** A run that leaves the live list marks its chat
 *    unread. Opening the chat clears it. The map lives in the member's own
 *    chat namespace, so it is scoped by member AND org. A new member, a run
 *    that only this tab knew, and a chat the member watched mark nothing.
 * 2. **One toast for each finished run,** and never for the open chat. A run
 *    of a hidden tab waits for the tab, and two tabs show one toast.
 * 3. **The hidden tab.** The title text for each state, the favicon swap to a
 *    pre-made file, and the restore.
 * 4. **The phone pill.** It shows, hides, links, and counts "+N". Only the
 *    pill asks for steps, and only while it shows.
 * 5. **The badges and the lists.** Amber, then blue, then green. The panel's
 *    "New reply" row. The dot on every chat list.
 *
 * The runner is `environment: "node"`, so markup renders to a string, a fake
 * stands in for `document`, and the shell wiring is read as source.
 */

import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children?: unknown }) =>
    createElement("a", { href, ...rest }, children as never),
}));

import SessionRunDot, { sessionDot } from "@/components/SessionRunDot";
import NavBadge from "@/components/NavBadge";
import { paneBadge } from "@/components/Sidebar";
import { HIDDEN_MS } from "@/lib/liveRuns";
import { activityRows, chatLink, runBadge, runStatusText, type ActivityRow } from "@/lib/runActivity";
import {
  FAVICON_NEEDS,
  FAVICON_REPLY,
  UNREAD_MAX,
  UNREAD_TTL_MS,
  _resetRunSignalsForTests,
  applyTabSignal,
  attentionTitle,
  clearUnread,
  finishedToast,
  getUnread,
  markChatOpen,
  observeRuns,
  onTabVisible,
  parseUnread,
  pillModel,
  pillText,
  pruneUnread,
  type IconLink,
  type ObserveInput,
  type TabDocument,
  type UnreadEntry,
} from "@/lib/runSignals";
import { bindChatScope, chatKey, chatScope, deleteSession } from "@/lib/sessions";
import { ActivityList } from "@/lib/shell/ActivityControl";
import { AgentPillView } from "@/lib/shell/RunSignals";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

class MemoryStorage {
  private map = new Map<string, string>();
  get length(): number {
    return this.map.size;
  }
  key(i: number): string | null {
    return [...this.map.keys()][i] ?? null;
  }
  getItem(k: string): string | null {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.map.set(k, String(v));
  }
  removeItem(k: string): void {
    this.map.delete(k);
  }
  keys(): string[] {
    return [...this.map.keys()];
  }
}

const OWNER = chatScope("vjvarada@fracktal.in", "org-fracktal") as string;
/** The same person, in a second organization. */
const OWNER_ELSEWHERE = chatScope("vjvarada@fracktal.in", "org-dewin") as string;
/** A second member of the same org. */
const COLLEAGUE = chatScope("ops@fracktal.in", "org-fracktal") as string;

const NOW = Date.parse("2026-10-10T12:00:00Z");
const ago = (mins: number) => new Date(NOW - mins * 60_000).toISOString();

let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
  _resetRunSignalsForTests();
  bindChatScope(OWNER);
});

afterEach(() => {
  bindChatScope(null);
  _resetRunSignalsForTests();
  vi.unstubAllGlobals();
});

type Run = { threadId: string; agentName?: string; title?: string | null; startedAt?: string | null };

/** One poll result, seen by the tracker. */
function see(server: Run[], over: Partial<ObserveInput> = {}) {
  return observeRuns({
    generation: 1,
    server,
    localIds: [],
    localAgent: () => undefined,
    localTitle: () => null,
    visible: true,
    now: NOW,
    ...over,
  });
}

const A: Run = { threadId: "t-a", agentName: "task-manager", title: "Plan the week", startedAt: ago(3) };
const B: Run = { threadId: "t-b", agentName: "orchestrator", title: "Draft", startedAt: ago(1) };

// ── 1. The unread rule ───────────────────────────────────────────────────────

describe("a run that ends marks its chat unread", () => {
  it("marks the chat when its run leaves the list, with its agent and title", () => {
    see([A, B]);
    expect(getUnread()).toEqual({});
    see([B]);
    const unread = getUnread();
    expect(Object.keys(unread)).toEqual(["t-a"]);
    expect(unread["t-a"]).toMatchObject({ at: NOW, agent: "task-manager", title: "Plan the week", run: A.startedAt });
  });

  it("stores it in the member's own chat namespace, built by chatKey", () => {
    see([A]);
    see([]);
    expect(storage.keys()).toEqual([`cc-chat::${OWNER}::unread`]);
    expect(chatKey("unread")).toBe(`cc-chat::${OWNER}::unread`);
  });

  it("is scoped by member and by org", () => {
    see([A]);
    see([]);
    expect(Object.keys(getUnread())).toEqual(["t-a"]);
    // The same person in another org reads another map.
    bindChatScope(OWNER_ELSEWHERE);
    expect(getUnread()).toEqual({});
    // Another member of the same org reads another map.
    bindChatScope(COLLEAGUE);
    expect(getUnread()).toEqual({});
    // Back again: the switch deleted nothing.
    bindChatScope(OWNER);
    expect(Object.keys(getUnread())).toEqual(["t-a"]);
  });

  it("writes and reads nothing while no member is bound", () => {
    bindChatScope(null);
    see([A]);
    see([]);
    expect(storage.keys()).toEqual([]);
    expect(getUnread()).toEqual({});
  });

  it("opening the chat clears it", () => {
    see([A, B]);
    see([]);
    expect(Object.keys(getUnread()).sort()).toEqual(["t-a", "t-b"]);
    const release = markChatOpen("t-a");
    expect(Object.keys(getUnread())).toEqual(["t-b"]);
    release();
    expect(clearUnread("t-b")).toBe(true);
    expect(clearUnread("t-b")).toBe(false);
    expect(storage.keys()).toEqual([]);
  });

  it("a chat the member watches end is read: no mark", () => {
    const release = markChatOpen("t-a");
    see([A]);
    see([]);
    expect(getUnread()).toEqual({});
    release();
  });

  it("an open chat of a HIDDEN tab is marked, and the tab showing again clears it", () => {
    const release = markChatOpen("t-a");
    see([A], { visible: false });
    see([], { visible: false });
    expect(Object.keys(getUnread())).toEqual(["t-a"]);
    onTabVisible(NOW);
    expect(getUnread()).toEqual({});
    release();
  });

  it("a new member empties the list, and that is no finish", () => {
    see([A, B]);
    see([], { generation: 2 });
    expect(getUnread()).toEqual({});
  });

  it("a run that only this tab knew can leave its set while it runs: no finish", () => {
    see([], { localIds: ["t-local"] });
    see([]);
    expect(getUnread()).toEqual({});
  });

  it("a run that the server listed, then this tab alone, ends when both drop it", () => {
    see([A], { localIds: ["t-a"] });
    see([], { localIds: ["t-a"] });
    expect(getUnread()).toEqual({});
    see([], { localIds: [] });
    expect(Object.keys(getUnread())).toEqual(["t-a"]);
  });

  it("a deleted chat keeps no dot", () => {
    see([A]);
    see([]);
    deleteSession("t-a");
    expect(getUnread()).toEqual({});
  });

  it("keeps the map small: the newest 50, and nothing older than 7 days", () => {
    const map: Record<string, UnreadEntry> = {};
    for (let i = 0; i < UNREAD_MAX + 5; i++) {
      map[`t-${i}`] = { at: NOW - i * 1000, agent: "x", title: null, run: null, toasted: true };
    }
    map["t-old"] = { at: NOW - UNREAD_TTL_MS - 1, agent: "x", title: null, run: null, toasted: true };
    const kept = pruneUnread(map, NOW);
    expect(Object.keys(kept)).toHaveLength(UNREAD_MAX);
    expect(kept["t-0"]).toBeDefined();
    expect(kept["t-old"]).toBeUndefined();
    expect(kept[`t-${UNREAD_MAX}`]).toBeUndefined();
  });

  it("trusts no stored entry it cannot read", () => {
    expect(parseUnread("not json")).toEqual({});
    expect(parseUnread("[1,2]")).toEqual({});
    expect(parseUnread(JSON.stringify({ a: { at: "x" }, b: { at: 5, agent: 7 } }))).toEqual({
      b: { at: 5, agent: "unknown", title: null, run: null, toasted: false },
    });
  });
});

// ── 2. One toast for each finished run ───────────────────────────────────────

describe("the finished toast", () => {
  it("shows once for a run that ends, and never again for the same list", () => {
    see([A, B]);
    expect(see([B])).toEqual([{ threadId: "t-a", agent: "task-manager", title: "Plan the week" }]);
    expect(see([B])).toEqual([]);
    expect(see([B])).toEqual([]);
    expect(getUnread()["t-a"].toasted).toBe(true);
  });

  it("never shows for the open chat, in a visible tab or a hidden one", () => {
    const release = markChatOpen("t-a");
    see([A]);
    expect(see([])).toEqual([]);
    see([A], { visible: false });
    expect(see([], { visible: false })).toEqual([]);
    expect(onTabVisible(NOW)).toEqual([]);
    release();
  });

  it("waits for a hidden tab, then shows once when it shows", () => {
    see([A], { visible: false });
    expect(see([], { visible: false })).toEqual([]);
    expect(onTabVisible(NOW)).toEqual([{ threadId: "t-a", agent: "task-manager", title: "Plan the week" }]);
    expect(onTabVisible(NOW)).toEqual([]);
  });

  it("a chat opened before the tab shows gets no late toast", () => {
    see([A], { visible: false });
    see([], { visible: false });
    const release = markChatOpen("t-a");
    expect(onTabVisible(NOW)).toEqual([]);
    release();
  });

  it("two tabs that see one run end show one toast", () => {
    // Another tab of this browser saw the run end and showed its toast.
    storage.setItem(
      chatKey("unread") as string,
      JSON.stringify({ "t-a": { at: NOW - 2000, agent: "task-manager", title: "Plan the week", run: A.startedAt, toasted: true } }),
    );
    see([A]);
    expect(see([])).toEqual([]);
    expect(getUnread()["t-a"].at).toBe(NOW - 2000);
  });

  it("a NEW run of the same chat gets its own toast", () => {
    see([A]);
    expect(see([])).toHaveLength(1);
    const again = { ...A, startedAt: ago(0) };
    see([again]);
    expect(see([])).toHaveLength(1);
  });

  it("says '<Agent> finished', names the chat, and offers Open", () => {
    expect(finishedToast({ threadId: "t-a", agent: "task-manager", title: "Plan the week" })).toEqual({
      key: "run-finished:t-a",
      title: "Task manager finished",
      description: "Plan the week",
      actionLabel: "Open",
    });
    expect(finishedToast({ threadId: "t-b", agent: "orchestrator", title: "  " }).title).toBe("Assistant finished");
  });

  it("goes through the shell's one toast, and Open takes the open-chat link", () => {
    const src = read("lib/shell/RunSignals.tsx");
    expect(src).toContain('import { useToast } from "@/components/ui/Toast";');
    expect(src).toMatch(/toast\.show\(\{[\s\S]*?key: t\.key,[\s\S]*?router\.push\(chatLink\(run\.agent, run\.threadId, held\)\)/);
    expect(src).not.toMatch(/@base-ui\/react/);
  });
});

// ── 3. The hidden tab ────────────────────────────────────────────────────────

class FakeLink implements IconLink {
  attrs = new Map<string, string>();
  removed = false;
  constructor(init: Record<string, string> = {}) {
    for (const [k, v] of Object.entries(init)) this.attrs.set(k, v);
  }
  getAttribute(n: string) {
    return this.attrs.get(n) ?? null;
  }
  setAttribute(n: string, v: string) {
    this.attrs.set(n, v);
  }
  remove() {
    this.removed = true;
  }
}

function fakeDoc(links: FakeLink[]): TabDocument & { appended: FakeLink[] } {
  const appended: FakeLink[] = [];
  return {
    title: "Metorite Control Plane",
    visibilityState: "visible",
    querySelectorAll: () => links,
    createElement: () => new FakeLink(),
    head: { appendChild: (n: IconLink) => appended.push(n as FakeLink) },
    appended,
  };
}

describe("the hidden tab's title and favicon", () => {
  it("pins the title text for each state", () => {
    expect(attentionTitle(1, 0)).toBe("(1) Needs you · Metorite");
    expect(attentionTitle(2, 3)).toBe("(2) Needs you · Metorite");
    expect(attentionTitle(0, 1)).toBe("(1) New reply · Metorite");
    expect(attentionTitle(0, 4)).toBe("(4) New reply · Metorite");
    expect(attentionTitle(0, 0)).toBeNull();
  });

  it("a hidden tab shows the count and the dot, and a visible tab gets its own back", () => {
    const icon = new FakeLink({ rel: "icon", href: "/favicon.ico?abc", type: "image/x-icon" });
    const doc = fakeDoc([icon]);
    applyTabSignal(doc, 2, 1);
    // Visible: nothing changes.
    expect(doc.title).toBe("Metorite Control Plane");
    expect(icon.getAttribute("href")).toBe("/favicon.ico?abc");

    doc.visibilityState = "hidden";
    applyTabSignal(doc, 2, 1);
    expect(doc.title).toBe("(2) Needs you · Metorite");
    expect(icon.getAttribute("href")).toBe(FAVICON_NEEDS);
    expect(icon.getAttribute("type")).toBe("image/png");

    // The needs go, a reply waits.
    applyTabSignal(doc, 0, 1);
    expect(doc.title).toBe("(1) New reply · Metorite");
    expect(icon.getAttribute("href")).toBe(FAVICON_REPLY);

    doc.visibilityState = "visible";
    applyTabSignal(doc, 0, 1);
    expect(doc.title).toBe("Metorite Control Plane");
    expect(icon.getAttribute("href")).toBe("/favicon.ico?abc");
    expect(icon.getAttribute("type")).toBe("image/x-icon");
  });

  it("a hidden tab with nothing for the member keeps its own title", () => {
    const doc = fakeDoc([new FakeLink({ rel: "icon", href: "/favicon.ico" })]);
    doc.visibilityState = "hidden";
    applyTabSignal(doc, 0, 0);
    expect(doc.title).toBe("Metorite Control Plane");
    applyTabSignal(doc, 1, 0);
    applyTabSignal(doc, 0, 0);
    expect(doc.title).toBe("Metorite Control Plane");
  });

  it("adds a link when the page has none, and takes it away on restore", () => {
    const doc = fakeDoc([]);
    doc.visibilityState = "hidden";
    applyTabSignal(doc, 0, 2);
    expect(doc.appended).toHaveLength(1);
    expect(doc.appended[0].getAttribute("href")).toBe(FAVICON_REPLY);
    doc.visibilityState = "visible";
    applyTabSignal(doc, 0, 2);
    expect(doc.appended[0].removed).toBe(true);
  });

  it("swaps to pre-made files, and draws no canvas", () => {
    for (const f of [FAVICON_NEEDS, FAVICON_REPLY]) {
      expect(existsSync(fileURLToPath(new URL(`../../public${f}`, import.meta.url))), f).toBe(true);
    }
    for (const f of ["lib/runSignals.ts", "lib/shell/RunSignals.tsx"]) {
      expect(read(f)).not.toMatch(/getContext|<canvas|OffscreenCanvas|toDataURL/);
    }
  });

  it("keeps the hidden-tab 30 s poll: the signals poll nothing of their own", () => {
    expect(HIDDEN_MS).toBe(30_000);
    for (const f of ["lib/runSignals.ts", "lib/shell/RunSignals.tsx"]) {
      const src = read(f);
      expect(src).not.toMatch(/\bfetch\(|setInterval\(|subscribeLiveRuns/);
    }
  });

  it("the host restores the tab when the tab shows, and when it goes", () => {
    const src = read("lib/shell/RunSignals.tsx");
    expect(src).toMatch(/document\.addEventListener\("visibilitychange", apply\)/);
    expect(src).toContain("applyTabSignal(tabDocument(), 0, 0)");
  });
});

// ── 4. The phone pill ────────────────────────────────────────────────────────

const row = (over: Partial<ActivityRow> & { threadId: string }): ActivityRow => ({
  agentName: "task-manager",
  title: null,
  startedAt: ago(1),
  state: "running",
  lastStep: null,
  ...over,
});

describe("the phone's agent pill", () => {
  const NONE: ReadonlySet<string> = new Set();

  it("hides with no live run, and a new reply alone is no live run", () => {
    expect(pillModel([], NONE)).toBeNull();
    expect(pillModel([row({ threadId: "t-r", state: "new_reply" })], NONE)).toBeNull();
  });

  it("shows the newest run with its step, or Working", () => {
    const m = pillModel([row({ threadId: "t-1", lastStep: "Search tasks" }), row({ threadId: "t-2" })], NONE);
    expect(m?.row.threadId).toBe("t-1");
    expect(m?.text).toBe("Task manager · Search tasks");
    expect(pillText(row({ threadId: "x", agentName: "orchestrator" }))).toBe("Assistant · Working");
    expect(pillText(row({ threadId: "x", state: "needs_input", lastStep: "Ignored" }))).toBe("Task manager · Needs your answer");
  });

  it("counts the other runs as +N", () => {
    const m = pillModel([row({ threadId: "t-1" }), row({ threadId: "t-2" }), row({ threadId: "t-3" })], NONE);
    expect(m?.more).toBe(2);
    const html = renderToStaticMarkup(createElement(AgentPillView, { model: m!, href: "/chat" }));
    expect(html).toContain("+2");
    expect(html).toContain("and 2 more");
    const one = pillModel([row({ threadId: "t-1" })], NONE);
    expect(renderToStaticMarkup(createElement(AgentPillView, { model: one!, href: "/chat" }))).not.toContain(
      "data-agent-pill-more",
    );
  });

  it("hides the open chat, and shows the next run in its place", () => {
    const rows = [row({ threadId: "t-1" }), row({ threadId: "t-2", lastStep: "Read mail" })];
    expect(pillModel(rows, new Set(["t-1"]))?.text).toBe("Task manager · Read mail");
    expect(pillModel(rows, new Set(["t-1"]))?.more).toBe(0);
    expect(pillModel([rows[0]], new Set(["t-1"]))).toBeNull();
  });

  it("links to its chat through the open-chat job", () => {
    const m = pillModel([row({ threadId: "t-1" })], NONE)!;
    const href = chatLink(m.row.agentName, m.row.threadId, null);
    const html = renderToStaticMarkup(createElement(AgentPillView, { model: m, href }));
    expect(href).toBe("/tasks?do=open-chat&fill.session=t-1");
    expect(html).toContain('href="/tasks?do=open-chat&amp;fill.session=t-1"');
    expect(read("lib/shell/RunSignals.tsx")).toContain(
      "<AgentPillView model={model} href={chatLink(model.row.agentName, model.row.threadId, heldHrefs)} />",
    );
  });

  it("wears tokens only, z-50, the safe-area clearance, and moves only with motion allowed", () => {
    const m = pillModel([row({ threadId: "t-1" })], NONE)!;
    const html = renderToStaticMarkup(createElement(AgentPillView, { model: m, href: "/chat" }));
    expect(html).toContain("agent-pill-bottom");
    expect(html).toContain("z-50");
    expect(html).toContain("agent-pill-progress");
    expect(html).toContain("motion-safe:animate-pulse");
    expect(html).not.toMatch(/(^|[\s"])animate-pulse/);
    expect(html).not.toMatch(/#[0-9a-f]{3,8}\b|\b(bg|text|border)-(red|blue|green|amber|sky|yellow|slate|gray|zinc)-\d/);
    const css = read("app/globals.css");
    expect(css).toMatch(/\.agent-pill-bottom \{\s*bottom: calc\(3\.5rem \+ env\(safe-area-inset-bottom, 0px\) \+ 0\.5rem\);/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: no-preference\) \{\s*\.agent-pill-progress \{\s*animation:/);
    // The only animation of the line sits inside the motion query.
    expect(css.match(/animation: agent-pill-progress/g)).toHaveLength(1);
    // The toasts and the page's last row rise above the pill while it shows.
    expect(css).toMatch(/:root\[data-agent-pill\] \.toast-viewport-bottom \{/);
    expect(css).toMatch(/:root\[data-agent-pill\] \.pb-nav \{/);
  });

  it("only the phone layout mounts the pill, and every layout the host", () => {
    const shell = read("components/AppShell.tsx");
    expect(shell.match(/<AgentPill \/>/g)).toHaveLength(1);
    expect(shell.match(/<RunSignalsHost \/>/g)).toHaveLength(2);
    const mobile = shell.slice(shell.indexOf("// ── Mobile layout"));
    expect(mobile).toContain("<AgentPill />");
  });

  it("only the pill asks for steps, and only while it shows in a visible tab", () => {
    const src = read("lib/shell/RunSignals.tsx");
    const pill = src.slice(src.indexOf("export function AgentPill()"));
    expect(src.match(/wantLiveSteps\(\)/g)).toHaveLength(1);
    expect(pill).toContain("wantLiveSteps()");
    expect(pill).toMatch(/if \(!shown \|\| typeof document === "undefined"\) return;/);
    expect(pill).toMatch(/if \(tabVisible\(\) && !release\) release = wantLiveSteps\(\);/);
  });
});

// ── 5. The badges and the lists ──────────────────────────────────────────────

describe("the badge, the panel and the chat lists show a new reply", () => {
  it("amber, then blue, then green", () => {
    expect(runBadge(3, 1, 2)).toEqual({ count: 1, tone: "warning", label: "1 assistant needs your answer" });
    expect(runBadge(3, 0, 2)).toEqual({ count: 2, tone: "info", label: "2 new replies" });
    expect(runBadge(3, 0, 0)).toEqual({ count: 3, tone: "success", label: "3 assistants running" });
    expect(runBadge(0, 0, 1)).toEqual({ count: 1, tone: "info", label: "1 new reply" });
    expect(runBadge(0, 0, 0)).toBeNull();
  });

  it("the sidebar pane and the blue badge", () => {
    expect(paneBadge("/tasks", 0, { "/tasks": 2 }, {}, { "/tasks": 1 })).toEqual({
      badge: 1,
      badgeTone: "info",
      badgeLabel: "1 new reply",
    });
    const html = renderToStaticMarkup(createElement(NavBadge, { count: 1, tone: "info", label: "1 new reply" }));
    expect(html).toContain("bg-info text-info-foreground");
    expect(html).not.toContain("animate-pulse");
  });

  it("counts unread chats on their pane, in the shell and the sidebar", () => {
    const hook = read("hooks/useActiveSessions.ts");
    expect(hook).toContain("unreadByApp: countRunsByApp(unreadRefs, visibleHrefs)");
    expect(read("components/Sidebar.tsx")).toContain(
      "runBadge(runCounts[href] ?? 0, needsCounts[href] ?? 0, unreadCounts[href] ?? 0)",
    );
    expect(read("lib/shell/ActivityControl.tsx")).toContain("unread={unreadTotal}");
  });

  it("the panel lists a 'New reply' row for a chat that runs no more", () => {
    const unread = { "t-r": { at: NOW - 5 * 60_000, agent: "task-manager", title: "Plan" }, "t-a": { at: NOW, agent: "x", title: null } };
    const rows = activityRows([{ threadId: "t-a", agentName: "orchestrator", startedAt: ago(1) }], [], () => undefined, () => null, unread);
    // A chat that runs again shows its live row only.
    expect(rows.map((r) => [r.threadId, r.state])).toEqual([
      ["t-a", "running"],
      ["t-r", "new_reply"],
    ]);
    expect(runStatusText(rows[1], NOW)).toBe("New reply · 5 min ago");
    expect(runStatusText({ ...rows[1], finishedAt: NOW }, NOW)).toBe("New reply · just now");
    const html = renderToStaticMarkup(createElement(ActivityList, { rows, now: NOW, visibleHrefs: null }));
    expect(html).toContain('data-activity-row="new_reply"');
    expect(html).toContain("bg-info");
    expect(html).toContain("New reply · 5 min ago");
    expect(html).toContain('href="/tasks?do=open-chat&amp;fill.session=t-r"');
  });

  it("the row dot: a live run wins, then an unread reply", () => {
    expect(sessionDot(true, true)).toBe("running");
    expect(sessionDot(false, true)).toBe("unread");
    expect(sessionDot(false, false)).toBeNull();
    const html = renderToStaticMarkup(createElement(SessionRunDot, { running: false, unread: true }));
    expect(html).toContain('aria-label="New reply"');
    expect(html).toContain("bg-info");
    expect(renderToStaticMarkup(createElement(SessionRunDot, { running: false, unread: false }))).toBe("");
  });

  it.each([
    "app/chat/page.tsx",
    "app/tasks/components/AssistantRail.tsx",
    "app/projects/components/AssistantRail.tsx",
    "app/email/components/EmailAssistantChat.tsx",
  ])("%s draws the one dot with the member's unread chats", (file) => {
    const src = read(file);
    expect(src).toContain("const unreadIds = useUnreadIds();");
    expect(src).toMatch(/<SessionRunDot running=\{activeRunIds\.has\(s\.id\)\} unread=\{unreadIds\.has\(s\.id\)\}/);
  });

  it("an open chat clears its mark: AgentChat holds its thread open", () => {
    expect(read("components/AgentChat.tsx")).toMatch(/^\s*useChatOpen\(sessionId\);$/m);
  });
});
