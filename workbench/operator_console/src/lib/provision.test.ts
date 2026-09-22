// The minted organization key, read out of a provision response.
//
// 🔴 **A customer with no `cc_live_` key cannot be served AT ALL**, and
// `hathi-labs-llp` ran for weeks that way because minting was a separate act
// somebody had to remember. Provisioning mints it now, and the secret exists
// in exactly one place: the create response. If this parse is wrong, the key
// is gone and the customer is dark.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { mintedKeyFrom } from "./provision";

const SRC = join(__dirname, "..");
const COMPONENT = readFileSync(join(SRC, "app", "NewCustomer.tsx"), "utf8");

const KEY = { prefix: "cc_live_abc123", token: "cc_live_abc123_thesecret" };

describe("mintedKeyFrom", () => {
  it("reads the key a fresh create returns", () => {
    const body = JSON.stringify({ organization_id: "x", slug: "s", key: KEY });
    expect(mintedKeyFrom(body)).toEqual(KEY);
  });

  it("⚠️ answers null for a RE-create, which carries no key and is not an error", () => {
    // The Console guards the mint on a live key already existing — that guard
    // is what stops a retried form issuing a pile of credentials. The caller
    // must render the success panel with no key section, never a failure.
    expect(mintedKeyFrom(JSON.stringify({ organization_id: "x", slug: "s" }))).toBe(
      null,
    );
  });

  it("does not throw on a body that is not JSON at all", () => {
    // ⚠️ submit() renders a success panel around this. An exception here would
    // replace a customer that WAS created with a blank screen.
    expect(mintedKeyFrom("<html>502 Bad Gateway</html>")).toBe(null);
    expect(mintedKeyFrom("")).toBe(null);
  });

  it("treats HALF a key as no key", () => {
    // A token with no prefix cannot be named in the key list, the revoke call
    // or the audit trail. Showing it would invite the operator to save a
    // credential they can never revoke.
    expect(mintedKeyFrom(JSON.stringify({ key: { token: KEY.token } }))).toBe(null);
    expect(mintedKeyFrom(JSON.stringify({ key: { prefix: KEY.prefix } }))).toBe(null);
  });

  it("rejects an EMPTY string half, which JSON.parse is happy with", () => {
    expect(mintedKeyFrom(JSON.stringify({ key: { prefix: "", token: "t" } }))).toBe(
      null,
    );
    expect(mintedKeyFrom(JSON.stringify({ key: { prefix: "p", token: "" } }))).toBe(
      null,
    );
  });

  it("rejects a non-object body and a null key", () => {
    expect(mintedKeyFrom("null")).toBe(null);
    expect(mintedKeyFrom("42")).toBe(null);
    expect(mintedKeyFrom(JSON.stringify({ key: null }))).toBe(null);
    expect(mintedKeyFrom(JSON.stringify({ key: "cc_live_x" }))).toBe(null);
  });
});

describe("the create screen SHOWS the key it was handed", () => {
  it("renders the token", () => {
    // Parsing it and never drawing it is the same outcome as not parsing it.
    expect(COMPONENT).toContain("{minted.token}");
  });

  it("warns that it is shown once", () => {
    // Only the hash is stored. An operator who navigates away without copying
    // has to revoke and mint again, so the screen has to say so BEFORE they do.
    expect(COMPONENT).toContain("shown once and never again");
  });

  it("⚠️ uses the SAME banner + token shape as the customer key panel", () => {
    // One look, one vocabulary (DESIGN_SYSTEM.md). A second secret-display
    // style would be a parallel seam for an act that already has one.
    const KEYS = readFileSync(
      join(SRC, "app", "customers", "[slug]", "Actions.tsx"),
      "utf8",
    );
    for (const cls of ['className="banner danger"', 'className="token"']) {
      expect(KEYS, "the pattern this copies moved").toContain(cls);
      expect(COMPONENT, "the create screen invented its own").toContain(cls);
    }
  });

  it("names where the key has to GO", () => {
    // A secret with no instruction is a secret that gets pasted nowhere. The
    // box reads it from one env var.
    expect(COMPONENT).toContain("CUSTOMER_CONSOLE_ORG_KEY");
  });
});
