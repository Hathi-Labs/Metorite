// WS-31 CP-10 slice 4 — our provider accounts.
//
// Spec: `specs/customer_console.md` §6A · §6B.2 · D57.7.
//
// ⚠️ **The subject is what an operator CONCLUDES, not what renders.** Two
// wrong conclusions cost real money, and both are the same mistake — counting
// rows instead of counting LIVE PLATFORM rows:
//
//   1. "We're covered" when the only live credential is scoped to one org.
//   2. "We're covered" when the row being read is revoked.
//
// ⚠️ **The secret must not appear in any read path.** Fenced structurally
// below rather than by example: the component must contain no GET, and the
// Console's own list query does not select the column.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  type ProviderAccount,
  armedProviders,
  byokOrgs,
  coverageLine,
  describeScope,
  groupByProvider,
  groupLine,
  groupStatus,
  healthLabel,
  healthTone,
  isLive,
  isPlatform,
  wouldRotate,
} from "./providers";

const SRC = join(__dirname, "..");
const ADMIN = readFileSync(join(SRC, "app", "providers", "ProviderAdmin.tsx"), "utf8");
const PAGE = readFileSync(join(SRC, "app", "providers", "page.tsx"), "utf8");
const READ = readFileSync(join(SRC, "lib", "read.ts"), "utf8");
const HEADER = readFileSync(join(SRC, "app", "Header.tsx"), "utf8");

const CRED = (over: Partial<ProviderAccount> = {}): ProviderAccount => ({
  id: "c1",
  provider: "anthropic",
  apiBase: null,
  label: null,
  orgSlug: null,
  createdAt: "2026-08-28T00:00:00Z",
  revokedAt: null,
  health: "unknown",
  lastCheckedAt: null,
  healthNote: null,
  ...over,
});

describe("coverage — the number that decides whether AI works", () => {
  it("counts a live platform credential", () => {
    expect(armedProviders([CRED()])).toEqual(["anthropic"]);
  });

  it("does NOT count a revoked one", () => {
    // 🔴 A revoked row still renders in the table. Counting it would tell an
    // operator they are covered while every call fails.
    expect(armedProviders([CRED({ revokedAt: "2026-08-28T01:00:00Z" })])).toEqual([]);
  });

  it("does NOT count a BYOK one", () => {
    // 🔴 The sharper of the two. A key scoped to one org looks exactly like
    // coverage in a list, and leaves every OTHER tenant with no AI at all.
    expect(armedProviders([CRED({ orgSlug: "acme" })])).toEqual([]);
  });

  it("de-duplicates and sorts, so the line reads the same every time", () => {
    expect(
      armedProviders([CRED(), CRED({ id: "c2" }), CRED({ id: "c3", provider: "openai" })]),
    ).toEqual(["anthropic", "openai"]);
  });

  it("reports BYOK organizations separately", () => {
    expect(byokOrgs([CRED(), CRED({ id: "c2", orgSlug: "acme" })])).toEqual(["acme"]);
  });
});

describe("the line an operator reads first", () => {
  it("says AI is DEAD when nothing is armed, not 'no rows'", () => {
    // 🔴 This is the shipped state — `provider_credential` held 0 live rows on
    // 2026-08-28. An empty table reads as a page nobody has used yet. It must
    // read as a system that cannot serve.
    const line = coverageLine([]);
    expect(line).toContain("every AI call fails");
    expect(line).not.toMatch(/^no rows/i);
  });

  it("says the same when the only credential is revoked", () => {
    expect(coverageLine([CRED({ revokedAt: "2026-08-28T01:00:00Z" })]))
      .toContain("every AI call fails");
  });

  it("names the providers once armed", () => {
    expect(coverageLine([CRED()])).toContain("anthropic");
  });
});

describe("rotation is the same POST, and must be said BEFORE the click", () => {
  it("warns when a live credential for that provider already exists", () => {
    expect(wouldRotate([CRED()], "anthropic", null)).toBe(true);
  });

  it("is case-insensitive, because the Console lower-cases the provider", () => {
    // `normalise_provider` means `Anthropic` and `anthropic` are ONE row, so a
    // case-sensitive check here would promise an insert and deliver a replace.
    expect(wouldRotate([CRED()], "Anthropic", null)).toBe(true);
  });

  it("does not warn across the platform/BYOK boundary", () => {
    // They are different rows under the partial unique index, so installing
    // one genuinely does not touch the other.
    expect(wouldRotate([CRED()], "anthropic", "acme")).toBe(false);
    expect(wouldRotate([CRED({ orgSlug: "acme" })], "anthropic", null)).toBe(false);
  });

  it("ignores a revoked row — replacing nothing is not a rotation", () => {
    expect(wouldRotate([CRED({ revokedAt: "2026-08-28T01:00:00Z" })], "anthropic", null))
      .toBe(false);
  });

  it("says nothing on an empty provider field", () => {
    expect(wouldRotate([CRED()], "", null)).toBe(false);
  });
});

