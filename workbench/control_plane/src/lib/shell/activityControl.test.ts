/**
 * The top-right activity control and its panel (WS-51 S3,
 * `project-docs/specs/chat_run_continuity.md` §4 S3).
 *
 * The runner is `environment: "node"`, so markup is rendered to a string and
 * the shell wiring is read as source. What this file holds (R7):
 *
 * 1. The control's three states come from `runBadge`: quiet, a green count,
 *    and an amber count that wins over the green.
 * 2. The panel's rows render, newest first, with the three row texts.
 * 3. A row links to its chat: in its own app for Projects, My Tasks and Email,
 *    in Chat for every other agent, and in Chat for a pane the member cannot
 *    see. Every app a link names declares the job that opens it.
 * 4. The panel is a `Modal`, so Escape closes it and returns focus. Its every
 *    dismissal calls the caller's `onClose`.
 * 5. The empty state.
 * 6. The step is plain text, never HTML.
 * 7. The shell owns the control, with the shell bar flag on OR off: the bar
 *    draws it when the flag is on, the sidebar head when it is off, and the
 *    phone's Menu drawer always. AppShell mounts the one panel in every
 *    layout. The open-chat job listens with the flag off too.
 * 8. The step costs a stream read, so the poll asks for it only while the
 *    panel is open (`?steps=1`), and the BFF passes on nothing else.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children?: unknown }) =>
    createElement("a", { href, ...rest }, children as never),
}));

/** The props the panel last handed to `Modal`. */
const modalSeen: { props: Record<string, unknown> | null } = { props: null };
vi.mock("@/components/ui/Modal", () => ({
  default: (props: Record<string, unknown> & { children?: unknown }) => {
    modalSeen.props = props;
    return createElement("div", { "data-modal": String(props.placement) }, props.children as never);
  },
}));
import { NAV_SECTIONS } from "@/lib/nav";
import {
  CHAT_IN_APP,
  OPEN_CHAT_JOB,
  activityRows,
  chatLink,
  elapsedLabel,
  runStatusText,
  type ActivityRow,
} from "@/lib/runActivity";

import { activeSessionsPath } from "@/lib/activeSessionsPath";
import { _resetLiveRunsForTests, pollUrl, wantLiveSteps } from "@/lib/liveRuns";

import {
  ActivityButton,
  ActivityList,
  ActivityPanel,
  NO_RUNS_TEXT,
  activityControlLabel,
} from "./ActivityControl";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

const NOW = Date.parse("2026-10-10T12:00:00Z");
const ago = (mins: number) => new Date(NOW - mins * 60_000).toISOString();

const control = (running: number, needsInput: number) =>
  renderToStaticMarkup(
    createElement(ActivityButton, { running, needsInput, open: false, onOpen: () => {} }),
  );

const list = (rows: ActivityRow[], visibleHrefs: ReadonlySet<string> | null = null) =>
  renderToStaticMarkup(createElement(ActivityList, { rows, now: NOW, visibleHrefs }));

const row = (over: Partial<ActivityRow>): ActivityRow => ({
  threadId: "t",
  agentName: "orchestrator",
  title: "A chat",
  startedAt: ago(2),
  state: "running",
  lastStep: null,
  ...over,
});

// ── 1. The control ──────────────────────────────────────────────────────────

