/**
 * WS-27bm S8 — the one auto-open rule, shared by `/chat` and the Projects
 * rail (spec `projects_ai_chat.md` §14).
 *
 * The rule is a pure function with its side-panel calls injected, so these
 * cases drive it in the node environment. The last block reads the two chats'
 * source: it fails if either grows its own copy again, or if the rail stops
 * pruning the panel to its own session.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import {
  LIVE_BADGE_MS,
  autoOpenArtifact,
  shouldAutoOpen,
  syncPanelToSession,
} from "./autoOpenArtifact";

function deps() {
  const scheduled: { fn: () => void; ms: number }[] = [];
  return {
    openDoc: vi.fn(),
    setDocLive: vi.fn(),
    schedule: vi.fn((fn: () => void, ms: number) => {
      scheduled.push({ fn, ms });
    }),
    scheduled,
  };
}

describe("shouldAutoOpen", () => {
  it("opens the documents a member reads: Markdown and HTML", () => {
    for (const path of ["outputs/status.md", "outputs/notes.mdx", "outputs/page.html", "outputs/p.htm"]) {
      expect(shouldAutoOpen(path, false), path).toBe(true);
    }
  });

  it("opens a React artifact under outputs/ and nowhere else", () => {
    expect(shouldAutoOpen("outputs/dash.jsx", false)).toBe(true);
    expect(shouldAutoOpen("src/components/Foo.tsx", false)).toBe(false);
  });

  it("does not open an image, a CSV or a PDF", () => {
    for (const path of ["outputs/chart.png", "outputs/data.csv", "outputs/report.pdf"]) {
      expect(shouldAutoOpen(path, false), path).toBe(false);
    }
  });

  it("never opens on mobile, where there is no side panel", () => {
    expect(shouldAutoOpen("outputs/status.md", true)).toBe(false);
  });
});

describe("autoOpenArtifact", () => {
  it("opens a Markdown file live, then clears the badge", () => {
    const d = deps();
    const opened = autoOpenArtifact("outputs/status.md", { sessionId: "s1", isMobile: false }, d);
    expect(opened).toBe(true);
    expect(d.openDoc).toHaveBeenCalledWith({
      path: "outputs/status.md",
      name: "status.md",
      sessionId: "s1",
      live: true,
    });
    expect(d.scheduled).toHaveLength(1);
    expect(d.scheduled[0].ms).toBe(LIVE_BADGE_MS);
    d.scheduled[0].fn();
    expect(d.setDocLive).toHaveBeenCalledWith("s1", "outputs/status.md", false);
  });

  it("leaves a PNG as a card", () => {
    const d = deps();
    expect(autoOpenArtifact("outputs/chart.png", { sessionId: "s1", isMobile: false }, d)).toBe(false);
    expect(d.openDoc).not.toHaveBeenCalled();
    expect(d.schedule).not.toHaveBeenCalled();
  });

  it("opens nothing on mobile", () => {
    const d = deps();
    expect(autoOpenArtifact("outputs/status.md", { sessionId: "s1", isMobile: true }, d)).toBe(false);
    expect(d.openDoc).not.toHaveBeenCalled();
  });

  it("opens nothing without a session", () => {
    const d = deps();
    expect(autoOpenArtifact("outputs/status.md", { sessionId: "", isMobile: false }, d)).toBe(false);
    expect(autoOpenArtifact("outputs/status.md", { sessionId: null, isMobile: false }, d)).toBe(false);
    expect(d.openDoc).not.toHaveBeenCalled();
  });
});

describe("syncPanelToSession", () => {
  it("prunes the panel to the active session", () => {
    const prune = vi.fn();
    syncPanelToSession("s2", prune);
    expect(prune).toHaveBeenCalledWith("s2");
  });

  it("does nothing before a session exists", () => {
    const prune = vi.fn();
    syncPanelToSession("", prune);
    syncPanelToSession(undefined, prune);
    expect(prune).not.toHaveBeenCalled();
  });
});

// ── Both chats use the rule, and neither keeps a copy ──────────────────────

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

describe("the two chats", () => {
  const rail = read("../app/projects/components/AssistantRail.tsx");
  const chat = read("../app/chat/page.tsx");

  it("the Projects rail prunes the panel when its session changes", () => {
    expect(rail).toMatch(
      /useEffect\(\(\) => \{\s*syncPanelToSession\(activeId\);\s*\}, \[activeId\]\);/,
    );
  });

  it("the Projects rail hands written files to the shared rule", () => {
    expect(rail).toContain("autoOpenArtifact(entry.path, { sessionId: activeId, isMobile })");
    expect(rail).toContain("onArtifact={handleArtifact}");
  });

  it("/chat uses the same rule and keeps no extension list of its own", () => {
    expect(chat).toContain("autoOpenArtifact(entry.path");
    expect(chat).toContain("syncPanelToSession(activeSessionId)");
    expect(chat).not.toMatch(/\["md", "mdx", "html", "htm"\]/);
    expect(chat).not.toContain("setDocLive(");
  });
});
