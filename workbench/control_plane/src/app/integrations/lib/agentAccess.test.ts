// The delete switch of the AI agents view (WS-55 EA-6h, EA3-51).
//
// The card draws only a state that the gateway gave. A 404 is "the agent
// flag is off", and the card is not drawn. Any other failure is an error,
// never "off".
//
// Mutations, each one red here:
// - `readAccess` answers `{delete_enabled: false}` for a body with no boolean;
// - `readAgentAccess` answers `null` for every failure, not only a 404;
// - `saveAgentAccess` sends `{delete_enabled: true}` whatever it is given.
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AGENT_ACCESS_KEY,
  AGENTS_KEY_PREFIX,
  DELETE_SWITCH_COPY,
  deleteSwitchHint,
  readAgentAccess,
  saveAgentAccess,
} from "./agents";

const sent: { url: string; init?: RequestInit }[] = [];

function stub(status: number, body: unknown) {
  sent.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      sent.push({ url: String(input), init });
      return new Response(JSON.stringify(body), { status });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the delete switch", () => {
  it("reads the member's switch", async () => {
    stub(200, { delete_enabled: true });
    expect(await readAgentAccess()).toEqual({ delete_enabled: true });
    expect(sent[0].url).toBe("/api/auth/me/agent-access");
  });

  it("is null when agent connections are off", async () => {
    stub(404, { error: "x" });
    expect(await readAgentAccess()).toBeNull();
  });

  it("is an error, never off, when the read fails or holds no boolean", async () => {
    stub(502, { error: "agent_access_unavailable" });
    await expect(readAgentAccess()).rejects.toThrow("did not load");
    stub(200, { delete_enabled: "yes" });
    await expect(readAgentAccess()).rejects.toThrow("did not load");
    stub(200, {});
    await expect(readAgentAccess()).rejects.toThrow("did not load");
  });

  it("saves the state it is given, and returns what the gateway stored", async () => {
    stub(200, { delete_enabled: false });
    expect(await saveAgentAccess(false)).toEqual({ delete_enabled: false });
    expect(sent[0].init?.method).toBe("PUT");
    expect(JSON.parse(String(sent[0].init?.body))).toEqual({ delete_enabled: false });
    stub(200, { delete_enabled: true });
    await saveAgentAccess(true);
    expect(JSON.parse(String(sent[0].init?.body))).toEqual({ delete_enabled: true });
    stub(503, { error: "x" });
    await expect(saveAgentAccess(true)).rejects.toThrow("was not saved");
  });

  it("says what each state does, and shares the cache prefix", () => {
    expect(deleteSwitchHint(true)).toBe(DELETE_SWITCH_COPY.on);
    expect(deleteSwitchHint(false)).toBe(DELETE_SWITCH_COPY.off);
    expect(DELETE_SWITCH_COPY.body).toContain("cannot be undone");
    expect(DELETE_SWITCH_COPY.body).toContain("asks you first");
    expect(AGENT_ACCESS_KEY.startsWith(AGENTS_KEY_PREFIX)).toBe(true);
  });
});
