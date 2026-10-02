// WS-17 EM-T3d — the mapper and the copy of Organisation → Email.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.3, "EM-T3d".
//
// D-EM-4: an admin sees how many members connected a mailbox, and never their
// mail. The gateway answers seven integers. These cases hold the browser half:
// the mapper keeps those seven and nothing else, and a read that is not seven
// integers is a FAILED read, never a row of zeros.
import { describe, expect, it } from "vitest";

import {
  COUNT_LABELS,
  EMAIL_TAB_COPY,
  countTiles,
  mapConnectionCounts,
  readConnectionCounts,
  type ConnectionCounts,
} from "./emailConnections";

const WIRE = {
  members: 2,
  mailboxes: 3,
  microsoft: 1,
  gmail: 1,
  imap: 1,
  sync_errors: 1,
  first_sync_pending: 1,
};

const SEVEN: (keyof ConnectionCounts)[] = [
  "members",
  "mailboxes",
  "microsoft",
  "gmail",
  "imap",
  "syncErrors",
  "firstSyncPending",
];

/** Every string the tab can draw. */
function allCopy(): string[] {
  return [
    ...Object.values(EMAIL_TAB_COPY.approval),
    ...Object.values(EMAIL_TAB_COPY.counts),
    ...Object.values(COUNT_LABELS),
  ];
}

describe("mapConnectionCounts (EM-T3d)", () => {
  it("keeps the seven integer counts", () => {
    expect(mapConnectionCounts(WIRE)).toEqual({
      members: 2,
      mailboxes: 3,
      microsoft: 1,
      gmail: 1,
      imap: 1,
      syncErrors: 1,
      firstSyncPending: 1,
    });
  });

  it("drops every other field, a string address first of all", () => {
    const out = mapConnectionCounts({
      ...WIRE,
      email_address: "alice@contoso.test",
      account_id: "8a0c3d4e-0000-4000-8000-000000000000",
      member: "bob@contoso.test",
      sync_error: "token refused for alice@contoso.test",
    });
    expect(out).not.toBeNull();
    expect(Object.keys(out!).sort()).toEqual([...SEVEN].sort());
    expect(JSON.stringify(out)).not.toContain("@");
  });

  it("refuses a payload that is not seven non-negative integers", () => {
    const bad: unknown[] = [
      null,
      undefined,
      "2",
      [],
      [WIRE],
      {},
      { ...WIRE, members: undefined },
      { ...WIRE, gmail: "1" },
      { ...WIRE, imap: 1.5 },
      { ...WIRE, sync_errors: -1 },
      { ...WIRE, first_sync_pending: Number.NaN },
      { ...WIRE, microsoft: true },
    ];
    for (const raw of bad) {
      expect(mapConnectionCounts(raw), JSON.stringify(raw)).toBeNull();
    }
  });

  it("keeps a real zero, which is an answer", () => {
    const zeros = Object.fromEntries(Object.keys(WIRE).map((k) => [k, 0]));
    expect(mapConnectionCounts(zeros)).toEqual(
      Object.fromEntries(SEVEN.map((k) => [k, 0])),
    );
  });
});

describe("readConnectionCounts (EM-T3d)", () => {
  it("a refusal or an outage is a failed read", () => {
    expect(readConnectionCounts(false, WIRE)).toEqual({ state: "failed" });
    expect(readConnectionCounts(false, null)).toEqual({ state: "failed" });
  });

  it("an answer that is not seven integers is a failed read, never zeros", () => {
    expect(readConnectionCounts(true, {})).toEqual({ state: "failed" });
    expect(readConnectionCounts(true, { detail: "Forbidden" })).toEqual({ state: "failed" });
  });

  it("seven integers are a ready read", () => {
    const read = readConnectionCounts(true, WIRE);
    expect(read.state).toBe("ready");
    expect(read.state === "ready" && read.counts.members).toBe(2);
  });
});

describe("countTiles (EM-T3d)", () => {
  it("draws the seven counts in a fixed order, and nothing else", () => {
    const tiles = countTiles(mapConnectionCounts(WIRE)!);
    expect(tiles.map((t) => t.key)).toEqual(SEVEN);
    expect(tiles.map((t) => t.value)).toEqual([2, 3, 1, 1, 1, 1, 1]);
    for (const t of tiles) expect(t.label).toBe(COUNT_LABELS[t.key]);
  });
});

describe("the copy (EM-T3d)", () => {
  it("never claims that the organization approved the app", () => {
    // Microsoft holds the approval. Metorite records nothing, so it cannot
    // know. The word does not appear at all, so no sentence can claim it.
    for (const s of allCopy()) {
      expect(s, s).not.toMatch(/\bapproved\b|\bverified\b|\bconfirmed\b/i);
    }
  });

  it("says members connected a mailbox, never 'of your members'", () => {
    // A removed member counts until a purge (spec risk "A removed member").
    expect(EMAIL_TAB_COPY.counts.intro).toMatch(/members connected a mailbox/);
    for (const s of allCopy()) expect(s, s).not.toMatch(/of your members/i);
  });

  it("holds no address", () => {
    for (const s of allCopy()) expect(s, s).not.toContain("@");
  });

  it("names a failed read as a failure, with no number in it", () => {
    expect(EMAIL_TAB_COPY.counts.failed).toMatch(/could not/i);
    expect(EMAIL_TAB_COPY.counts.failed).not.toMatch(/\d/);
  });
});
