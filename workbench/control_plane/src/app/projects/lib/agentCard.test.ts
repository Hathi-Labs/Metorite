/**
 * WS-55 EA-13d: the card (`external_agent_access.md` §12.2 item 2, EA3-41,
 * EA3-48, EA3-50).
 *
 * The PR chip text for each state and each `checks_state`, its tone, its
 * hover, its link, the "Needs you" rule for the run's member and for another
 * member, the hover line, and the run pill `<client> \u00b7 <host>`.
 *
 * R7, the mutations. Each one turned the named test red (run 2026-10-18, each
 * one after a commit, because `git checkout` restores HEAD):
 *
 * - M1 `pullRequestChip` drops the checks part for `failing` -> "the chip text".
 * - M2 `prChipTone` reads `failing` before `merged` -> "the tone of the chip".
 * - M3 `pullRequestChip` shows checks on a merged pull request -> "the chip text".
 * - M4 `pullRequestChip` drops the `+N` -> "the chip text for more".
 * - M5 `pullRequestChip` draws any URL as a link -> "only an https address".
 * - M6 `TaskMeta`'s `ChipLink` drops `stopPropagation` -> "the link keeps the click".
 * - M7 `isViewer` ignores case -> "Needs you reads to the run's member only".
 * - M8 `agentPillText` ignores `note` -> "the hover line".
 * - M9 `runIdentity` reads the host from the client's name -> "the run pill".
 * - M16 the `pr` chip class written outside `TaskMeta` -> "no chip colour class".
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TaskMeta } from "@/components/TaskMeta";
import { prChipTone } from "@/lib/statusAccent";
import { type PullRequestFact, pullRequestChip } from "@/lib/taskCard";

import { AgentPill } from "../components/AgentPill";
import { agentPillText, runIdentity } from "./agentRuns";
import type { TaskRow } from "./api";
import { cardChips, visibleChips } from "./card";
import { DEFAULT_SHOWN } from "./shownFields";

const NOW = Date.parse("2026-10-18T12:00:00Z");

const pr = (over: Partial<PullRequestFact> = {}): PullRequestFact => ({
  repo: "acme/widgets",
  number: 12,
  state: "open",
  checks_state: null,
  url: "https://github.com/acme/widgets/pull/12",
  more: 0,
  ...over,
});

const row = (over: Partial<TaskRow> = {}): TaskRow =>
  ({
    id: "t1",
    project_id: "p1",
    root_project_id: "p1",
    status_id: "s1",
    title: "Ship it",
    ...over,
  }) as TaskRow;

describe("the chip text", () => {
  it.each([
    [{ state: "open", checks_state: "passing" }, "PR #12 · Open · ✓ checks"],
    [{ state: "open", checks_state: "failing" }, "PR #12 · Open · ✗ checks"],
    [{ state: "draft", checks_state: "pending" }, "PR #12 · Draft · … checks"],
    [{ state: "open", checks_state: "none" }, "PR #12 · Open"],
    [{ state: "open", checks_state: null }, "PR #12 · Open"],
    [{ state: "merged", checks_state: "passing" }, "PR #12 · Merged"],
    [{ state: "closed", checks_state: "failing" }, "PR #12 · Closed"],
  ] as const)("%j reads %s", (over, text) => {
    expect(pullRequestChip(pr(over))?.label).toBe(text);
  });

  it("the chip text for more: `+2` follows the words", () => {
    expect(pullRequestChip(pr({ state: "merged", more: 2 }))?.label).toBe(
      "PR #12 · Merged +2",
    );
    expect(pullRequestChip(pr({ checks_state: "passing", more: 1 }))?.label).toBe(
      "PR #12 · Open · ✓ checks +1",
    );
  });

  it("names no chip for no pull request, an unknown state or no number", () => {
    expect(pullRequestChip(null)).toBeNull();
    expect(pullRequestChip(undefined)).toBeNull();
    expect(pullRequestChip(pr({ state: "frozen" }))).toBeNull();
    expect(pullRequestChip(pr({ number: null }))).toBeNull();
  });

  it("the hover holds the title, the counts and the others", () => {
    const chip = pullRequestChip(
      pr({
        title: "Fix the extruder",
        checks_state: "failing",
        checks_summary: { passed: 12, failed: 1, pending: 0, skipped: 3, total: 16 },
        more: 2,
      }),
    );
    expect(chip?.title).toContain("Fix the extruder (acme/widgets#12)");
    expect(chip?.title).toContain("Checks: 12 passed, 1 failed.");
    expect(chip?.title).toContain("2 other pull requests.");
    expect(chip?.title).not.toContain("0 pending");
  });

  it("a merged pull request has no checks line", () => {
    const chip = pullRequestChip(
      pr({ state: "merged", checks_summary: { passed: 3, failed: 0, pending: 0 } }),
    );
    expect(chip?.title).not.toContain("Checks:");
  });

  it("only an https address draws as a link", () => {
    expect(pullRequestChip(pr())?.href).toBe("https://github.com/acme/widgets/pull/12");
    expect(pullRequestChip(pr({ url: "javascript:alert(1)" }))?.href).toBeUndefined();
    expect(pullRequestChip(pr({ url: "http://github.com/a/b/pull/1" }))?.href).toBeUndefined();
    expect(pullRequestChip(pr({ url: null }))?.href).toBeUndefined();
  });

  it("carries a glyph for each state, so the chip never depends on colour", () => {
    const icons = ["open", "draft", "merged", "closed"].map(
      (state) => pullRequestChip(pr({ state }))?.icon,
    );
    expect(icons).toEqual([
      "GitPullRequest",
      "GitPullRequestDraft",
      "GitMerge",
      "GitPullRequestClosed",
    ]);
  });
});

describe("the tone of the chip", () => {
  it("merged is the done tone, failing is danger, pending is warning, the rest are muted", () => {
    expect(prChipTone("merged", "failing")).toBe("success");
    expect(prChipTone("open", "failing")).toBe("danger");
    expect(prChipTone("draft", "pending")).toBe("warning");
    expect(prChipTone("open", "passing")).toBe("muted");
    expect(prChipTone("open", "none")).toBe("muted");
    expect(prChipTone("open", null)).toBe("muted");
    expect(prChipTone("closed", "failing")).toBe("muted");
    expect(prChipTone("closed", "pending")).toBe("muted");
  });

  it("the descriptor takes its tone from prChipTone, and keeps the tone a name", () => {
    expect(pullRequestChip(pr({ checks_state: "failing" }))?.tone).toBe("danger");
    expect(pullRequestChip(pr({ state: "merged" }))?.tone).toBe("success");
  });
});

describe("the chip on the card", () => {
  const withPr = row({ pr: pr({ checks_state: "passing" }) });

  it("rides the chip row after the progress, and the gate is the `pr` field", () => {
    const keys = cardChips(
      row({ subtasks: { done: 1, total: 2 }, tags: ["ops"], pr: pr() }),
      NOW,
    ).map((c) => c.key);
    expect(keys).toEqual(["subtasks", "pr", "tags:ops"]);
    expect(DEFAULT_SHOWN).toContain("pr");
    expect(visibleChips(withPr, DEFAULT_SHOWN, NOW).map((c) => c.key)).toContain("pr");
    expect(
      visibleChips(
        withPr,
        DEFAULT_SHOWN.filter((k) => k !== "pr"),
        NOW,
      ).map((c) => c.key),
    ).not.toContain("pr");
  });

  it("a task with no pull request earns no chip", () => {
    expect(cardChips(row(), NOW)).toEqual([]);
  });

  it("the link keeps the click: it opens a new tab and stops the card from opening", () => {
    const html = renderToStaticMarkup(
      createElement(TaskMeta, { chips: [pullRequestChip(pr({ checks_state: "passing" }))!] }),
    );
    expect(html).toContain('href="https://github.com/acme/widgets/pull/12"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain("PR #12");
    const source = readFileSync(
      fileURLToPath(new URL("../../../components/TaskMeta.tsx", import.meta.url)),
      "utf8",
    );
    const link = source.slice(source.indexOf("function ChipLink"));
    for (const stopped of ["onClick", "onPointerDown", "onKeyDown", "onDragStart"]) {
      expect(link.slice(0, link.indexOf("</a>"))).toMatch(
        new RegExp(`${stopped}=\\{[^}]*stopPropagation`),
      );
    }
  });

  it("no chip colour class outside TaskMeta", () => {
    const read = (path: string) =>
      readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");
    const cls = /(text|bg|border)-(success|destructive|warning)\b/;
    for (const path of [
      "../../../lib/taskCard.ts",
      "../../../lib/statusAccent.ts",
      "./card.ts",
      "../components/TableView.tsx",
      "../components/TaskBoard.tsx",
      "../components/TaskList.tsx",
    ]) {
      const source = read(path);
      const prPart =
        path.endsWith("statusAccent.ts")
          ? source.slice(source.indexOf("Pull request chip (WS-55 EA-13d"))
          : source;
      // TableView and the lists may hold their own old classes. Only the chip
      // code is held to this rule, so a line that names the chip is checked.
      const chipLines = prPart
        .split("\n")
        .filter((line) => /pullRequestChip|prChip|data-pr-chip/.test(line));
      for (const line of chipLines) expect(line, path).not.toMatch(cls);
    }
  });
});

describe("the pill: Needs you, and the hover line", () => {
  const facts = { clientVia: "mcp:claude.ai:claude-code", memberEmail: "ravi@x.test" };

  it("Needs you reads to the run's member only", () => {
    expect(agentPillText("awaiting", { ...facts, viewerEmail: "ravi@x.test" }).text).toBe(
      "Needs you",
    );
    expect(agentPillText("awaiting", { ...facts, viewerEmail: "RAVI@x.test" }).text).toBe(
      "Needs you",
    );
    expect(agentPillText("awaiting", { ...facts, viewerEmail: "lena@x.test" }).text).toBe(
      "Waiting for Ravi",
    );
    expect(agentPillText("awaiting", facts).text).toBe("Waiting for Ravi");
    expect(agentPillText("awaiting", { ...facts, viewerEmail: "" }).text).toBe("Waiting for Ravi");
  });

  it("a stale run reads as the client idle, and the other states do not change", () => {
    expect(agentPillText("stale", facts).text).toBe("Claude Code idle");
    expect(agentPillText("working", facts).text).toBe("Claude Code working");
    expect(agentPillText("queued", facts).text).toBe("Queued for Claude Code");
  });

  it("the hover line is the agent's newest line, in every state", () => {
    for (const state of ["awaiting", "working", "queued", "stale", "answered"] as const) {
      expect(agentPillText(state, { ...facts, note: "  Reading the tests  " }).title).toBe(
        "Reading the tests",
      );
    }
    expect(agentPillText("working", { ...facts, note: "   " }).title).toBe(
      "Claude Code works on this task",
    );
  });

  it("the pill draws the note as its title and the words as its text", () => {
    const html = renderToStaticMarkup(
      createElement(AgentPill, {
        state: "awaiting",
        ...facts,
        viewerEmail: "ravi@x.test",
        note: "Which database?",
      }),
    );
    expect(html).toContain('title="Which database?"');
    expect(html).toContain("Needs you");
  });
});

describe("the run pill", () => {
  it("reads `<client_name> · <host>`", () => {
    expect(
      runIdentity({
        client_via: "mcp:claude.ai:claude-code",
        client_name: "Claude Code",
        client_host: "claude.ai",
      }),
    ).toBe("Claude Code · claude.ai");
  });

  it("falls back to the stamp when the gateway sends no name", () => {
    expect(runIdentity({ client_via: "mcp:claude.ai:claude-code" })).toBe(
      "Claude Code · claude.ai",
    );
    expect(runIdentity({ client_via: "mcp:claude.ai" })).toBe("Claude · claude.ai");
  });

  it("a host that is its own name reads once, and a client at another host reads as that host", () => {
    expect(runIdentity({ client_via: "mcp:evil.example:claude-code" })).toBe("evil.example");
    expect(
      runIdentity({
        client_via: "mcp:evil.example:claude-code",
        client_name: "evil.example",
        client_host: "evil.example",
      }),
    ).toBe("evil.example");
  });
});

