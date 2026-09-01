// The identity row's judgements — and the honesty rule that outranks them:
// a name the audit log cannot back must never render.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { displayName, initials, readWhoami, showable } from "./whoami";

describe("parsing the Console's answer", () => {
  it("carries a session identity through", () => {
    expect(
      readWhoami({ method: "session", actor: "op@x.io", role: "admin" }),
    ).toEqual({ method: "session", actor: "op@x.io", role: "admin" });
  });

  it("🔴 break-glass names NOBODY, whatever the body claims", () => {
    // Even a Console bug that put a name on the break-glass answer must not
    // reach the screen: the audit log for that path says the shared token,
    // and the sidebar must never disagree with the audit log.
    expect(
      readWhoami({ method: "breakglass", actor: "spoof@x.io", role: "admin" }),
    ).toEqual({ method: "breakglass", actor: null, role: null });
  });

  it("answers null for garbage rather than rendering it", () => {
    expect(readWhoami(null)).toBeNull();
    expect(readWhoami("nope")).toBeNull();
    expect(readWhoami({ method: "session" })).toBeNull();
    expect(readWhoami({ method: "session", actor: "  " })).toBeNull();
    expect(readWhoami({ method: "other", actor: "x@y.z" })).toBeNull();
  });

  it("tolerates a missing role — interim sessions may not carry one", () => {
    const w = readWhoami({ method: "session", actor: "op@x.io" });
    expect(w).toEqual({ method: "session", actor: "op@x.io", role: null });
  });
});

describe("who gets a row", () => {
  it("only a named session", () => {
    expect(showable(readWhoami({ method: "session", actor: "a@b.c" }))).toBe(true);
    expect(showable(readWhoami({ method: "breakglass" }))).toBe(false);
    expect(showable(null)).toBe(false);
  });
});

describe("the name and the avatar", () => {
  it("shows the local part as the name", () => {
    expect(displayName("vjvarada@constellationspace.io")).toBe("vjvarada");
    expect(displayName("no-at-sign")).toBe("no-at-sign");
  });

  it("takes initials across separators, else the first two letters", () => {
    expect(initials("vijay.varada@x.io")).toBe("VV");
    expect(initials("vijay-varada@x.io")).toBe("VV");
    expect(initials("vjvarada@x.io")).toBe("VJ");
    expect(initials("x@x.io")).toBe("X");
  });

  it("never answers empty — an empty avatar collapses the box", () => {
    expect(initials("...@x.io")).toBe("?");
  });
});

describe("the wiring", () => {
  const read = (p: string) => readFileSync(join(__dirname, p), "utf8");

  it("🔴 the sidebar mounts the row, and the row judges through this module", () => {
    expect(read("../app/Header.tsx")).toContain("<Identity />");
    const identity = read("../app/Identity.tsx");
    expect(identity).toContain("showable(");
    expect(identity).toContain("readWhoami(");
  });

  it("the route answers for the CALLER, through the gate every route uses", () => {
    const route = read("../app/api/operator/session/route.ts");
    expect(route).toContain("export async function GET");
    expect(route).toContain("readOperatorSession");
  });
});
