// The decisions of the GitHub card and the App connect flow (WS-54 GH-3).
//
// Mutations, each red here (run 2026-10-18):
//  U1 `parseGitHubReturn` takes any `account` -> "a confirm needs a GitHub login".
//  U2 `isGitHubInstallUrl` drops the host check -> "only an install link on github.com".
//  U3 `githubCardView` sets `warnsOfToken: false` for a token row -> "a token row warns".
//  U4 a code is missing from `GITHUB_RETURN_CODES` -> "the codes mirror the gateway".
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  GITHUB_RETURN_CODES,
  GITHUB_RETURN_TEXT,
  TOKEN_FOLD_LABEL,
  confirmCopy,
  githubCardView,
  githubReturnPath,
  isGitHubInstallUrl,
  parseGitHubReturn,
  refusalText,
  replaceTokenCopy,
  safeInstallationUrl,
  tokenProblem,
  type GitHubAccount,
} from "./githubConnect";

const q = (s: string) => new URLSearchParams(s);

function account(over: Partial<GitHubAccount>): GitHubAccount {
  return {
    connected: true, status: "connected", login: "acme", auth: "app",
    account_type: "Organization", repository_selection: "selected",
    installation_url: "https://github.com/organizations/acme/settings/installations/5",
    connected_by: "dana@example.com", organization_name: "Fracktal", app_available: true,
    ...over,
  };
}

