// WS-20 WA-C3: the history import copy (spec §12.4.1 P12). Every state of
// the account and every answer of the connect has its own true sentence.

import { describe, expect, it } from "vitest";
import {
  accountHistoryLine,
  connectHistoryCopy,
  historyDeadlinePassed,
} from "./historySync";

const NOW = new Date("2026-10-07T12:00:00Z");
const LATER = "2026-10-08T06:00:00+00:00";
const EARLIER = "2026-10-07T06:00:00+00:00";

const account = (over: Record<string, unknown>) => ({
  history_sync_state: null,
  history_sync_error: null,
  history_import_progress: null,
  history_sync_deadline: LATER,
  ...over,
});

describe("connectHistoryCopy", () => {
  it("says a request is a request, not an import", () => {
    const copy = connectHistoryCopy("requested");
    expect(copy).toContain("asked Meta");
    expect(copy).not.toMatch(/imported/i);
  });

  it("points a failure at Numbers inside the window", () => {
    expect(connectHistoryCopy("failed")).toContain("Start it again from Numbers");
  });

  it("says a pending import has not started", () => {
    expect(connectHistoryCopy("pending")).toContain("has not started");
  });

  it("keeps the old promise only where nothing is imported", () => {
    for (const s of [null, undefined] as const) {
      expect(connectHistoryCopy(s)).toContain("Older chats are not imported");
    }
    expect(connectHistoryCopy("requested")).not.toContain("comes later");
  });
});

describe("historyDeadlinePassed", () => {
  it("reads the deadline against now", () => {
    expect(historyDeadlinePassed(LATER, NOW)).toBe(false);
    expect(historyDeadlinePassed(EARLIER, NOW)).toBe(true);
  });

  it("treats a missing or broken deadline as passed", () => {
    expect(historyDeadlinePassed(null, NOW)).toBe(true);
    expect(historyDeadlinePassed("not a date", NOW)).toBe(true);
  });
});

describe("accountHistoryLine", () => {
  it("draws nothing for an account that is not coexistence", () => {
    expect(accountHistoryLine(account({}), NOW)).toBeNull();
  });

  it("shows the progress while the import runs", () => {
    expect(
      accountHistoryLine(
        account({ history_sync_state: "requested", history_import_progress: 55 }),
        NOW
      )
    ).toEqual({ text: "Importing history · 55%", tone: "muted", canStart: false });
    expect(
      accountHistoryLine(account({ history_sync_state: "requested" }), NOW)?.text
    ).toBe("History import requested");
  });

  it("marks a complete import as success", () => {
    expect(
      accountHistoryLine(account({ history_sync_state: "complete" }), NOW)
    ).toEqual({ text: "History imported", tone: "success", canStart: false });
  });

  it("shows the decline from the server, and offers no retry", () => {
    const line = accountHistoryLine(
      account({ history_sync_state: "declined", history_sync_error: "Off in the app." }),
      NOW
    );
    expect(line).toEqual({ text: "Off in the app.", tone: "muted", canStart: false });
  });

  it("offers a retry for a failure inside the window", () => {
    const line = accountHistoryLine(
      account({ history_sync_state: "failed", history_sync_error: "Meta said no" }),
      NOW
    );
    expect(line).toEqual({
      text: "History import failed: Meta said no",
      tone: "destructive",
      canStart: true,
    });
  });

  it("offers a start for a pending import inside the window", () => {
    expect(
      accountHistoryLine(account({ history_sync_state: "pending" }), NOW)?.canStart
    ).toBe(true);
  });

  it("offers no button after the window, and says to connect again", () => {
    for (const state of ["pending", "failed"]) {
      const line = accountHistoryLine(
        account({ history_sync_state: state, history_sync_deadline: EARLIER }),
        NOW
      );
      expect(line?.canStart).toBe(false);
      expect(line?.text).toContain("connect it again");
    }
  });
});
