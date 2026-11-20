// API-5: the pure rules of the API connections in the console.
import { describe, expect, it } from "vitest";

import { connector } from "@/lib/connectors";

import {
  API_RETURN_KEYS,
  BOOKS_PROVIDER,
  addApiLabel,
  apiDisconnectCopy,
  chatLaterLine,
  apiReturn,
  apiRowState,
  connectErrorText,
  parseBooksStatus,
  pickErrorText,
  pickIsOver,
  resultCardView,
  returnDecision,
  startErrorText,
  t1Apis,
  usedBy,
  withoutApiReturn,
  type ResultCard,
} from "./apiConnect";
import { disconnectCopy, type Connection } from "./connections";

/** The card of Zoho Books today, as the gateway sends it: no chat reader yet (API-4). */
const CARD: ResultCard = {
  provider: BOOKS_PROVIDER,
  label: "Zoho Books",
  works_now: {
    unlocks: "Connects the invoices and totals of your Zoho Books organization to Metorite.",
    owning_app: "integrations",
    apps: [],
    mira: "The chat cannot read Zoho Books yet. That comes in a later release.",
  },
  templates: [],
  build_offer: false,
  chat_reads: false,
};

/** A card of a connection that the chat reads, for the next-step rules. */
const READ_CARD: ResultCard = {
  ...CARD,
  provider: "github",
  label: "GitHub",
  works_now: { ...CARD.works_now, mira: "Mira can now use GitHub when you ask." },
  build_offer: true,
  chat_reads: true,
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

  it("ends the pick for each refusal after the gateway took the entry", () => {
    // P1-A of the review: a failed test read took the entry and revoked the
    // token, so a second pick can only answer invalid_pick.
    expect(pickIsOver("invalid_pick")).toBe(true);
    expect(pickIsOver("unknown_organization")).toBe(true);
    expect(pickIsOver("test_read_failed")).toBe(true);
    expect(pickErrorText("test_read_failed")).toContain(
      "Connect Zoho Books again with an account that can read invoices",
    );
    expect(pickErrorText("test_read_failed")).not.toContain("pick another");
    expect(pickIsOver("pending_unavailable")).toBe(false);
    expect(pickIsOver(undefined)).toBe(false);
  });
});

describe("the result card (IN-9)", () => {
  it("says what is true now for Zoho Books: the chat cannot read it yet, and no build", () => {
    const view = resultCardView(CARD);
    expect(view.title).toBe("Zoho Books is connected.");
    expect(view.lines).toEqual([
      "Connects the invoices and totals of your Zoho Books organization to Metorite.",
      "No app or chat tool uses it yet.",
      "The chat cannot read Zoho Books yet. That comes in a later release.",
    ]);
    expect(view.lines.join(" ")).not.toContain("Mira can now");
    expect(view.next).toBeNull();
    // A template does not open a step either, while the chat cannot read it.
    expect(resultCardView({ ...CARD, templates: ["Monthly revenue"], build_offer: true }).next).toBeNull();
  });

  it("for a connection that the chat reads: names the apps, and a template wins over a build", () => {
    expect(resultCardView(READ_CARD).next).toEqual({ label: "Build an app on GitHub", href: "/studio" });
    const view = resultCardView({
      ...READ_CARD,
      works_now: { ...READ_CARD.works_now, apps: ["Revenue board"] },
      templates: ["Monthly revenue"],
    });
    expect(view.lines[1]).toBe("These use it now: Revenue board.");
    expect(view.next?.label).toBe("Start from the template Monthly revenue");
    expect(resultCardView({ ...READ_CARD, build_offer: false }).next).toBeNull();
  });

  it("reads chat_reads as false unless the gateway says true", () => {
    expect(parseBooksStatus({ connected: true, card: { ...CARD, chat_reads: "true" } }).card?.chat_reads).toBe(false);
    expect(parseBooksStatus({ connected: true, card: READ_CARD }).card?.chat_reads).toBe(true);
  });
});

describe("the one switch, chat_reads (API-5 review, P1-B)", () => {
  it("makes the dialog say that the chat cannot read Zoho Books yet", () => {
    const books = connector(BOOKS_PROVIDER)!;
    expect(books.chat_reads).toBe(false);
    expect(chatLaterLine(books)).toBe("The chat cannot read Zoho Books yet. That comes in a later release.");
    expect(books.unlocks).not.toMatch(/chat/i);
    expect(chatLaterLine({ label: "GitHub" })).toBe("");
  });
});

describe("the decision of a return (P3)", () => {
  const ok = parseBooksStatus({ connected: true, status: "connected", card: CARD });
  it("does nothing for a null status: the route is dark or the member has no right", () => {
    for (const ret of [{ kind: "connected" as const }, { kind: "pick" as const, pick: "x".repeat(20) }, null]) {
      expect(returnDecision(ret, null)).toEqual({ kind: "nothing" });
    }
  });

  it("shows the card only when the status says connected and carries a card", () => {
    expect(returnDecision({ kind: "connected" }, ok)).toEqual({ kind: "card", card: CARD });
    const not = parseBooksStatus({ connected: false, status: "disconnected", card: CARD });
    expect(returnDecision({ kind: "connected" }, not).kind).toBe("toast");
    const noCard = parseBooksStatus({ connected: true, status: "connected" });
    expect(returnDecision({ kind: "connected" }, noCard).kind).toBe("toast");
  });

  it("opens the pick with the gateway's choices, else says the pick expired", () => {
    const pending = parseBooksStatus({ choices: [{ id: "60001", name: "A" }] });
    expect(returnDecision({ kind: "pick", pick: "x".repeat(20) }, pending)).toEqual({
      kind: "pick",
      choices: [{ id: "60001", name: "A" }],
    });
    expect(returnDecision({ kind: "pick", pick: "x".repeat(20) }, parseBooksStatus({}))).toEqual({
      kind: "toast",
      text: pickErrorText("invalid_pick"),
    });
  });

  it("gives the words of an error code", () => {
    expect(returnDecision({ kind: "error", code: "test_read_failed" }, ok)).toEqual({
      kind: "toast",
      text: connectErrorText("test_read_failed"),
    });
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
