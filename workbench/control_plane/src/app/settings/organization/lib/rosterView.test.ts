import { describe, expect, it } from "vitest";
import {
  ROSTER_URL,
  emptyLine,
  purgeAlternative,
  removeCopy,
  rowsFor,
  splitRoster,
} from "./rosterView";
import type { Member } from "@/app/settings/members/types";

function member(email: string, status: Member["status"]): Member {
  return { email, display_name: "", status, roles: [] };
}

const ROWS = [
  member("ana@acme.test", "active"),
  member("ben@acme.test", "invited"),
  member("cy@acme.test", "suspended"),
  member("dee@acme.test", "removed"),
];

describe("the roster read", () => {
  it("asks for the removed rows in the same request", () => {
    expect(ROSTER_URL).toBe("/api/admin/members?include_removed=true");
  });

  it("keeps removed people out of the live roster", () => {
    const { members, removed } = splitRoster(ROWS);
    expect(members.map((m) => m.email)).toEqual([
      "ana@acme.test",
      "ben@acme.test",
      "cy@acme.test",
    ]);
    expect(removed.map((m) => m.email)).toEqual(["dee@acme.test"]);
  });
});

describe("rowsFor", () => {
  const { members, removed } = splitRoster(ROWS);

  it("All leaves the removed people out", () => {
    expect(rowsFor("all", members, removed).map((m) => m.status)).toEqual([
      "active",
      "invited",
      "suspended",
    ]);
  });

  it("Removed shows only the removed people", () => {
    expect(rowsFor("removed", members, removed).map((m) => m.email)).toEqual([
      "dee@acme.test",
    ]);
  });

  it("each status pill shows its own status", () => {
    for (const s of ["active", "invited", "suspended"] as const) {
      const rows = rowsFor(s, members, removed);
      expect(rows).toHaveLength(1);
      expect(rows[0].status).toBe(s);
    }
  });

  it("an empty Removed list does not read like a failed load", () => {
    expect(emptyLine("removed")).toBe("Nobody has been removed.");
    expect(emptyLine("all")).toBe("No members here yet.");
  });
});

describe("purgeAlternative", () => {
  const line = (status: Member["status"]) => {
    const a = purgeAlternative(member("x@acme.test", status));
    return `${a.lead}${a.action ?? ""}${a.tail}`;
  };

  it("a removed person is not sent to a Remove that their row lacks", () => {
    const a = purgeAlternative(member("dee@acme.test", "removed"));
    expect(a.action).toBeNull();
    expect(line("removed")).not.toContain("Remove");
    expect(line("removed")).not.toContain("recoverable");
    expect(line("removed")).toContain("already removed");
  });

  it("everybody else is still pointed at Remove", () => {
    for (const s of ["active", "invited", "suspended"] as const) {
      expect(purgeAlternative(member("x@acme.test", s)).action).toBe("Remove");
      expect(line(s)).toContain("recoverable");
    }
  });
});

describe("removeCopy", () => {
  it("an invite is revoked, with no seal preview and no Suspend advice", () => {
    const c = removeCopy(member("ben@acme.test", "invited"));
    expect(c.title).toContain("Revoke the invite");
    expect(c.confirm).toBe("Revoke invite");
    expect(c.showSeal).toBe(false);
    // Suspend is not offered on an invited row, so the dialog must not
    // send the admin to it.
    expect(c.after).not.toContain("Suspend");
  });

  it("a signed-in person is removed, with the seal preview", () => {
    const c = removeCopy({
      ...member("ana@acme.test", "active"),
      display_name: "Ana",
    });
    expect(c.title).toBe("Remove Ana?");
    expect(c.confirm).toBe("Remove");
    expect(c.showSeal).toBe(true);
    expect(c.after).toContain("Suspend");
  });
});