describe("the control wears the one run badge", () => {
  it("is quiet when nothing runs: the icon alone", () => {
    const html = control(0, 0);
    expect(html).toContain('data-activity-control="quiet"');
    expect(html).not.toContain("data-nav-badge");
    expect(html).toContain('aria-label="Assistants: none running"');
  });

  it("counts the running runs in green", () => {
    const html = control(2, 0);
    expect(html).toContain('data-activity-control="success"');
    expect(html).toContain('data-nav-badge="success"');
    expect(html).toContain("bg-success");
    expect(html).not.toContain("bg-warning");
    expect(html).toContain(">2<");
    expect(html).toContain('aria-label="Assistants: 2 assistants running"');
  });

  // Mutation: let green win in `runBadge`, and this fails.
  it("turns amber when a run needs the member, and amber wins over green", () => {
    const html = control(3, 1);
    expect(html).toContain('data-activity-control="warning"');
    expect(html).toContain('data-nav-badge="warning"');
    expect(html).toContain("bg-warning");
    expect(html).not.toContain("bg-success");
    expect(html).toContain(">1<");
    expect(activityControlLabel(3, 1)).toBe("Assistants: 1 assistant needs your answer");
  });

  it("says that it opens a dialog, and whether it is open", () => {
    const html = control(1, 0);
    expect(html).toContain('aria-haspopup="dialog"');
    expect(html).toContain('aria-expanded="false"');
    const open = renderToStaticMarkup(
      createElement(ActivityButton, { running: 1, needsInput: 0, open: true, onOpen: () => {} }),
    );
    expect(open).toContain('aria-expanded="true"');
  });

  it("draws the agent icon from the icon set, with no colour of its own", () => {
    const src = read("lib/shell/ActivityControl.tsx");
    expect(src).toMatch(/icon="Bot"/);
    expect(src).not.toMatch(/lucide-react/);
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,6}\b|\b(?:bg|text)-(?:green|amber|yellow|emerald)-\d/);
  });
});

// ── 2. The rows and their order ─────────────────────────────────────────────

describe("the panel lists every live run, newest first", () => {
  it("orders the server's rows by start, newest first, and a fresh local run first of all", () => {
    const rows = activityRows(
      [
        { threadId: "old", agentName: "task-manager", startedAt: ago(30), title: "Old" },
        { threadId: "new", agentName: "orchestrator", startedAt: ago(1), title: "New" },
        { threadId: "parked", agentName: "email-assistant", state: "needs_input", title: "Parked" },
        { threadId: "mid", agentName: "projects-assistant", startedAt: ago(10), title: "Mid" },
      ],
      ["local", "new"],
      (id) => (id === "local" ? "task-manager" : undefined),
      (id) => (id === "local" ? "Just sent" : null),
    );
    expect(rows.map((r) => r.threadId)).toEqual(["local", "new", "mid", "old", "parked"]);
    expect(rows[0]).toMatchObject({ agentName: "task-manager", title: "Just sent", state: "running" });
    expect(rows.find((r) => r.threadId === "parked")?.state).toBe("needs_input");
  });

  it("fills an unknown agent from this tab's own record", () => {
    const rows = activityRows([{ threadId: "t1", agentName: "unknown" }], [], () => "email-assistant");
    expect(rows[0].agentName).toBe("email-assistant");
  });

  it("renders each row's agent, app, title and status, in order", () => {
    const html = list([
      row({ threadId: "a", agentName: "task-manager", title: "Plan my week", lastStep: "Search tasks" }),
      row({ threadId: "b", agentName: "projects-assistant", title: "Q4 board", startedAt: ago(5) }),
      row({ threadId: "c", agentName: "email-assistant", title: "Reply to Ravi", state: "needs_input" }),
    ]);
    const at = (s: string) => html.indexOf(s);
    expect(at("Plan my week")).toBeGreaterThan(-1);
    expect(at("Plan my week")).toBeLessThan(at("Q4 board"));
    expect(at("Q4 board")).toBeLessThan(at("Reply to Ravi"));
    expect(html).toContain("Task manager · My Tasks");
    expect(html).toContain("Projects assistant · Projects");
    expect(html).toContain("Email assistant · My Email");
    expect(html).toContain("Running · 2 min · Search tasks");
    expect(html).toContain("Running · 5 min");
    expect(html).toContain("Needs your answer");
    expect(html).toContain('data-activity-row="needs_input"');
    expect(html.match(/data-activity-row=/g)?.length).toBe(3);
  });

  it("names a chat with no title, and the orchestrator as the assistant", () => {
    const html = list([row({ title: null, agentName: "orchestrator" })]);
    expect(html).toContain("Untitled chat");
    expect(html).toContain("Assistant · Chat");
  });

  it("writes the status of each state", () => {
    expect(runStatusText({ state: "needs_input", startedAt: ago(9), lastStep: "x" }, NOW)).toBe(
      "Needs your answer",
    );
    expect(runStatusText({ state: "running", startedAt: ago(0), lastStep: null }, NOW)).toBe(
      "Running · under 1 min",
    );
    expect(runStatusText({ state: "running", startedAt: null, lastStep: "Thinking" }, NOW)).toBe(
      "Running · Thinking",
    );
    expect(elapsedLabel(ago(65), NOW)).toBe("1 h 5 min");
    expect(elapsedLabel(ago(60 * 50), NOW)).toBe("2 d");
    expect(elapsedLabel("not a date", NOW)).toBeNull();
  });
});