describe("the codes", () => {
  it("the codes mirror the gateway", () => {
    const source = readFileSync(
      new URL("../../../../apps/services/gateway/gateway/routes/github/connect.py", import.meta.url),
      "utf-8",
    );
    const block = source.match(/CODES: frozenset\[str\] = frozenset\(\{([\s\S]*?)\}\)/);
    expect(block).not.toBeNull();
    const gateway = [...(block?.[1] ?? "").matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort();
    expect([...GITHUB_RETURN_CODES].sort()).toEqual(gateway);
  });

  it("every code has one sentence, and none has a semicolon", () => {
    for (const code of GITHUB_RETURN_CODES) {
      expect(GITHUB_RETURN_TEXT[code].text.length).toBeGreaterThan(5);
      expect(GITHUB_RETURN_TEXT[code].text).not.toContain(";");
    }
    expect(GITHUB_RETURN_TEXT.requested.text).toBe(
      "Your GitHub organization owner must approve the install. Press Connect again after they approve.",
    );
    expect(GITHUB_RETURN_TEXT.other_org.text).toBe(
      "This GitHub account is connected to another Metorite organization.",
    );
  });
});

describe("the return", () => {
  it("reads a known code and refuses an unknown one", () => {
    expect(parseGitHubReturn(q("view=connections&github=expired"))).toEqual({ code: "expired" });
    expect(parseGitHubReturn(q("github=evil"))).toBeNull();
    expect(parseGitHubReturn(q("view=connections"))).toBeNull();
  });

  it("a confirm needs a GitHub login", () => {
    expect(parseGitHubReturn(q("github=confirm&account=acme-co"))).toEqual({
      code: "confirm", account: "acme-co",
    });
    for (const bad of ["", "-acme", "a b", "<script>", "x".repeat(40), "acme/../x"]) {
      expect(parseGitHubReturn(q(`github=confirm&account=${encodeURIComponent(bad)}`))).toEqual({
        code: "failed",
      });
    }
  });

  it("builds only relative paths to the Connections view", () => {
    expect(githubReturnPath({ code: "failed" })).toBe("/integrations?view=connections&github=failed");
    expect(githubReturnPath({ code: "confirm", account: "acme" })).toBe(
      "/integrations?view=connections&github=confirm&account=acme",
    );
    expect(githubReturnPath({ code: "confirm", account: "bad name" })).toBe(
      "/integrations?view=connections&github=confirm",
    );
  });
});

describe("the URLs", () => {
  it("only an install link on github.com", () => {
    expect(isGitHubInstallUrl("https://github.com/apps/metorite/installations/new?state=a.b")).toBe(true);
    for (const bad of [
      "https://evil.example/apps/metorite/installations/new?state=a",
      "http://github.com/apps/metorite/installations/new?state=a",
      "https://github.com.evil.example/apps/m/installations/new?state=a",
      "https://user:pw@github.com/apps/m/installations/new?state=a",
      "https://github.com/login/oauth/authorize?state=a",
      "https://github.com/apps/m/installations/new?state=a&redirect_uri=x",
      "https://github.com/apps/m/installations/new",
      42,
      null,
    ]) {
      expect(isGitHubInstallUrl(bad)).toBe(false);
    }
  });

  it("only an installation page at GitHub", () => {
    expect(safeInstallationUrl("https://github.com/organizations/acme/settings/installations/12")).toBe(
      "https://github.com/organizations/acme/settings/installations/12",
    );
    expect(safeInstallationUrl("https://github.com/settings/installations/12")).toBe(
      "https://github.com/settings/installations/12",
    );
    expect(safeInstallationUrl("https://evil.example/settings/installations/12")).toBeNull();
    expect(safeInstallationUrl("https://github.com/settings/installations/12?x=1")).toBeNull();
    expect(safeInstallationUrl("javascript:alert(1)")).toBeNull();
  });
});

describe("the card", () => {
  it("an org with no connection gets Connect GitHub, for a manager only", () => {
    const none = account({ connected: false, status: "not_connected", auth: null, login: null });
    expect(githubCardView(none, true).connectLabel).toBe("Connect GitHub");
    expect(githubCardView(none, false).connectLabel).toBeNull();
    expect(githubCardView(null, true).mode).toBe("none");
  });

  it("no button when the App is not turned on", () => {
    const none = account({ connected: false, status: "not_connected", auth: null, app_available: false });
    expect(githubCardView(none, true).connectLabel).toBeNull();
  });

  it("an App row names the account and the reach", () => {
    const view = githubCardView(account({}), true);
    expect(view.mode).toBe("app");
    expect(view.headline).toContain("acme");
    expect(view.headline).toContain("the repositories you picked");
    expect(view.canDisconnect).toBe(true);
    expect(view.warnsOfToken).toBe(false);
  });

  it("a suspended App row needs attention", () => {
    const view = githubCardView(account({ connected: false, status: "needs_reconnect" }), true);
    expect(view.mode).toBe("attention");
    expect(view.connectLabel).toBe("Reconnect GitHub");
  });

  it("a token row warns before the App replaces it", () => {
    const view = githubCardView(account({ auth: "token", login: "octo", installation_url: null }), true);
    expect(view.mode).toBe("token");
    expect(view.warnsOfToken).toBe(true);
    expect(replaceTokenCopy().note).toBe("This replaces the token that is connected now.");
  });
});

describe("the Advanced token (GH-7)", () => {
  it("names the fold as the spec does", () => {
    expect(TOKEN_FOLD_LABEL).toBe("Advanced: use a personal access token");
  });

  it("refuses an empty, spaced or oversized token before any request", () => {
    expect(tokenProblem("")).toBe("Paste a token first.");
    expect(tokenProblem("   ")).toBe("Paste a token first.");
    expect(tokenProblem("ghp_a b")).toBe("That is not a GitHub token.");
    expect(tokenProblem("x".repeat(256))).toBe("That is not a GitHub token.");
    expect(tokenProblem(" ghp_AbCdEf0123456789 ")).toBeNull();
  });

  it("the device flow is gone from the client", () => {
    const here = new URL("../components/", import.meta.url);
    expect(() => readFileSync(new URL("GitHubDeviceConnect.tsx", here), "utf-8")).toThrow();
    const badge = readFileSync(new URL("GitHubAccountBadge.tsx", here), "utf-8");
    expect(badge).not.toMatch(/fetch\(\s*["'`]\/api\/integrations\/github\/connect-cli/);
    expect(badge).not.toMatch(/device\/(start|poll)/);
  });
});

describe("the words", () => {
  it("the confirm names the account and the org", () => {
    expect(confirmCopy("acme", "Fracktal").title).toBe("Connect acme to Fracktal?");
    expect(confirmCopy("acme", null).title).toBe("Connect acme to this Metorite organization?");
    expect(confirmCopy("acme", "Fracktal").body).toContain("It writes nothing to GitHub.");
  });

  it("a refusal reads a code or a fixed line, never the raw detail", () => {
    expect(refusalText("other_org")).toBe(GITHUB_RETURN_TEXT.other_org.text);
    expect(refusalText("<b>boom</b>")).toBe(GITHUB_RETURN_TEXT.failed.text);
    expect(refusalText("cross_origin")).toContain("Open Integrations");
  });
});