describe("row labels", () => {
  it("distinguishes platform from BYOK in words, not a code", () => {
    expect(describeScope(CRED())).toContain("everyone");
    expect(describeScope(CRED({ orgSlug: "acme" }))).toContain("acme");
  });

  it("reads org_slug, not the scope string", () => {
    // `scope` is the Console's derived word. `org_slug` is the column the
    // Router keys on, so a disagreement between them must resolve to the row.
    expect(isPlatform(CRED({ orgSlug: "acme" }))).toBe(false);
    expect(isLive(CRED({ revokedAt: null }))).toBe(true);
  });
});

describe("the secret never crosses a read path", () => {
  it("the component runs no GET", () => {
    // ⚠️ The list is read by the server component with the caller's own
    // token. A client fetch would put it on a path the browser can replay.
    expect(ADMIN).not.toContain('method: "GET"');
    expect(ADMIN).not.toContain("/api/operator/providers?");
  });

  it("the secret field is MASKED BY DEFAULT and clears on success", () => {
    // ⚠️ **This test used to read `type="password"` literally, and the field
    // is now revealable.** The reveal is deliberate — a key typed by hand into
    // a masked box is unverifiable, and the retry costs a second visit to the
    // vendor's console. So the property worth pinning moved: not "always
    // masked", but "masked unless somebody asks, and never asking by default".
    expect(ADMIN).toContain("useState(false)");
    expect(ADMIN).toContain('showSecret ? "text" : "password"');
    expect(ADMIN).toContain('setSecret("")');
    expect(ADMIN).toContain('autoComplete="off"');
  });

  it("🔴 the reveal does not survive the save, or the panel closing", () => {
    // A revealed key left on screen outlives the reason it was revealed. Both
    // exits put it back behind the mask before anything else happens.
    const success = ADMIN.slice(ADMIN.indexOf("A key left in a field survives"));
    expect(success).toContain("setShowSecret(false)");
    const closer = ADMIN.slice(ADMIN.indexOf("function close()"));
    expect(closer.slice(0, 400)).toContain("setShowSecret(false)");
  });

  it("the page reads server-side and surfaces a failure", () => {
    // ⚠️ **Two files now, and the split is the point.** The page is a SERVER
    // component that reads through `read.ts`, and `read.ts` is the only place
    // that turns a non-200 into something a reader sees. Asserting the call
    // site alone would pass on a page that swallowed the status.
    expect(PAGE).not.toContain("use client");
    expect(PAGE).toContain("readAccounts");
    expect(READ).toContain("listProviderCreds");
    expect(READ).toContain("The Console answered");
  });

  it("🔴 the free-text card's sentinel can never be a real vendor", () => {
    // ⚠️ `openFor` holds a slug, and one value of it means "the card that is
    // not a vendor". If a vendor could ever be spelled that way, opening its
    // card would blank the vendor field and the operator would install a key
    // under whatever they typed next. The Console's own regex rules it out:
    // a slug must start with [a-z0-9], and this starts with an underscore.
    const sentinel = ADMIN.match(/const OTHER = "([^"]*)";/)?.[1];
    expect(sentinel).toBeDefined();
    expect(sentinel).not.toMatch(/^[a-z0-9][a-z0-9_.-]{1,39}$/);
    // And it must be plain ASCII. A sentinel written as a leading space once
    // reached disk as a NUL byte, which git then classified as binary while
    // every test and the typechecker passed on it.
    expect(sentinel).toMatch(/^[\x21-\x7e]+$/);
  });

  it("🔴 declares NO component inside the component", () => {
    // A component declared in `ProviderAdmin`'s body is a new function object
    // on every render, so React sees a different element type and REMOUNTS it.
    // The setup panel holds the secret field: typing one character calls
    // `setSecret`, re-renders, remounts the panel, and the caret is gone —
    // making the one field this page exists for unusable, one character at a
    // time. It typechecks, and every other test here passes on it.
    const body = ADMIN.slice(ADMIN.indexOf("export default function ProviderAdmin"));
    expect(body).not.toMatch(/\n {2}function [A-Z]/);
    // And the two that were inside must be at module scope, where their
    // identity is stable across renders.
    expect(ADMIN).toMatch(/\nfunction SetupPanel\(/);
    expect(ADMIN).toMatch(/\nfunction Card\(/);
  });

  it("relays a refusal VERBATIM", () => {
    // The Console owns the provider-shape, whitespace and length rules. A
    // paraphrase here would be a second vocabulary for one 400.
    expect(ADMIN).toContain("The Console refused:");
  });
});

describe("the surface", () => {
  it("is reachable from the sidebar", () => {
    // ⚠️ Two facts, not one object literal — see the note in catalog.test.ts.
    expect(HEADER).toContain('href: "/providers"');
    expect(HEADER).toContain('label: "Providers"');
  });

  it("so are the other two AI pages, in the same group", () => {
    expect(HEADER).toContain('href: "/models"');
    expect(HEADER).toContain('href: "/tiers"');
  });

  it("confirms before revoking, and says what revoking costs", () => {
    // 🔴 Revoking the platform credential stops every non-BYOK AI call.
    expect(ADMIN).toContain("confirm(");
    expect(ADMIN).toContain("not BYOK");
  });
});

describe("one card per vendor", () => {
  const ROWS = [
    CRED({ id: "a1", provider: "anthropic" }),
    CRED({ id: "a2", provider: "anthropic", label: "overflow" }),
    CRED({ id: "a3", provider: "anthropic", orgSlug: "acme" }),
    CRED({ id: "a4", provider: "anthropic", revokedAt: "2026-08-01T00:00:00Z" }),
    CRED({ id: "o1", provider: "openai" }),
    CRED({ id: "z1", provider: "groq", revokedAt: "2026-08-01T00:00:00Z" }),
  ];

  it("🔴 keeps SEVERAL platform keys for one vendor as several", () => {
    // The flat table drew these as unrelated rows, which is exactly why nobody
    // noticed we had a spare — a second key is how a rate limit stops being an
    // outage.
    const g = groupByProvider(ROWS).find((x) => x.provider === "anthropic");
    expect(g?.platform).toHaveLength(2);
    expect(g?.byok).toHaveLength(1);
    expect(g?.revoked).toHaveLength(1);
  });

  it("puts vendors we can actually call first", () => {
    // ⚠️ A dead vendor needs attention, but it is not what somebody came to
    // find. The banner above carries the alarm; this list stays useful.
    expect(groupByProvider(ROWS).map((g) => g.provider))
      .toEqual(["anthropic", "openai", "groq"]);
  });

  it("says plainly when a vendor has no live key at all", () => {
    const g = groupByProvider(ROWS).find((x) => x.provider === "groq");
    expect(groupLine(g!)).toContain("Nothing here can be called");
  });

  it("🔴 does not call a BYOK-only vendor covered", () => {
    const byokOnly = [CRED({ provider: "mistral", orgSlug: "acme" })];
    const g = groupByProvider(byokOnly)[0];
    expect(groupLine(g)).toContain("No account for everyone");
  });

  it("counts the spare rather than saying 'ok'", () => {
    const g = groupByProvider(ROWS).find((x) => x.provider === "anthropic");
    expect(groupLine(g!)).toContain("2 keys");
  });

  // ── The catalogue, not the receipt ──────────────────────────────────────
  //
  // 🔴 The page's whole job is getting the FIRST key installed, and with no
  // keys it drew nothing at all. These four pin the fix.

  it("🔴 draws a card for a vendor we hold NO key for", () => {
    const g = groupByProvider([], ["anthropic", "groq"]);
    expect(g.map((x) => x.provider)).toEqual(["anthropic", "groq"]);
    expect(g[0].platform).toHaveLength(0);
  });

  it("🔴 still draws a vendor we hold a key for that is not on the list", () => {
    // Hiding a live credential because somebody forgot to add its guide is
    // how a key gets forgotten — and it is our vendor bill.
    const held = [CRED({ provider: "acme-llm" })];
    expect(groupByProvider(held, ["anthropic"]).map((x) => x.provider))
      .toContain("acme-llm");
  });

  it("lists each vendor ONCE when it is both held and known", () => {
    const held = [CRED({ provider: "anthropic" })];
    const names = groupByProvider(held, ["anthropic", "groq"])
      .map((x) => x.provider);
    expect(names.filter((n) => n === "anthropic")).toHaveLength(1);
  });

  it("keeps armed vendors above the ones still to do", () => {
    const held = [CRED({ provider: "openai" })];
    expect(groupByProvider(held, ["anthropic", "openai"])[0].provider)
      .toBe("openai");
  });
});

describe("where a vendor stands, in one word", () => {
  it("🔴 separates 'never set up' from 'we revoked it'", () => {
    // ⚠️ The second is a decision somebody took. Drawing it as "not set up"
    // invites the next operator to quietly undo it.
    const untouched = groupByProvider([], ["mistral"])[0];
    const dropped = groupByProvider(
      [CRED({ provider: "mistral", revokedAt: "2026-08-01T00:00:00Z" })],
    )[0];
    expect(groupStatus(untouched)).toBe("untouched");
    expect(groupStatus(dropped)).toBe("dropped");
    expect(groupLine(untouched)).toBe("Not set up.");
    expect(groupLine(dropped)).toContain("revoked");
  });

  it("does not call a BYOK-only vendor armed", () => {
    const g = groupByProvider([CRED({ provider: "groq", orgSlug: "acme" })])[0];
    expect(groupStatus(g)).toBe("byok-only");
  });

  it("calls a vendor with a live platform key armed", () => {
    expect(groupStatus(groupByProvider([CRED({ provider: "groq" })])[0]))
      .toBe("armed");
  });
});

describe("the health dot", () => {
  it("🔴 paints an UNCHECKED account neutral, never green", () => {
    // Nothing probes a vendor account today, so every live row reports
    // `unknown`. Green would be a claim nobody measured, on the one screen
    // where believing it means not checking.
    expect(healthTone("unknown")).toBe("neutral");
    expect(healthLabel("unknown")).toBe("never checked");
  });

  it("separates slow from dead", () => {
    expect(healthTone("degraded")).toBe("warn");
    expect(healthTone("failing")).toBe("danger");
    expect(healthTone("ok")).toBe("ok");
  });
});
