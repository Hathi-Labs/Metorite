/**
 * The run badge on the nav (WS-51 S1, `chat_run_continuity.md` §4 S1).
 *
 * The desktop sidebar, the phone drawer and the phone's bottom bar draw one
 * component, `NavBadge`. A running count is `success`, and `warning` is
 * "needs you": a run that waits on the member's answer (WS-51 S2), which wins
 * over the green count on the same pane. The runner is `environment:
 * "node"`, so markup is rendered to a string and the phone shell is read as
 * source.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import NavBadge from "./NavBadge";
import { NavLink, paneBadge } from "./Sidebar";
import type { NavPane } from "@/lib/nav";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

describe("NavBadge", () => {
  it("draws a running count in the success tone, with its spoken name", () => {
    const html = renderToStaticMarkup(
      createElement(NavBadge, { count: 2, tone: "success", label: "2 assistants running" }),
    );
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-label="2 assistants running"');
    expect(html).toContain("bg-success text-success-foreground");
    expect(html).not.toContain("bg-warning");
    expect(html).toContain(">2<");
  });

  it("pulses only when the member allows motion", () => {
    const html = renderToStaticMarkup(
      createElement(NavBadge, { count: 1, tone: "success", label: "1 assistant running" }),
    );
    expect(html).toContain("motion-safe:animate-pulse");
    expect(html).not.toMatch(/(^|[\s"])animate-pulse/);
  });

  it("draws nothing for zero", () => {
    expect(
      renderToStaticMarkup(createElement(NavBadge, { count: 0, tone: "success", label: "x" })),
    ).toBe("");
  });

  it("caps a large count", () => {
    const html = renderToStaticMarkup(
      createElement(NavBadge, { count: 12, tone: "success", label: "12 assistants running" }),
    );
    expect(html).toContain(">9+<");
  });
});

describe("the sidebar wears the run badge", () => {
  const chat: NavPane = {
    href: "/chat",
    team: "studio",
    label: "Chat",
    icon: "MessageCircle",
    note: "",
    launch: "live",
  };
  const link = (collapsed: boolean, badge: ReturnType<typeof paneBadge>) =>
    renderToStaticMarkup(
      createElement(NavLink, { pane: chat, pathname: "/projects", collapsed, onNavigate: () => {}, ...badge }),
    );

  it("a pane with runs gets a success count, /agents keeps its warning", () => {
    expect(paneBadge("/chat", 4, { "/chat": 2 })).toEqual({
      badge: 2,
      badgeTone: "success",
      badgeLabel: "2 assistants running",
    });
    expect(paneBadge("/agents", 3, { "/chat": 2 })).toMatchObject({ badge: 3, badgeTone: "warning" });
    expect(paneBadge("/agents", 0, {})).toEqual({});
    expect(paneBadge("/projects", 0, { "/chat": 1 })).toEqual({});
  });

  it.each([false, true])("renders in %s collapsed mode", (collapsed) => {
    const html = link(collapsed, paneBadge("/chat", 0, { "/chat": 2 }));
    expect(html).toContain('aria-label="2 assistants running"');
    expect(html).toContain("bg-success text-success-foreground");
    expect(html).not.toContain("bg-warning");
  });

  it("names the pane inside a collapsed link that has a badge", () => {
    const html = link(true, paneBadge("/chat", 0, { "/chat": 1 }));
    expect(html).toContain('<span class="sr-only">Chat</span>');
  });

  it("draws no badge for a pane with no runs", () => {
    expect(link(false, paneBadge("/chat", 0, {}))).not.toContain("data-nav-badge");
  });

  // WS-51 S2. Mutation: pass no needs counts to `runBadge` in paneBadge, and
  // these fail.
  it("a pane whose run needs the member wears amber in place of green", () => {
    expect(paneBadge("/chat", 0, { "/chat": 3 }, { "/chat": 1 })).toEqual({
      badge: 1,
      badgeTone: "warning",
      badgeLabel: "1 assistant needs your answer",
    });
    // Another pane's wait leaves this pane green.
    expect(paneBadge("/chat", 0, { "/chat": 2 }, { "/tasks": 1 })).toMatchObject({ badgeTone: "success" });
  });

  it.each([false, true])("draws the amber count in %s collapsed mode, with no pulse", (collapsed) => {
    const html = link(collapsed, paneBadge("/chat", 0, { "/chat": 2 }, { "/chat": 1 }));
    expect(html).toContain('aria-label="1 assistant needs your answer"');
    expect(html).toContain("bg-warning text-warning-foreground");
    expect(html).not.toContain("bg-success");
    expect(html).not.toContain("animate-pulse");
  });
});

describe("the phone nav wears the run badge on every page", () => {
  const shell = read("./AppShell.tsx");

  it("reads the counts from the shared poller", () => {
    expect(shell).toContain("useRunActivity(drawerHrefs)");
    expect(shell).not.toContain("useActiveSessions()");
  });

  it("the Menu tab carries the total off /chat, and the Chats tab on it", () => {
    expect(shell).toContain("const tabBadge = runBadge(activeCount, needsTotal, unreadTotal);");
    expect(shell).toMatch(
      /<AppIcon name="Menu" size=\{20\} \/>\s*\{!isChatPage && tabBadge && \(\s*<NavBadge count=\{tabBadge\.count\} tone=\{tabBadge\.tone\} label=\{tabBadge\.label\} placement="tab"/,
    );
    expect(shell).toMatch(
      /<AppIcon name="MessageCircle" size=\{20\} \/>\s*\{tabBadge && \(\s*<NavBadge count=\{tabBadge\.count\} tone=\{tabBadge\.tone\} label=\{tabBadge\.label\} placement="tab"/,
    );
  });

  it("each drawer link carries its app's count, amber when a run there waits", () => {
    expect(shell).toContain(
      "const drawerBadge = (href: string) => runBadge(runCounts[href] ?? 0, needsCounts[href] ?? 0, unreadCounts[href] ?? 0);",
    );
    expect(shell).toMatch(/const b = drawerBadge\(p\.href\);\s*return b \? <NavBadge count=\{b\.count\} tone=\{b\.tone\} label=\{b\.label\} \/> : null;/);
  });

  it("no phone badge hard-codes its tone", () => {
    expect(shell).not.toMatch(/<NavBadge[^>]*tone="success"/);
  });

  it("draws no hand-rolled count", () => {
    expect(shell).not.toMatch(/rounded-full bg-success text-success-foreground/);
  });
});
