// The CRM's mode, connection and mirror marks (WS-53 CRM-U5, CRM-Z7).
import { describe, expect, it } from "vitest";

import {
  CONNECT_ERROR_FALLBACK,
  canCreate,
  clearReturnParams,
  connectErrorMessage,
  customRows,
  effectiveMode,
  firstOpen,
  humanizeKey,
  isMirrored,
  modeLine,
  needsReconnect,
  onlyInMetorite,
  progressDetail,
  progressPercent,
  returnNotice,
  shouldPoll,
  startFailureMessage,
  syncFailureMessage,
  type ConnectionInfo,
  type SyncStatus,
} from "./connect";

const conn = (over: Partial<ConnectionInfo> = {}): ConnectionInfo => ({
  connected: true,
  provider: "zoho",
  owning_app: "crm",
  status: "connected",
  location: "in",
  scopes: [],
  connected_by: "a@x.test",
  last_sync_at: null,
  last_error: null,
  mode: null,
  ...over,
});

const sync = (over: Partial<SyncStatus> = {}): SyncStatus => ({
  state: "idle",
  kind: null,
  started_at: null,
  finished_at: null,
  last_success_at: null,
  counts: {},
  estimate: null,
  error: null,
  next_run_at: null,
  ...over,
});

describe("firstOpen (CRM-U5)", () => {
  const base = { loading: false, settingsMode: undefined, isAdmin: true };

  it("waits while a read runs", () => {
    expect(firstOpen({ ...base, loading: true, connection: null })).toBe("loading");
  });
  it("shows two cards to an admin with the mirror on and no mode", () => {
    expect(firstOpen({ ...base, connection: conn() })).toBe("choose");
  });
  it("shows the Native card only when the mirror is off and the mode is not chosen", () => {
    expect(firstOpen({ ...base, connection: null, settingsMode: null })).toBe("native-only");
  });
  it("tells a member to ask an admin", () => {
    expect(firstOpen({ ...base, isAdmin: false, connection: conn() })).toBe("wait-admin");
  });
  it("opens the pipeline once a mode exists", () => {
    expect(firstOpen({ ...base, connection: conn({ mode: "native" }) })).toBe("ready");
    expect(firstOpen({ ...base, connection: conn({ mode: "mirror" }), isAdmin: false })).toBe(
      "ready",
    );
    expect(firstOpen({ ...base, connection: null, settingsMode: "native" })).toBe("ready");
  });
  it("never locks a member who cannot read the mode", () => {
    expect(firstOpen({ ...base, isAdmin: false, connection: null })).toBe("ready");
    expect(firstOpen({ ...base, connection: null, settingsMode: undefined })).toBe("ready");
  });
  it("reads the mode from the connection first, then from the settings", () => {
    expect(effectiveMode(conn({ mode: "mirror" }), "native")).toBe("mirror");
    expect(effectiveMode(null, "native")).toBe("native");
    expect(effectiveMode(null, undefined)).toBeNull();
  });
});

describe("the title bar line (spec 8.1)", () => {
  const now = Date.parse("2026-10-11T12:00:00Z");
  it("says Native", () => {
    expect(modeLine({ mode: "native", connection: null, sync: null })).toBe("Native");
  });
  it("says when Zoho last synced", () => {
    const line = modeLine({
      mode: "mirror",
      connection: conn(),
      sync: sync({ last_success_at: "2026-10-11T11:56:00Z" }),
      nowMs: now,
    });
    expect(line).toBe("Synced from Zoho · 4m ago");
  });
  it("falls back to the connection's last sync", () => {
    const line = modeLine({
      mode: "mirror",
      connection: conn({ last_sync_at: "2026-10-11T10:00:00Z" }),
      sync: null,
      nowMs: now,
    });
    expect(line).toBe("Synced from Zoho · 2h ago");
  });
  it("says Importing while a sync runs", () => {
    expect(
      modeLine({ mode: "mirror", connection: conn(), sync: sync({ state: "running" }) }),
    ).toBe("Importing from Zoho…");
  });
  it("says nothing before a mode exists", () => {
    expect(modeLine({ mode: null, connection: null, sync: null })).toBeNull();
  });
  it("tells a lapsed consent from a good one", () => {
    expect(needsReconnect(conn({ status: "needs_reconnect" }))).toBe(true);
    expect(needsReconnect(conn())).toBe(false);
    expect(needsReconnect(null)).toBe(false);
  });
});

