// WS-54 fix round 1, item 8: the chat's SETUP tokens in the BFF.
//
// Mutations, red here (run 2026-10-18):
//  S-M1 `splitSetupVars` keeps GITHUB_TOKEN with the other keys -> "the token is split out".
//  S-M2 `githubSetupNote` answers "" for a refusal -> "a refusal adds a line".
import { describe, expect, it } from "vitest";

import { APP_CONNECTED_NOTE, githubSetupNote, splitSetupVars } from "./setupTokens";

const TOKEN = "ghp_BFF_SENTINEL_0123456789abcdef";

describe("the chat's SETUP tokens", () => {
  it("the token is split out, and the other keys stay", () => {
    expect(
      splitSetupVars([
        { service: "github", key: "GITHUB_TOKEN", value: ` ${TOKEN} ` },
        { service: "serpapi", key: "SERPAPI_API_KEY", value: "abc" },
        { service: "x", key: "EMPTY_KEY", value: "  " },
      ]),
    ).toEqual({ githubToken: TOKEN, vars: [{ key: "SERPAPI_API_KEY", value: "abc" }] });
  });

  it("a refusal adds a line, and the line never holds the token", () => {
    expect(githubSetupNote(200, undefined, TOKEN)).toBe("");
    const note = githubSetupNote(422, "GitHub refused this token.", TOKEN);
    expect(note).toBe(
      "Metorite did not save the GitHub token: GitHub refused this token. Paste it in Integrations, under Advanced.",
    );
    expect(githubSetupNote(422, `bad ${TOKEN}`, TOKEN)).not.toContain(TOKEN);
    expect(githubSetupNote(409, "app_connected", TOKEN)).toBe(APP_CONNECTED_NOTE);
    expect(APP_CONNECTED_NOTE).not.toContain(";");
  });
});