// ── 3. The links ────────────────────────────────────────────────────────────

describe("a row opens its chat in its app", () => {
  const job = (app: string, id: string) => `${app}?do=${OPEN_CHAT_JOB}&fill.session=${id}`;

  // Mutation: send every row to Chat, and this fails.
  it.each([
    ["orchestrator", "/chat"],
    ["task-manager", "/tasks"],
    ["projects-assistant", "/projects"],
    ["email-assistant", "/email"],
    ["app-builder", "/chat"], // App Workshop opens a chat per app, not by id
    ["research-agent", "/chat"],
    ["unknown", "/chat"],
  ])("%s opens in %s", (agent, app) => {
    expect(chatLink(agent, "t-1")).toBe(job(app, "t-1"));
  });

  it("a run on a pane the member cannot see opens in Chat", () => {
    expect(chatLink("task-manager", "t-1", new Set(["/chat", "/projects"]))).toBe(job("/chat", "t-1"));
  });

  it("escapes the thread id", () => {
    expect(chatLink("orchestrator", "a b&c")).toBe(job("/chat", "a%20b%26c"));
  });

  it("the rendered row carries the link", () => {
    const html = list([row({ threadId: "t-9", agentName: "task-manager" })]);
    expect(html).toContain(`href="/tasks?do=${OPEN_CHAT_JOB}&amp;fill.session=t-9"`);
  });

  it("every app a link can name declares the job that opens the chat", () => {
    const pages: Record<string, string> = {
      "/chat": "app/chat/page.tsx",
      "/tasks": "app/tasks/page.tsx",
      "/projects": "app/projects/page.tsx",
      "/email": "app/email/page.tsx",
    };
    for (const app of [...CHAT_IN_APP, "/chat"]) {
      const file = pages[app];
      expect(file, `no page known for ${app}`).toBeTruthy();
      expect(read(file), `${file} does not open a chat from a link`).toMatch(
        /<ShellJob\s+id=\{OPEN_CHAT_JOB\}\s+ungated\b/,
      );
    }
    // The three rails hand the asked chat to their own session hook.
    expect(read("app/tasks/page.tsx")).toMatch(/askRailSession\("task-manager"/);
    expect(read("app/email/page.tsx")).toMatch(/askRailSession\("email-assistant"/);
    expect(read("app/projects/page.tsx")).toMatch(/askRailSession\(PROJECTS_AGENT/);
  });

  it("every in-app chat is a pane the manifest maps an agent to", () => {
    const agentPanes = new Set(NAV_SECTIONS.flatMap((s) => s.items.filter((p) => p.agent).map((p) => p.href)));
    for (const app of CHAT_IN_APP) expect(agentPanes.has(app)).toBe(true);
  });
});

// ── 4. Keyboard and focus ───────────────────────────────────────────────────

describe("the panel closes from the keyboard", () => {
  // Mutation: drop `onClose` from the Modal, and this fails.
  it("is a Modal, and hands the caller's onClose to it", () => {
    const onClose = vi.fn();
    modalSeen.props = null;
    const html = renderToStaticMarkup(
      createElement(ActivityPanel, { open: true, onClose, rows: [], visibleHrefs: null }),
    );
    expect(html).toContain('data-modal="end"');
    expect(html).toContain(NO_RUNS_TEXT);
    const props = modalSeen.props as { open: boolean; onClose: () => void; title: string } | null;
    expect(props?.open).toBe(true);
    expect(props?.title).toBe("Assistants");
    props?.onClose();
    expect(onClose).toHaveBeenCalledTimes(1);
    // The phone's sheet is the same panel.
    renderToStaticMarkup(
      createElement(ActivityPanel, { open: true, onClose, rows: [], visibleHrefs: null, placement: "sheet" }),
    );
    expect((modalSeen.props as unknown as { placement: string }).placement).toBe("sheet");
  });

  it("the panel imports the one Modal primitive", () => {
    const src = read("lib/shell/ActivityControl.tsx");
    expect(src).toMatch(/import Modal, \{ type ModalPlacement \} from "@\/components\/ui\/Modal";/);
    expect(src).not.toMatch(/@base-ui\/react|fixed inset-0/);
  });

  it("the Modal routes every dismissal, Escape among them, to onClose", () => {
    // Base UI calls onOpenChange(false) for Escape and an outside press.
    // `e2e/modal.spec.ts` drives the real key in a browser.
    const modal = read("components/ui/Modal.tsx");
    expect(modal).toMatch(/onOpenChange=\{\(next\) => \{\s*if \(!next\) onClose\(\);/);
    expect(modal).toMatch(/\n\s+modal\n/);
  });

  it("a tap on a row closes the panel as it navigates", () => {
    const src = read("lib/shell/ActivityControl.tsx");
    expect(src).toMatch(/onNavigate=\{onClose\}/);
    expect(src).toMatch(/onClick=\{onNavigate\}/);
  });
});

// ── 5. The empty state ──────────────────────────────────────────────────────

describe("the empty state", () => {
  it("says that no assistant runs", () => {
    const html = list([]);
    expect(html).toContain(NO_RUNS_TEXT);
    expect(NO_RUNS_TEXT).toBe("No assistants are running.");
    expect(html).not.toContain("data-activity-row");
  });
});

// ── 6. The step is text ─────────────────────────────────────────────────────

describe("the step is plain text", () => {
  it("draws markup in a step as text, never as an element", () => {
    const html = list([row({ lastStep: '<img src=x onerror="alert(1)">' })]);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(read("lib/shell/ActivityControl.tsx")).not.toMatch(/dangerouslySetInnerHTML/);
  });

  it("cuts a long step to one short line", () => {
    const text = runStatusText({ state: "running", startedAt: null, lastStep: "y".repeat(200) }, NOW);
    expect(text.length).toBeLessThanOrEqual("Running · ".length + 60);
  });
});

// ── 7. The shell owns it ────────────────────────────────────────────────────

describe("the shell owns the control, with the shell bar on or off", () => {
  it("the shell bar draws it after the app's tools", () => {
    const bar = read("lib/shell/ShellBar.tsx");
    expect(bar).toMatch(/activity=\{<ActivityControl \/>\}/);
    expect(bar.indexOf("ref={setRight}")).toBeLessThan(bar.indexOf("{activity}"));
    expect(bar).not.toMatch(/<ActivityPanel\b|<ActivityHost\b/);
  });

  // Review of #815. Mutation: drop the sidebar's control, and with the shell
  // bar flag off the desktop shows nothing.
  it("with the shell bar off, the sidebar head draws it", () => {
    const side = read("components/Sidebar.tsx");
    expect(side).toMatch(/\{!barOn \? <ActivityControl \/> : null\}/);
    expect(side).toMatch(/useState\(\(\) => shellBarOn\(\)\)/);
  });

  it("AppShell mounts the one panel in every layout, with no flag around it", () => {
    const shell = read("components/AppShell.tsx");
    expect(shell.match(/<ActivityHost\b/g)?.length).toBe(2); // desktop, phone
    expect(shell).toMatch(/<ActivityHost placement=\{frame === "classic" \? "top" : "end"\} \/>/);
    expect(shell).toMatch(/<ActivityHost placement="sheet" \/>/);
    // The phone drawer draws the control outside the shell bar's gate.
    const drawer = shell.slice(shell.indexOf("NS-1 on the phone"));
    expect(drawer.indexOf("<ActivityControl onBeforeOpen={close}")).toBeGreaterThan(-1);
    expect(drawer.indexOf("<ActivityControl")).toBeLessThan(drawer.indexOf("<nav"));
  });

  it("the open-chat job listens with the shell bar off", () => {
    const job = read("lib/shell/doJob.tsx");
    expect(job).toMatch(/if \(!props\.ungated && !shellBarOn\(\)\) return null;/);
  });

  it("reads the S1 store and polls nothing", () => {
    for (const file of ["lib/shell/ActivityControl.tsx", "lib/shell/ShellBar.tsx"]) {
      const src = read(file);
      expect(src).not.toMatch(/\/api\/chat\/active-sessions|fetch\(/);
    }
    const src = read("lib/shell/ActivityControl.tsx");
    expect(src).toMatch(/useRunActivity\(null, workspace\)/);
    expect(src).toMatch(/useActivityRows\(workspace\)/);
  });

  it("no app draws its own copy", () => {
    // The control is imported only by the shell: the bar, the sidebar head
    // and the phone shell.
    const allowed = new Set(["lib/shell/ShellBar.tsx", "components/AppShell.tsx", "components/Sidebar.tsx"]);
    const importers = (
      [
        "app/chat/page.tsx",
        "app/tasks/page.tsx",
        "app/projects/page.tsx",
        "app/email/page.tsx",
        "components/Sidebar.tsx",
      ] as const
    ).filter((f) => /from "@\/lib\/shell\/ActivityControl"/.test(read(f)));
    expect(importers.filter((f) => !allowed.has(f))).toEqual([]);
  });
});

// ── 8. The step is asked for only while the panel is open ───────────────────

describe("the step is asked for only while the panel is open", () => {
  // Review of #815. Mutation: always send steps=1, and this fails.
  it("the poll asks for steps only while a surface wants them", () => {
    _resetLiveRunsForTests();
    expect(pollUrl()).toBe("/api/chat/active-sessions");
    const release = wantLiveSteps();
    expect(pollUrl()).toBe("/api/chat/active-sessions?steps=1");
    const second = wantLiveSteps();
    release();
    expect(pollUrl()).toBe("/api/chat/active-sessions?steps=1");
    second();
    second(); // a second release changes nothing
    expect(pollUrl()).toBe("/api/chat/active-sessions");
  });

  it("the open panel asks, and lets go when it closes", () => {
    const src = read("lib/shell/ActivityControl.tsx");
    expect(src).toMatch(/if \(!open\) return;\s*\/\/[^\n]*\n\s*const release = wantLiveSteps\(\);/);
    expect(src).toMatch(/return \(\) => \{\s*release\(\);/);
  });

  it("the BFF passes steps=1 on, and nothing else", () => {
    expect(activeSessionsPath("http://x/api/chat/active-sessions")).toBe("/chat/active-sessions");
    expect(activeSessionsPath("http://x/api/chat/active-sessions?steps=1")).toBe("/chat/active-sessions?steps=1");
    expect(activeSessionsPath("http://x/a?steps=1&org=other")).toBe("/chat/active-sessions?steps=1");
    expect(activeSessionsPath("http://x/a?steps=true")).toBe("/chat/active-sessions");
    expect(activeSessionsPath(undefined)).toBe("/chat/active-sessions");
    expect(read("app/api/chat/active-sessions/route.ts")).toMatch(/activeSessionsPath\(req\.url\)/);
  });
});