describe("the return from Zoho (spec 8.3)", () => {
  it("reads a success", () => {
    expect(returnNotice("connected=zoho")).toEqual({ kind: "connected" });
  });
  it("has words for each refusal code", () => {
    for (const code of [
      "access_denied",
      "invalid_state",
      "untrusted_host",
      "mode_is_native",
      "zoho_not_configured",
    ]) {
      const notice = returnNotice(`connect_error=${code}`);
      expect(notice).toMatchObject({ kind: "error", code });
      expect(connectErrorMessage(code)).not.toBe(CONNECT_ERROR_FALLBACK);
    }
  });
  it("falls back for an unknown code", () => {
    expect(connectErrorMessage("weird")).toBe(CONNECT_ERROR_FALLBACK);
    expect(connectErrorMessage(null)).toBe(CONNECT_ERROR_FALLBACK);
  });
  it("sees no trip in any other query", () => {
    expect(returnNotice("")).toBeNull();
    expect(returnNotice("tab=deals&deal=1")).toBeNull();
    expect(returnNotice("connected=other")).toBeNull();
  });
  it("clears the two parameters and keeps the rest", () => {
    expect(clearReturnParams("connected=zoho&tab=deals")).toBe("tab=deals");
    expect(clearReturnParams("connect_error=access_denied")).toBe("");
  });
  it("words a refused Connect and a refused Sync now", () => {
    expect(startFailureMessage(409, "mode_is_native")).toMatch(/already uses Metorite/);
    expect(startFailureMessage(503, "zoho_not_configured")).toMatch(/not set up/);
    expect(startFailureMessage(500, undefined)).toBe(CONNECT_ERROR_FALLBACK);
    expect(syncFailureMessage(409, "sync_running")).toMatch(/already running/);
    expect(syncFailureMessage(409, "not_connected")).toMatch(/not connected/);
  });
});

describe("the import progress", () => {
  const running = sync({
    state: "running",
    kind: "import",
    counts: { contacts: 100, deals: 50 },
    estimate: { contacts: 400, deals: 100 },
  });
  it("reports a percent against the estimate", () => {
    expect(progressPercent(running)).toBe(30);
    expect(progressDetail(running)).toBe("Contacts 100 of 400 · Deals 50 of 100");
  });
  it("reports counts only when there is no estimate", () => {
    const noEstimate = sync({ state: "running", counts: { contacts: 1200 } });
    expect(progressPercent(noEstimate)).toBeNull();
    expect(progressDetail(noEstimate)).toBe("Contacts 1,200");
  });
  it("has no percent with no counts", () => {
    expect(progressPercent(sync({ state: "running" }))).toBeNull();
    expect(progressPercent(null)).toBeNull();
  });
  it("polls while a sync runs, and during a watch", () => {
    expect(shouldPoll(running, false)).toBe(true);
    expect(shouldPoll(sync(), false)).toBe(false);
    expect(shouldPoll(null, true)).toBe(true);
  });
});

describe("the mirror marks (spec 3.3)", () => {
  it("hides New only in Mirror mode", () => {
    expect(canCreate("mirror")).toBe(false);
    expect(canCreate("native")).toBe(true);
    expect(canCreate(null)).toBe(true);
  });
  it("knows a mirrored record by its ext_source", () => {
    expect(isMirrored({ ext_source: "zoho" })).toBe(true);
    expect(isMirrored({ ext_source: null })).toBe(false);
    expect(isMirrored({})).toBe(false);
    expect(isMirrored(null)).toBe(false);
  });
  it("badges a Metorite-only activity under a mirrored record", () => {
    expect(onlyInMetorite({ ext_source: null }, true)).toBe(true);
    expect(onlyInMetorite({}, true)).toBe(true);
    expect(onlyInMetorite({ ext_source: "zoho" }, true)).toBe(false);
    expect(onlyInMetorite({ ext_source: null }, false)).toBe(false);
  });
  it("humanizes a custom key", () => {
    expect(humanizeKey("Lead_Source")).toBe("Lead Source");
    expect(humanizeKey("annual_revenue")).toBe("Annual revenue");
    expect(humanizeKey("leadScore")).toBe("Lead Score");
  });
  it("lists the custom fields as label and value", () => {
    const rows = customRows({
      custom: { Lead_Source: "Web", Hot: true, Empty: null, Tags: ["a", "b"], Owner: { name: "Dana" } },
    });
    expect(rows.map((r) => [r.label, r.value])).toEqual([
      ["Lead Source", "Web"],
      ["Hot", "Yes"],
      ["Empty", "—"],
      ["Tags", "a, b"],
      ["Owner", "Dana"],
    ]);
    expect(customRows({})).toEqual([]);
    expect(customRows({ custom: null })).toEqual([]);
  });
});
