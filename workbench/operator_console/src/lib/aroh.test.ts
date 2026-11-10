// CP-APP (task 1105): the Aroh tab of a customer page and the /aroh page.
// The rules are pure, so this file covers them with no renderer.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  arohState,
  connectProblem,
  connectWord,
  connectionState,
  readArohApp,
  readConnection,
  resultLine,
  seatCountProblem,
  seatLine,
  type ArohApp,
  type ArohConnection,
} from "./aroh";

const SRC = join(__dirname, "..");

const APP: ArohApp = {
  app: "aroh",
  name: "Aroh",
  enabled: true,
  configured: true,
  deployment: { label: "aroh", base_url: "https://aroh.dev.metorite.com" },
  seats: { purchased: 2, assigned: 1, available: 1, oversubscribed: false },
  holders: [],
  usage: { window_days: 30, calls: 0, credits: "0", cost_usd: "0", members: [] },
  updated_at: null,
  updated_by: null,
};

const CONN: ArohConnection = {
  app: "aroh",
  base_url: "https://aroh.dev.metorite.com",
  default_base_url: "https://aroh.dev.metorite.com",
  token_set: true,
  token_last4: "abcd",
  connected: false,
  connected_at: null,
  key_prefix: null,
  key_capabilities: null,
  capabilities: ["resolve", "seat_admin", "serve"],
  deployment: null,
  last_result: null,
  updated_at: null,
  updated_by: null,
};

describe("reading the Console", () => {
  it("finds the Aroh entry of GET /orgs/apps", () => {
    const got = readArohApp({ org_slug: "x", apps: [APP] });
    expect(got?.seats.purchased).toBe(2);
    expect(got?.usage.members).toEqual([]);
  });

  it("says null, not an empty app, for a Console that predates CP-APP", () => {
    expect(readArohApp({ detail: "Not Found" })).toBeNull();
    expect(readArohApp({ apps: [{ app: "other" }] })).toBeNull();
    expect(readArohApp(null)).toBeNull();
  });

  it("reads the connection and never invents a token", () => {
    const got = readConnection({ ...CONN, operator_token: "leak" });
    expect(got?.token_set).toBe(true);
    expect(JSON.stringify(got)).not.toContain("leak");
    expect(readConnection({ app: "aroh" })).toBeNull();
  });
});

describe("the seat line and the state chip", () => {
  it("leads with seats in use", () => {
    expect(seatLine(APP.seats)).toBe("1 of 2 seats in use");
    expect(seatLine({ purchased: 1, assigned: 0, available: 1, oversubscribed: false })).toBe(
      "0 of 1 seat in use",
    );
    expect(seatLine({ purchased: 0, assigned: 0, available: 0, oversubscribed: false })).toBe(
      "No Aroh seats",
    );
  });

  it("tells off, full and never-given apart", () => {
    expect(arohState({ ...APP, configured: false, enabled: false }).label).toBe("Not given");
    expect(arohState({ ...APP, enabled: false }).tone).toBe("warn");
    expect(
      arohState({ ...APP, seats: { purchased: 2, assigned: 2, available: 0, oversubscribed: false } })
        .label,
    ).toBe("On · all seats in use");
    expect(arohState(APP)).toEqual({ label: "On", tone: "ok" });
  });
});

describe("the seat count form refuses what the Console would refuse", () => {
  it("needs a whole number, no lower than the seats in use, and a reason", () => {
    expect(seatCountProblem("x", 0, "pilot")).toMatch(/whole number/);
    expect(seatCountProblem("1.5", 0, "pilot")).toMatch(/whole number/);
    expect(seatCountProblem("1", 2, "pilot")).toMatch(/2 seats are in use/);
    expect(seatCountProblem("3", 2, "")).toMatch(/Say why/);
    expect(seatCountProblem("3", 2, "pilot")).toBeNull();
  });
});

describe("Connect Aroh", () => {
  it("names the act: Connect first, Rotate after", () => {
    expect(connectWord(CONN)).toBe("Connect Aroh");
    expect(connectWord({ ...CONN, connected: true })).toBe("Rotate key");
  });

  it("🔴 needs the typed name, because each act mints or revokes a real key", () => {
    expect(connectProblem(CONN, "", "connect")).toMatch(/Type "aroh"/);
    expect(connectProblem(CONN, "Aroh", "connect")).toBeNull();
    expect(connectProblem({ ...CONN, token_set: false }, "aroh", "connect")).toMatch(/token/);
    expect(connectProblem(CONN, "aroh", "disconnect")).toMatch(/not connected/);
    expect(connectProblem({ ...CONN, connected: true }, "aroh", "disconnect")).toBeNull();
  });

  it("draws a hub that holds no key as broken", () => {
    expect(connectionState({ ...CONN, token_set: false }).label).toBe("Not set up");
    expect(connectionState(CONN).tone).toBe("warn");
    expect(connectionState({ ...CONN, connected: true }).tone).toBe("ok");
    expect(
      connectionState({ ...CONN, connected: true, hub: { hub_status: 200, configured: false } }).tone,
    ).toBe("danger");
    expect(connectionState({ ...CONN, connected: true, hub: { hub_status: null } }).tone).toBe(
      "danger",
    );
  });

  it("says what the last act did in one line", () => {
    expect(resultLine(null)).toBeNull();
    expect(
      resultLine({ outcome: "rotated", hub_status: 200, kind: "deployment", last4: "wxyz" }),
    ).toBe("rotated · hub: 200 · kind deployment · key …wxyz");
    expect(resultLine({ outcome: "refused", hub_status: null, error: "unreachable" })).toBe(
      "refused · hub: no answer · unreachable",
    );
  });
});

describe("the wiring", () => {
  it("the /aroh page is in the sidebar", () => {
    expect(readFileSync(join(SRC, "app", "Header.tsx"), "utf8")).toContain('href: "/aroh"');
  });

  it("🔴 the hub token is a password field and no client file reads console.ts", () => {
    const page = readFileSync(join(SRC, "app", "aroh", "ArohConnect.tsx"), "utf8");
    expect(page).toContain('type="password"');
    expect(page).not.toContain("@/lib/console");
    const tab = readFileSync(join(SRC, "app", "customers", "[slug]", "Aroh.tsx"), "utf8");
    expect(tab).not.toContain("@/lib/console");
  });

  it("the customer page has an Aroh tab", () => {
    const life = readFileSync(join(SRC, "lib", "lifecycle.ts"), "utf8");
    expect(life).toContain('{ key: "aroh", label: "Aroh" }');
  });
});
