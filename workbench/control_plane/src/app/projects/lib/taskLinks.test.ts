/**
 * The Links section's drawing rules (WS-57 TR-2): the icon, the state chip,
 * the two names and the paste-to-add rule. `lib/taskLinks.ts` holds them.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { pastedLink, refAddedBy, refIcon, refNames, refState } from "./taskLinks";

const KINDS = ["pull_request", "issue", "commit", "branch", "comment", "doc", "url"] as const;
const PROVIDERS = ["github", "gitlab", "bitbucket", "google", "figma", "notion", "other"] as const;
const STATES = [null, "open", "draft", "merged", "closed"] as const;

describe("refIcon", () => {
  it("names a real icon for every kind, provider and state", () => {
    for (const kind of KINDS)
      for (const provider of PROVIDERS)
        for (const state of STATES)
          expect(isKnownIcon(refIcon({ kind, provider, state })), `${kind} ${provider} ${state}`).toBe(true);
  });

  it("draws a pull request the way GitHub does", () => {
    const pr = (state: string | null) => refIcon({ kind: "pull_request", provider: "github", state });
    expect(pr("open")).toBe("GitPullRequest");
    expect(pr("draft")).toBe("GitPullRequestDraft");
    expect(pr("merged")).toBe("GitMerge");
    expect(pr("closed")).toBe("GitPullRequestClosed");
    expect(pr(null)).toBe("GitPullRequest");
  });

  it("tells a doc by its provider", () => {
    expect(refIcon({ kind: "doc", provider: "google", state: null })).toBe("FileText");
    expect(refIcon({ kind: "doc", provider: "figma", state: null })).toBe("PenTool");
    expect(refIcon({ kind: "doc", provider: "notion", state: null })).toBe("BookOpen");
    expect(refIcon({ kind: "url", provider: "other", state: null })).toBe("Link");
  });
});

describe("refState", () => {
  it("says the state in words, merged as done and closed as muted", () => {
    expect(refState("merged")).toEqual({ label: "Merged", tone: "success" });
    expect(refState("closed")).toEqual({ label: "Closed", tone: "neutral" });
    expect(refState("open")).toEqual({ label: "Open", tone: "primary" });
    expect(refState("draft")).toEqual({ label: "Draft", tone: "neutral" });
  });

  it("draws no chip with no state, or one it does not know", () => {
    expect(refState(null)).toBeNull();
    expect(refState("weird")).toBeNull();
  });
});

describe("refNames", () => {
  it("leads with the title and keeps the label beside it", () => {
    expect(refNames({ title: "Fix the login", label: "widgets#12" })).toEqual({
      main: "Fix the login",
      aside: "widgets#12",
    });
  });

  it("falls back to the label, and never repeats it", () => {
    expect(refNames({ title: null, label: "widgets#12" })).toEqual({ main: "widgets#12", aside: null });
    expect(refNames({ title: "  ", label: "Google Doc" })).toEqual({ main: "Google Doc", aside: null });
    expect(refNames({ title: "Google Doc", label: "Google Doc" })).toEqual({ main: "Google Doc", aside: null });
  });
});

describe("pastedLink", () => {
  it("adds a single http(s) URL at once", () => {
    expect(pastedLink(" https://github.com/acme/widgets/pull/12 ")).toBe(
      "https://github.com/acme/widgets/pull/12",
    );
    expect(pastedLink("http://example.com")).toBe("http://example.com");
  });

  it("leaves anything else in the field", () => {
    expect(pastedLink("see https://example.com")).toBeNull();
    expect(pastedLink("javascript:alert(1)")).toBeNull();
    expect(pastedLink("example.com")).toBeNull();
    expect(pastedLink("")).toBeNull();
  });
});

/**
 * WS-55 EA-13b (§12.2 item 5, EA3-44). Who added a link, when no person did.
 * Mutations caught: the rule drops `github`, titles the stamp, or labels a
 * person's link, a chat link or a link from the task's text.
 */
describe("refAddedBy", () => {
  it("names the agent's client and the GitHub App", () => {
    expect(refAddedBy({ via: "mcp:claude.ai:claude-code" })).toBe("added by Claude Code");
    expect(refAddedBy({ via: "mcp:claude.ai" })).toBe("added by Claude");
    expect(refAddedBy({ via: "github" })).toBe("linked by GitHub");
  });

  it("says nothing for a person, the chat or a link from the text", () => {
    for (const via of ["ui", "chat", "text", ""])
      expect(refAddedBy({ via }), via).toBeNull();
  });
});

/**
 * The Links section draws the rule. A DOM-free render cannot run the fetch
 * of `TaskLinks`, so this reads its source. Mutation caught: the section
 * drops the `added by` line, or computes it without `refAddedBy`.
 */
describe("TaskLinks draws refAddedBy (EA-13b)", () => {
  const source = readFileSync(resolve(__dirname, "..", "components/TaskLinks.tsx"), "utf-8");

  it("computes the line from the one rule and draws it", () => {
    expect(source).toMatch(/const addedBy = refAddedBy\(row\);/);
    expect(source).toMatch(/data-ref-added-by>\s*\{addedBy\}\s*</);
  });
});
