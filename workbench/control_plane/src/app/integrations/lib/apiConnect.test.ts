// API-5: the pure rules of the API connections in the console.
import { describe, expect, it } from "vitest";

import { connector } from "@/lib/connectors";

import {
  API_RETURN_KEYS,
  BOOKS_PROVIDER,
  addApiLabel,
  apiDisconnectCopy,
  apiReturn,
  apiRowState,
  connectErrorText,
  parseBooksStatus,
  pickErrorText,
  pickIsOver,
  resultCardView,
  startErrorText,
  t1Apis,
  usedBy,
  withoutApiReturn,
  type ResultCard,
} from "./apiConnect";
import { disconnectCopy, type Connection } from "./connections";

const CARD: ResultCard = {
  provider: BOOKS_PROVIDER,
  label: "Zoho Books",
  works_now: {
    unlocks: "Read invoices and totals from Zoho Books in the chat.",
    owning_app: "integrations",
    apps: [],
    mira: "Mira can now use Zoho Books when you ask.",
  },
  templates: [],
  build_offer: true,
};

const params = (q: string) => new URLSearchParams(q);

describe("the T1 list", () => {
  it("is the api: rows of the one connector list, with Zoho Books", () => {
    const ids = t1Apis().map((r) => r.id);
    expect(ids).toEqual([BOOKS_PROVIDER]);
    expect(connector(BOOKS_PROVIDER)?.scope).toBe("org");
  });

  it("names the tile by the switch: no flag keeps Add API", () => {
    expect(addApiLabel(false)).toBe("Add API");
    expect(addApiLabel(true)).toBe("Connect an API");
  });
});

describe("the status", () => {
  it("reads connected only from the gateway's own true", () => {
    expect(apiRowState(null)).toBe("not_connected");
    expect(apiRowState(parseBooksStatus({}))).toBe("not_connected");
    expect(apiRowState(parseBooksStatus({ connected: "true", status: "connected" }))).toBe("not_connected");
    expect(apiRowState(parseBooksStatus({ connected: true, status: "connected" }))).toBe("connected");
    expect(apiRowState(parseBooksStatus({ connected: false, status: "needs_reconnect" }))).toBe("needs_reconnect");
  });

  it("keeps the card of a connected row only, and drops a bad choice", () => {
    const s = parseBooksStatus({
      connected: false,
      card: CARD,
      choices: [{ id: "60001", name: "Acme" }, { id: "1/../2", name: "x" }, { id: 3 }, "x", { id: "60002" }],
    });
    expect(s.card).toBeNull();
    expect(s.choices).toEqual([
      { id: "60001", name: "Acme" },
      { id: "60002", name: "60002" },
    ]);
    expect(parseBooksStatus({ connected: true, card: CARD }).card).toEqual(CARD);
    expect(parseBooksStatus(null).connected).toBe(false);
  });
});

describe("the return from Zoho", () => {
  it("reads the three shapes of the BFF callback, and nothing else", () => {
    expect(apiReturn(params("api_connected=zoho_books"))).toEqual({ kind: "connected" });
    expect(apiReturn(params("api_connected=other"))).toBeNull();
    expect(apiReturn(params("api_pick=AbCdEfGhIjKlMnOpQrStUv"))).toEqual({
      kind: "pick",
      pick: "AbCdEfGhIjKlMnOpQrStUv",
    });
    expect(apiReturn(params("api_pick=short"))).toBeNull();
    expect(apiReturn(params("api_connect_error=test_read_failed"))).toEqual({ kind: "error", code: "test_read_failed" });
    expect(apiReturn(params("api_connect_error=<b>"))).toEqual({ kind: "error", code: "failed" });
    expect(apiReturn(params("view=keys"))).toBeNull();
  });

  it("clears only the keys of the return", () => {
    const q = params("view=keys&api_pick=AbCdEfGhIjKlMnOpQrStUv&api_connected=zoho_books&api_connect_error=x");
    expect(withoutApiReturn("/integrations", q)).toBe("/integrations?view=keys");
    expect(withoutApiReturn("/integrations", params("api_pick=x"))).toBe("/integrations");
    expect(API_RETURN_KEYS).toHaveLength(3);
  });

  it("says what is wrong and what to do, and never reads the prototype", () => {
    expect(connectErrorText("test_read_failed")).toContain("connect again");
    expect(connectErrorText("access_denied")).toContain("cancelled");
    for (const code of ["__proto__", "constructor", "toString", "nope", null]) {
      expect(typeof connectErrorText(code)).toBe("string");
      expect(connectErrorText(code)).toContain("Nothing was saved");
      expect(typeof pickErrorText(code)).toBe("string");
      expect(typeof startErrorText(code)).toBe("string");
    }
    expect(startErrorText("not_available")).toBe("Zoho Books is not on for your organization.");
    expect(pickErrorText("invalid_pick")).toContain("Connect Zoho Books again");
  });

  it("ends the pick only for a refusal that a new pick cannot fix", () => {
    expect(pickIsOver("invalid_pick")).toBe(true);
    expect(pickIsOver("unknown_organization")).toBe(true);
    expect(pickIsOver("test_read_failed")).toBe(false);
    expect(pickIsOver(undefined)).toBe(false);
  });
});

describe("the result card (IN-9)", () => {
  it("says what works now, that nothing uses it yet, then one next step", () => {
    const view = resultCardView(CARD);
    expect(view.title).toBe("Zoho Books is connected.");
    expect(view.lines).toEqual([
      "Read invoices and totals from Zoho Books in the chat.",
      "No app or chat tool uses it yet.",
      "Mira can now use Zoho Books when you ask.",
    ]);
    expect(view.next).toEqual({ label: "Build an app on Zoho Books", href: "/studio" });
  });

  it("names the apps that use it, and a template wins over a build", () => {
    const view = resultCardView({
      ...CARD,
      works_now: { ...CARD.works_now, apps: ["Revenue board"] },
      templates: ["Monthly revenue"],
    });
    expect(view.lines[1]).toBe("These use it now: Revenue board.");
    expect(view.next?.label).toBe("Start from the template Monthly revenue");
    expect(resultCardView({ ...CARD, build_offer: false }).next).toBeNull();
  });
});

describe("used by, and the disconnect preview (IN-10)", () => {
  const row = (over: Partial<Connection> = {}): Connection => ({
    id: "api:zoho_books:0b0e8c1e-0000-4000-8000-00000000a2a2",
    kind: "oauth",
    provider: BOOKS_PROVIDER,
    label: "Zoho Books",
    owning_app: "integrations",
    scope: "org",
    owner: "admin@acme.test",
    status: "connected",
    last_sync_at: null,
    last_error: null,
    setup_href: "/integrations",
    can_disconnect: true,
    detail: {},
    ...over,
  });

  it("lists nothing for an API connection today, and says so", () => {
    expect(usedBy(row())).toEqual({ names: [], line: "No app, workflow or chat tool uses this connection yet." });
    expect(usedBy(row({ provider: "zoho" }))).toBeNull();
    expect(usedBy(row({ provider: "github" }))).toBeNull();
  });

  it("asks once, and says what stops and what the disconnect does", () => {
    const copy = disconnectCopy(row());
    expect(copy).toEqual(apiDisconnectCopy(row()));
    expect(copy.title).toBe("Disconnect Zoho Books?");
    expect(copy.body).toContain("revokes the sign-in at Zoho");
    expect(copy.body).toContain("Nothing uses this connection now, so nothing stops.");
    expect(copy.confirmLabel).toBe("Disconnect");
    expect(disconnectCopy(row({ provider: "zoho" })).title).toBe("Disconnect Zoho?");
  });
});
