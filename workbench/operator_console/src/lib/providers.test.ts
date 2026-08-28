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
  type ProviderCred,
  armedProviders,
  byokOrgs,
  coverageLine,
  describeScope,
  isLive,
  isPlatform,
  wouldRotate,
} from "./providers";

const SRC = join(__dirname, "..");
const ADMIN = readFileSync(join(SRC, "app", "providers", "ProviderAdmin.tsx"), "utf8");
const PAGE = readFileSync(join(SRC, "app", "providers", "page.tsx"), "utf8");
const HEADER = readFileSync(join(SRC, "app", "Header.tsx"), "utf8");

const CRED = (over: Partial<ProviderCred> = {}): ProviderCred => ({
  id: "c1",
  provider: "anthropic",
  api_base: null,
  label: null,
  org_slug: null,
  scope: "platform",
  created_at: "2026-08-28T00:00:00Z",
  revoked_at: null,
  ...over,
});

describe("coverage — the number that decides whether AI works", () => {
  it("counts a live platform credential", () => {
    expect(armedProviders([CRED()])).toEqual(["anthropic"]);
  });

  it("does NOT count a revoked one", () => {
    // 🔴 A revoked row still renders in the table. Counting it would tell an
    // operator they are covered while every call fails.
    expect(armedProviders([CRED({ revoked_at: "2026-08-28T01:00:00Z" })])).toEqual([]);
  });

  it("does NOT count a BYOK one", () => {
    // 🔴 The sharper of the two. A key scoped to one org looks exactly like
    // coverage in a list, and leaves every OTHER tenant with no AI at all.
    expect(armedProviders([CRED({ org_slug: "acme", scope: "byok" })])).toEqual([]);
  });

  it("de-duplicates and sorts, so the line reads the same every time", () => {
    expect(
      armedProviders([CRED(), CRED({ id: "c2" }), CRED({ id: "c3", provider: "openai" })]),
    ).toEqual(["anthropic", "openai"]);
  });

  it("reports BYOK organizations separately", () => {
    expect(byokOrgs([CRED(), CRED({ id: "c2", org_slug: "acme" })])).toEqual(["acme"]);
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
    expect(coverageLine([CRED({ revoked_at: "2026-08-28T01:00:00Z" })]))
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
    expect(wouldRotate([CRED({ org_slug: "acme" })], "anthropic", null)).toBe(false);
  });

  it("ignores a revoked row — replacing nothing is not a rotation", () => {
    expect(wouldRotate([CRED({ revoked_at: "2026-08-28T01:00:00Z" })], "anthropic", null))
      .toBe(false);
  });

  it("says nothing on an empty provider field", () => {
    expect(wouldRotate([CRED()], "", null)).toBe(false);
  });
});

describe("row labels", () => {
  it("distinguishes platform from BYOK in words, not a code", () => {
    expect(describeScope(CRED())).toContain("everyone");
    expect(describeScope(CRED({ org_slug: "acme" }))).toContain("acme");
  });

  it("reads org_slug, not the scope string", () => {
    // `scope` is the Console's derived word. `org_slug` is the column the
    // Router keys on, so a disagreement between them must resolve to the row.
    expect(isPlatform(CRED({ org_slug: "acme", scope: "platform" }))).toBe(false);
    expect(isLive(CRED({ revoked_at: null }))).toBe(true);
  });
});

describe("the secret never crosses a read path", () => {
  it("the component runs no GET", () => {
    // ⚠️ The list is read by the server component with the caller's own
    // token. A client fetch would put it on a path the browser can replay.
    expect(ADMIN).not.toContain('method: "GET"');
    expect(ADMIN).not.toContain("/api/operator/providers?");
  });

  it("the secret field is a password input and clears on success", () => {
    expect(ADMIN).toContain('type="password"');
    expect(ADMIN).toContain('setSecret("")');
    expect(ADMIN).toContain('autoComplete="off"');
  });

  it("the page reads server-side and surfaces a failure", () => {
    expect(PAGE).toContain("listProviderCreds");
    expect(PAGE).toContain("The Console answered");
  });

  it("relays a refusal VERBATIM", () => {
    // The Console owns the provider-shape, whitespace and length rules. A
    // paraphrase here would be a second vocabulary for one 400.
    expect(ADMIN).toContain("The Console refused:");
  });
});

describe("the surface", () => {
  it("is reachable from the top bar", () => {
    expect(HEADER).toContain('{ href: "/providers", label: "Providers" }');
  });

  it("confirms before revoking, and says what revoking costs", () => {
    // 🔴 Revoking the platform credential stops every non-BYOK AI call.
    expect(ADMIN).toContain("confirm(");
    expect(ADMIN).toContain("not BYOK");
  });
});
