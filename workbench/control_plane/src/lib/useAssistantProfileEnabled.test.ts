// The profile shows only for the build switch AND the gateway's yes for the
// member's organization (WS-63 PZ-25, `personalization.md` §14.2).
//
// Mutation: make `profileState` ignore `built`, and "the build switch off
// hides it" fails. Make it keep a yes over an error, and "a fault hides it"
// fails. Point a consumer back at `assistantProfileOn`, and the source scan
// fails.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PROFILE_URL,
  fetchProfileEnabled,
  profileState,
  useAssistantProfileEnabled,
  useAssistantProfileState,
} from "./useAssistantProfileEnabled";

const reply = (status: number, body: unknown) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe("fetchProfileEnabled", () => {
  it("is true only for enabled === true", async () => {
    expect(await fetchProfileEnabled(reply(200, { v: 1, enabled: true }))).toBe(true);
    expect(await fetchProfileEnabled(reply(200, { v: 1, enabled: false }))).toBe(false);
    expect(await fetchProfileEnabled(reply(200, { v: 1 }))).toBe(false);
    expect(await fetchProfileEnabled(reply(200, { enabled: "true" }))).toBe(false);
    expect(await fetchProfileEnabled(reply(200, null))).toBe(false);
  });

  it("throws on a fault, so the cache keeps no yes", async () => {
    await expect(fetchProfileEnabled(reply(503, { detail: "unavailable" }))).rejects.toThrow();
    await expect(fetchProfileEnabled(reply(502, { enabled: true }))).rejects.toThrow();
  });

  it("asks the BFF route of the profile", async () => {
    const f = reply(200, { enabled: true });
    await fetchProfileEnabled(f);
    expect((f as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe(PROFILE_URL);
  });
});

describe("profileState", () => {
  const read = (data: boolean | undefined, loading = false, error: string | null = null) => ({ data, loading, error });

  it("is on only with the build switch and a yes", () => {
    expect(profileState(true, read(true))).toBe("on");
  });
  it("the build switch off hides it, also over a yes", () => {
    expect(profileState(false, read(true))).toBe("off");
    expect(profileState(false, read(undefined, true))).toBe("off");
  });
  it("enabled false hides it", () => {
    expect(profileState(true, read(false))).toBe("off");
  });
  it("a fault is error, also over an earlier yes, and error is not on", () => {
    expect(profileState(true, read(undefined, false, "boom"))).toBe("error");
    expect(profileState(true, read(true, false, "boom"))).toBe("error");
  });
  it("the build switch off wins over a fault", () => {
    expect(profileState(false, read(undefined, false, "boom"))).toBe("off");
  });
  it("a read still out is pending, which is not on", () => {
    expect(profileState(true, read(undefined, true))).toBe("pending");
  });
});

describe("the hook, on the server render", () => {
  afterEach(() => vi.unstubAllEnvs());
  const Probe = () => createElement("i", null, `${useAssistantProfileState()}:${useAssistantProfileEnabled()}`);

  it("is off on the server, so no frame shows the profile before the answer", () => {
    vi.stubEnv("NEXT_PUBLIC_ASSISTANT_PROFILE", "1");
    // The server snapshot of the build switch is false (useSyncExternalStore).
    expect(renderToStaticMarkup(createElement(Probe))).toBe("<i>off:false</i>");
  });
});

describe("source scan: each place asks the hook, not the build switch alone", () => {
  const read = (rel: string) =>
    readFileSync(new URL(`../${rel}`, import.meta.url), "utf-8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");

  it.each([
    "app/settings/assistant/page.tsx",
    "app/settings/appearance/page.tsx",
    "lib/shell/MyDayPage.tsx",
    "components/AccountSwitcher.tsx",
    "components/Sidebar.tsx",
    "components/AppShell.tsx",
  ])("%s", (rel) => {
    const src = read(rel);
    expect(src).toMatch(/useAssistantProfile(Enabled|State|Read)\(\)/);
    expect(src).not.toMatch(/assistantProfileOn\(/);
  });

  it("My Day sends no address read unless the hook says yes", () => {
    const src = read("lib/shell/MyDayPage.tsx");
    expect(src).toMatch(/const profileOn = useAssistantProfileEnabled\(\);/);
    expect(src).toMatch(/useCachedResource<string>\(\s*profileOn \? ADDRESS_KEY : null,/);
  });

  it("the page draws its body from the state, with Retry wired to refresh", () => {
    const src = read("app/settings/assistant/page.tsx");
    expect(src).toMatch(/const \{ state, refresh \} = useAssistantProfileRead\(\);/);
    expect(src).toMatch(/<AssistantPageBody state=\{state\} onRetry=\{refresh\}>\s*<AssistantPane \/>/);
  });

  it("the account row and My Day hide on error: they ask the boolean", () => {
    for (const rel of ["lib/shell/MyDayPage.tsx", "components/AccountSwitcher.tsx", "components/Sidebar.tsx", "components/AppShell.tsx"]) {
      expect(read(rel)).toMatch(/useAssistantProfileEnabled\(\)/);
    }
  });

  it("each account row passes the hook's answer", () => {
    expect(read("components/Sidebar.tsx")).toMatch(/accountLinks\(sections, \{ assistant \}\)/);
    expect(read("components/AppShell.tsx")).toMatch(/accountLinks\(navSections, \{ assistant \}\)/);
    expect(read("components/AccountSwitcher.tsx")).toMatch(/assistant=\{assistant\}/);
  });
});
