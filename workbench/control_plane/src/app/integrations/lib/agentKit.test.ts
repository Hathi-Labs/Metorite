// KIT-4b. The "Download the agent kit" control: the pure rules, and the
// component rendered to a string (the runner has no DOM, as in mcpConnect.test.ts).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import AgentKitDownload, { AgentKitPanel } from "../components/AgentKitDownload";
import {
  CLIENT_TABS,
  installText,
  kitFileName,
  kitHref,
  parseIndex,
  readKitIndex,
} from "./agentKit";

const SIX = ["claude", "codex", "copilot", "gemini", "antigravity", "generic"];
const noop = () => undefined;

afterEach(() => vi.unstubAllGlobals());

describe("parseIndex", () => {
  it("accepts a good body", () => {
    expect(parseIndex({ kit_version: 1, clients: SIX })).toEqual({ kit_version: 1, clients: SIX });
  });
  it("rejects bad shapes", () => {
    for (const bad of [null, "x", 3, [], {}, { kit_version: "1", clients: SIX }, { kit_version: 1.5, clients: SIX },
      { kit_version: 1 }, { kit_version: 1, clients: "claude" }, { kit_version: 1, clients: [1] }]) {
      expect(parseIndex(bad)).toBeNull();
    }
  });
  it("drops unknown clients", () => {
    expect(parseIndex({ kit_version: 2, clients: ["claude", "vim"] })?.clients).toEqual(["claude"]);
  });
});

describe("the tabs", () => {
  it("are the six of the server, in order", () => {
    expect(CLIENT_TABS.map((t) => t.id)).toEqual(SIX);
    expect(CLIENT_TABS.map((t) => t.label)).toEqual([
      "Claude Code", "OpenAI Codex", "GitHub Copilot", "Gemini CLI", "Google Antigravity", "Other",
    ]);
  });
  it("hold short steps with no secret and no company name", () => {
    for (const t of CLIENT_TABS) {
      expect(t.steps.length).toBeGreaterThanOrEqual(3);
      expect(t.steps.length).toBeLessThanOrEqual(4);
      const text = installText(t, 1);
      expect(text).not.toMatch(/HathiLabs|Fracktal/i);
      expect(text).not.toMatch(/sk-[A-Za-z0-9]|Bearer |mtk_|eyJ[A-Za-z0-9]/);
    }
  });
});

describe("names", () => {
  it("builds the file name and the address", () => {
    expect(kitFileName("claude", 3)).toBe("metorite-agent-kit-claude-v3.zip");
    expect(kitHref("codex")).toBe("/api/agent-kit/codex.zip");
  });
});

describe("readKitIndex", () => {
  it("is null on 404 and on a failure", async () => {
    vi.stubGlobal("fetch", async () => new Response("{}", { status: 404 }));
    expect(await readKitIndex()).toBeNull();
    vi.stubGlobal("fetch", async () => {
      throw new Error("down");
    });
    expect(await readKitIndex()).toBeNull();
  });
  it("is the index on 200", async () => {
    vi.stubGlobal("fetch", async () => Response.json({ kit_version: 1, clients: SIX }));
    expect(await readKitIndex()).toEqual({ kit_version: 1, clients: SIX });
  });
});

describe("the control", () => {
  it("draws nothing before the index arrives, and so nothing on 404", () => {
    expect(renderToStaticMarkup(createElement(AgentKitDownload, { onCopy: noop }))).toBe("");
  });
  it("draws the six tabs on 200", () => {
    const html = renderToStaticMarkup(
      createElement(AgentKitPanel, { index: { kit_version: 1, clients: SIX }, clientId: "claude", onClient: noop, onCopy: noop }),
    );
    expect(html).toContain("Download the agent kit");
    for (const t of CLIENT_TABS) expect(html).toContain(t.label);
    expect(html).toContain("Copy the install steps");
  });
  it("points the download at the proxied zip of the selected tab", () => {
    const html = renderToStaticMarkup(
      createElement(AgentKitPanel, { index: { kit_version: 4, clients: SIX }, clientId: "gemini", onClient: noop, onCopy: noop }),
    );
    expect(html).toContain('href="/api/agent-kit/gemini.zip"');
    expect(html).toContain('download="metorite-agent-kit-gemini-v4.zip"');
    expect(html).toContain("Download the kit for Gemini CLI");
  });
});
