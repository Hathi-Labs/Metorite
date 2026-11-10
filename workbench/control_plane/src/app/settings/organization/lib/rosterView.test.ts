import { describe, expect, it } from "vitest";
import {
  ROSTER_URL,
  emptyLine,
  plainText,
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
  const line = (status: Member["status"]) =>
    plainText(purgeAlternative(member("x@acme.test", status)));
  const named = (status: Member["status"]) =>
    purgeAlternative(member("x@acme.test", status))
      .filter((p) => p.em)
      .map((p) => p.text);

  it("a removed person is not sent to a Remove that their row lacks", () => {
    expect(named("removed")).toEqual([]);
    expect(line("removed")).not.toContain("Remove");
    expect(line("removed")).not.toContain("recoverable");
    expect(line("removed")).toContain("already removed");
  });

  it("an invited person is pointed at Revoke invite, the button their row has", () => {
    expect(named("invited")).toEqual(["Revoke invite"]);
    expect(line("invited")).not.toContain("recoverable");
  });

  it("a signed-in person is still pointed at Remove", () => {
    for (const s of ["active", "suspended"] as const) {
      expect(named(s)).toEqual(["Remove"]);
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
    expect(plainText(c.after)).not.toContain("Suspend");
    // Review round 1: an invited person holds no Core seat yet, so the copy
    // must make no claim about a seat.
    for (const text of [c.title, c.effect, plainText(c.after), c.confirm]) {
      expect(text.toLowerCase()).not.toContain("seat");
    }
  });

  it("each dialog emphasises the control it names (round 3)", () => {
    const em = (status: Member["status"]) =>
      removeCopy(member("x@acme.test", status))
        .after.filter((p) => p.em)
        .map((p) => p.text);
    expect(em("invited")).toEqual(["Delete permanently"]);
    expect(em("active")).toEqual(["Suspend"]);
  });

  it("a signed-in person is removed, with the seal preview", () => {
    const c = removeCopy({
      ...member("ana@acme.test", "active"),
      display_name: "Ana",
    });
    expect(c.title).toBe("Remove Ana?");
    expect(c.confirm).toBe("Remove");
    expect(c.showSeal).toBe(true);
    expect(plainText(c.after)).toContain("use Suspend instead");
  });
});
