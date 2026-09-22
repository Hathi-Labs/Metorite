/**
 * The seats matrix — the arithmetic, where a seat can go missing.
 *
 * Spec: `people_center_app.md` §5.6.
 */

import { describe, expect, it } from "vitest";

import { actionFor, buildSeatMatrix } from "./seats";
import type { SeatGroup, SeatMember, SeatPerson } from "./seats";

const GROUPS: SeatGroup[] = [
  {
    slug: "sales",
    display_name: "Sales",
    is_center: true,
    members: [{ email: "Priya@Fracktal.in", role: "lead" }],
  },
  {
    slug: "engineering",
    display_name: "Engineering",
    is_center: true,
    members: [{ email: "priya@fracktal.in", role: "member" }],
  },
  { slug: "book-club", display_name: "Book club", is_center: false, members: [] },
];

const MEMBERS: SeatMember[] = [
  {
    email: "priya@fracktal.in",
    display_name: "Priya Sharma",
    status: "active",
    roles: ["admin"],
  },
  { email: "ghost@fracktal.in", display_name: "Ghost", roles: ["member"] },
];

const PEOPLE: SeatPerson[] = [
  {
    id: "p1",
    name: "Priya Sharma",
    email: "PRIYA@fracktal.in",
    title: "Firmware lead",
    department: "Engineering",
    has_login: true,
  },
  {
    id: "p2",
    name: "Rahul Contractor",
    email: "rahul@outside.example",
    title: "Contractor",
    has_login: false,
  },
  { id: "p3", name: "No Address", email: null },
];

function matrix() {
  return buildSeatMatrix(GROUPS, MEMBERS, PEOPLE);
}

describe("the columns", () => {
  it("puts Centers first, then everything else alphabetically", () => {
    expect(matrix().columns.map((c) => c.slug)).toEqual([
      "engineering",
      "sales",
      "book-club",
    ]);
  });
});

describe("the rows", () => {
  it("is the UNION of the directory and the roster", () => {
    // Priya is in both, Rahul is directory-only, Ghost is roster-only.
    // Taking either source alone would lose one of the three.
    expect(matrix().rows.map((r) => r.name)).toEqual([
      "Ghost",
      "Priya Sharma",
      "Rahul Contractor",
    ]);
  });

  it("drops a person with no address, because nothing can join them", () => {
    expect(matrix().rows.some((r) => r.name === "No Address")).toBe(false);
  });

  it("counts the two kinds of mismatch separately", () => {
    const m = matrix();
    expect(m.directoryOnly).toBe(1); // Rahul
    expect(m.membersWithoutADirectoryRow).toBe(1); // Ghost
  });
});

describe("the addresses are compared lowercased", () => {
  it("joins `Priya@Fracktal.in`, `priya@…` and `PRIYA@…` into ONE row", () => {
    // The three sources spell it three ways on purpose. `lower(email)` is the
    // join everywhere else in the product, and a raw comparison here would
    // split Priya into three rows and halve her seats in each.
    const rows = matrix().rows.filter((r) => r.email.toLowerCase().startsWith("priya"));
    expect(rows).toHaveLength(1);
    expect(rows[0].seatCount).toBe(2);
  });

  it("keeps the lead flag through the case difference", () => {
    const priya = matrix().rows.find((r) => r.name === "Priya Sharma")!;
    const sales = priya.cells.find((c) => c.slug === "sales")!;
    expect(sales.member).toBe(true);
    expect(sales.lead).toBe(true);
    const engineering = priya.cells.find((c) => c.slug === "engineering")!;
    expect(engineering.member).toBe(true);
    expect(engineering.lead).toBe(false);
  });
});

describe("a seat needs a login", () => {
  it("marks the directory-only person as having none", () => {
    const rahul = matrix().rows.find((r) => r.name === "Rahul Contractor")!;
    expect(rahul.hasLogin).toBe(false);
    expect(rahul.seatCount).toBe(0);
  });

  it("takes the ROSTER as the authority, not the directory's derived flag", () => {
    // `has_login: false` on the directory row, but a roster row exists. The
    // roster row IS the `app_user`, and `org_group_member` references it, so
    // that is the one that decides whether a seat can exist.
    const m = buildSeatMatrix(
      GROUPS,
      [{ email: "rahul@outside.example", display_name: "Rahul" }],
      PEOPLE,
    );
    expect(m.rows.find((r) => r.name === "Rahul Contractor")!.hasLogin).toBe(true);
  });

  it("answers `blocked` for every column of a person who cannot hold a seat", () => {
    const rahul = matrix().rows.find((r) => r.name === "Rahul Contractor")!;
    for (const column of matrix().columns) {
      expect(actionFor(rahul, column.slug)).toBe("blocked");
    }
  });
});

describe("what a toggle would do", () => {
  it("removes where the person is already in, and adds where they are not", () => {
    const priya = matrix().rows.find((r) => r.name === "Priya Sharma")!;
    expect(actionFor(priya, "sales")).toBe("remove");
    expect(actionFor(priya, "book-club")).toBe("add");
  });

  it("adds for a column the matrix does not carry rather than throwing", () => {
    const priya = matrix().rows.find((r) => r.name === "Priya Sharma")!;
    expect(actionFor(priya, "not-a-group")).toBe("add");
  });
});

describe("the empty cases", () => {
  it("no groups is an empty matrix, not a crash", () => {
    const m = buildSeatMatrix([], MEMBERS, PEOPLE);
    expect(m.columns).toEqual([]);
    expect(m.rows.every((r) => r.cells.length === 0)).toBe(true);
  });

  it("nobody at all is an empty matrix", () => {
    const m = buildSeatMatrix(GROUPS, [], []);
    expect(m.rows).toEqual([]);
    expect(m.directoryOnly).toBe(0);
    expect(m.membersWithoutADirectoryRow).toBe(0);
  });

  it("a group with no members array does not throw", () => {
    const m = buildSeatMatrix([{ slug: "x", display_name: "X" }], MEMBERS, PEOPLE);
    expect(m.rows.every((r) => r.cells[0].member === false)).toBe(true);
  });
});
