import { encode } from "next-auth/jwt";
import { describe, expect, it } from "vitest";
import {
  SESSION_COOKIE_PLAIN,
  SESSION_COOKIE_SECURE,
  SLOT_COUNT,
  activeSessionCookie,
  decodeAccount,
  isSlotCookieName,
  otherAccounts,
  readSlots,
  slotCookieName,
  stashIndex,
  type Account,
  type Slot,
} from "./accountSlots";

/**
 * MT-1k slice A2, rule 1: a slot counts only if it decodes with the session
 * secret and has not expired. The tokens here are real Auth.js tokens, made by
 * the library's own `encode`, so a change to the token format fails this file.
 */
const SECRET = "test-secret-at-least-32-characters-long!";
const NOW = Date.now();

/** A token that expires at `expMs`. ⚠️ `encode` sets `exp` from `maxAge` and
 *  ignores an `exp` in the payload, exactly as it does for a real sign-in. */
async function token(email: string, expMs: number, opts: { salt?: string; secret?: string } = {}) {
  return encode({
    token: { email, name: email.split("@")[0] },
    salt: opts.salt ?? SESSION_COOKIE_SECURE,
    secret: opts.secret ?? SECRET,
    maxAge: Math.round((expMs - Date.now()) / 1000),
  });
}

function jar(entries: Record<string, string>) {
  return { get: (n: string) => (n in entries ? { value: entries[n] } : undefined) };
}

const acct = (email: string, exp: number): Account => ({ email, name: null, exp });
const slot = (index: number, email: string, exp: number): Slot => ({ index, raw: `raw-${index}`, account: acct(email, exp) });

describe("activeSessionCookie", () => {
  it("finds the secure cookie on https and the plain one on http", () => {
    expect(activeSessionCookie(jar({ [SESSION_COOKIE_SECURE]: "a" }))).toEqual({
      name: SESSION_COOKIE_SECURE,
      secure: true,
      value: "a",
    });
    expect(activeSessionCookie(jar({ [SESSION_COOKIE_PLAIN]: "b" }))?.secure).toBe(false);
  });

  it("declines a chunked session, because a slot holds one cookie", () => {
    expect(activeSessionCookie(jar({ [`${SESSION_COOKIE_SECURE}.0`]: "a" }))).toBeNull();
  });

  it("recognises only slot cookies, in either form, for the proxy's sweep", () => {
    expect(isSlotCookieName("__Host-mt-acct-0")).toBe(true);
    expect(isSlotCookieName("mt-acct-3")).toBe(true);
    expect(isSlotCookieName("__Secure-authjs.session-token")).toBe(false);
    expect(isSlotCookieName("mt-acct-10")).toBe(false);
    expect(isSlotCookieName("x-mt-acct-1")).toBe(false);
  });

  it("names the slots with the same prefix rule", () => {
    expect(slotCookieName(0, true)).toBe("__Host-mt-acct-0");
    expect(slotCookieName(3, false)).toBe("mt-acct-3");
  });
});

describe("decodeAccount (rule 1)", () => {
  it("reads a good token", async () => {
    const t = await token("a@one.test", NOW + 86400_000);
    expect(await decodeAccount(t, SESSION_COOKIE_SECURE, SECRET, NOW)).toMatchObject({ email: "a@one.test" });
  });

  it("drops an expired token", async () => {
    const t = await token("a@one.test", NOW - 60_000);
    expect(await decodeAccount(t, SESSION_COOKIE_SECURE, SECRET, NOW)).toBeNull();
  });

  it("drops a token by OUR expiry check, not only the library's", async () => {
    // jose already refuses a token past its exp (with 15 s of tolerance), so
    // an already-expired token cannot tell whether this file checks at all.
    // Here the library accepts the token, because it is valid by the real
    // clock, and only decodeAccount's own check, against `nowMs`, refuses it.
    const t = await token("a@one.test", NOW + 3600_000);
    expect(await decodeAccount(t, SESSION_COOKIE_SECURE, SECRET, NOW + 2 * 3600_000)).toBeNull();
    expect(await decodeAccount(t, SESSION_COOKIE_SECURE, SECRET, NOW)).not.toBeNull();
  });

  it("drops a token made with another secret, as after a rotation", async () => {
    const t = await token("a@one.test", NOW + 86400_000, { secret: "another-secret-also-32-characters-long" });
    expect(await decodeAccount(t, SESSION_COOKIE_SECURE, SECRET, NOW)).toBeNull();
  });

  it("drops a token made with another salt, and garbage", async () => {
    const t = await token("a@one.test", NOW + 86400_000, { salt: "something-else" });
    expect(await decodeAccount(t, SESSION_COOKIE_SECURE, SECRET, NOW)).toBeNull();
    expect(await decodeAccount("not-a-token", SESSION_COOKIE_SECURE, SECRET, NOW)).toBeNull();
  });
});

describe("readSlots", () => {
  it("returns good slots in place, and null for empty or bad ones", async () => {
    const good = await token("b@two.test", NOW + 86400_000);
    const stale = await token("c@three.test", NOW - 60_000);
    const active = { name: SESSION_COOKIE_SECURE, secure: true, value: "x" };
    const slots = await readSlots(
      jar({ [slotCookieName(1, true)]: good, [slotCookieName(2, true)]: stale, [slotCookieName(3, true)]: "junk" }),
      active,
      SECRET,
      NOW,
    );
    expect(slots).toHaveLength(SLOT_COUNT);
    expect(slots.map((s) => s?.account.email ?? null)).toEqual([null, "b@two.test", null, null]);
  });
});

describe("stashIndex (rule 5)", () => {
  it("reuses the slot that holds the same email, in any case", () => {
    expect(stashIndex([null, slot(1, "A@one.test", 5)], "a@one.test")).toBe(1);
  });

  it("takes the first empty slot", () => {
    expect(stashIndex([slot(0, "b@x", 5), null, null, null], "a@one.test")).toBe(1);
  });

  it("replaces the slot that expires first when all are full", () => {
    const full = [slot(0, "b@x", 50), slot(1, "c@x", 10), slot(2, "d@x", 30), slot(3, "e@x", 40)];
    expect(stashIndex(full, "a@one.test")).toBe(1);
  });
});

describe("otherAccounts", () => {
  it("never lists the active email, and lists each other email once, newest token first", () => {
    const active = acct("a@one.test", 100);
    const list = otherAccounts(active, [
      slot(0, "A@ONE.test", 90),
      slot(1, "b@two.test", 50),
      slot(2, "b@two.test", 70),
      null,
    ]);
    expect(list.map((s) => [s.index, s.account.email])).toEqual([[2, "b@two.test"]]);
  });
});
