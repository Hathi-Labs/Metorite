/**
 * The Links section's drawing rules (WS-57 TR-2): the icon, the state chip,
 * the two names and the paste-to-add rule. `lib/taskLinks.ts` holds them.
 */
import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { pastedLink, refIcon, refNames, refState } from "./taskLinks";

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
