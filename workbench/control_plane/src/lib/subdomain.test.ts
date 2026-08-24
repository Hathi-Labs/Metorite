/**
 * Fences for WS-29 **MT-1f slice 1** — per-tenant workspace hostnames.
 *
 * Spec: `project-docs/specs/saas_multitenancy.md` §11 MT-1f, slice-1 done-when
 * 1-8 (each clause names the fence below that holds it; R7).
 *
 * Two kinds of check live here and the split is deliberate:
 *
 * * **executable** — `slugFromHost` and the redirect decision are pure, so the
 *   whole matrix (flag × host × session × slug) is *driven*, not asserted
 *   about. That is the point of keeping the rules out of `proxy.ts`: vitest in
 *   this tree is node-env and `import("@/auth")` cannot load `next-auth`, so
 *   anything decided inside the proxy would have no runnable fence at all.
 * * **source scans** — the two claims that are about the SHAPE of the tree
 *   rather than a value: that `proxy.ts` is the only host reader, and that the
 *   browser never learns a gateway URL. Both carry a non-vacuity assertion,
 *   because a sweep whose file list silently empties is the classic way a scan
 *   like this rots.
 *
 * What no test in this tree can prove is that the proxy *behaves* — there is no
 * DOM, no browser and no e2e here — so the composition itself is pinned by
 * shape and the live behaviour is the reviewer's manual gate.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  RESERVED_LABELS,
  SLUG_RE,
  appHost,
  isWorkspaceEnabled,
  slugFromHost,
  workspaceHostSlug,
  workspaceRedirect,
} from "./subdomain";

const BASE = "metorite.com";

const SRC = fileURLToPath(new URL("../", import.meta.url));
const PROXY = readFileSync(fileURLToPath(new URL("../proxy.ts", import.meta.url)), "utf-8");

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const FILES = sourceFiles(SRC).map((path) => ({
  rel: path.slice(SRC.length).replace(/\\/g, "/"),
  src: readFileSync(path, "utf-8"),
}));

// ══ done-when 1 · the host parser ═══════════════════════════════════════════

describe("slugFromHost — the workspace label, or null (done-when 1)", () => {
  const WORKSPACES: Array<[string, string]> = [
    ["acme.metorite.com", "acme"],
    // A Host header legitimately carries the port, and case is not part of a
    // hostname. Both are folded rather than rejected.
    ["ACME.Metorite.COM", "acme"],
    ["acme.metorite.com:3001", "acme"],
    // The fully-qualified form with the root label is the same host.
    ["acme.metorite.com.", "acme"],
    ["a.metorite.com", "a"],
    ["big-co-2.metorite.com", "big-co-2"],
    [`${"a".repeat(63)}.metorite.com`, "a".repeat(63)],
  ];

  for (const [host, slug] of WORKSPACES) {
    it(`reads ${host} as the workspace "${slug}"`, () => {
      expect(slugFromHost(host, BASE)).toBe(slug);
    });
  }

  const NOT_WORKSPACES: Array<[string, string]> = [
    ["metorite.com", "the apex is not a workspace"],
    ["metorite.com:3001", "…nor is the apex on a port"],
    ["app.metorite.com", "the app host is where everybody is SENT"],
    ["api.metorite.com", "the gateway's own hostname"],
    ["www.metorite.com", "reserved — and registrable through the signup form until B7"],
    ["a.b.metorite.com", "depth != 1: a customer owns one label, not a tree"],
    [".metorite.com", "an empty label"],
    ["..metorite.com", "…and an empty label with company"],
    ["evil.com", "outside the base domain entirely"],
    ["evilmetorite.com", "a suffix match that is not a subdomain"],
    ["acme.metorite.com.evil.com", "the base domain in the MIDDLE of the name"],
    ["METORITE.COM", "the apex, shouted"],
    [`${"a".repeat(64)}.metorite.com`, "64 characters — one past the DNS label limit"],
    ["-acme.metorite.com", "a leading hyphen fails the slug shape"],
    ["acme-.metorite.com", "…and a trailing one"],
    ["ac_me.metorite.com", "an underscore is not DNS-label-safe"],
    ["[::1]", "an address literal is not a workspace name"],
    ["[::1]:3001", "…nor is one with a port"],
    ["", "an absent Host header"],
    ["   ", "a blank one"],
  ];

  for (const [host, why] of NOT_WORKSPACES) {
    it(`returns null for ${JSON.stringify(host)} — ${why}`, () => {
      expect(slugFromHost(host, BASE)).toBeNull();
    });
  }

  it("returns null for a null/undefined host rather than throwing", () => {
    expect(slugFromHost(null, BASE)).toBeNull();
    expect(slugFromHost(undefined, BASE)).toBeNull();
  });

  it("returns null when the base domain itself is missing", () => {
    // Fail closed: an unconfigured base domain must not turn every hostname
    // into a workspace name.
    expect(slugFromHost("acme.metorite.com", "")).toBeNull();
    expect(slugFromHost("acme.metorite.com", "   ")).toBeNull();
  });

  it("refuses EVERY reserved label, not a hand-picked few (owner ruling B7)", () => {
    // The list is data; the fence walks it, so adding a label without wiring it
    // cannot pass. `api` is the live-defect case: it is registrable through the
    // public signup form today and would collide with the gateway's own host.
    for (const label of RESERVED_LABELS) {
      expect(slugFromHost(`${label}.${BASE}`, BASE)).toBeNull();
    }
    expect(RESERVED_LABELS).toContain("api");
    expect(RESERVED_LABELS).toContain("app");
  });

  it("keeps the reserved list sorted and duplicate-free, so the parity fence is stable", () => {
    // `tests/unit/test_subdomain_host_vocabulary.py` compares this list to the
    // gateway's set. Order is not semantic, but an unsorted list is how a
    // duplicate hides.
    expect([...RESERVED_LABELS].sort()).toEqual([...RESERVED_LABELS]);
    expect(new Set(RESERVED_LABELS).size).toBe(RESERVED_LABELS.length);
  });

  it("SLUG_RE alone refuses a dotted or empty label — so the depth line is BELT-AND-BRACES", () => {
    // Measured 2026-08-24: deleting `label.includes(".")` from `slugFromHost`
    // kills no test, because the charset already excludes a dot. Rather than
    // pretend the line is the guard (R7 — a rule no test can fail on is
    // advisory), the REDUNDANCY is what gets pinned: widen the charset to admit
    // a dot and this reds, which is the moment the depth line stops being
    // decoration and starts being the only thing keeping `a.b` out.
    expect(SLUG_RE.test("a.b")).toBe(false);
    expect(SLUG_RE.test("")).toBe(false);
  });

  it("names the one host everybody is sent back to", () => {
    expect(appHost(BASE)).toBe("app.metorite.com");
    // …and it is itself never a workspace, or the redirect would loop.
    expect(slugFromHost(appHost(BASE), BASE)).toBeNull();
  });
});

// ══ done-when 3 · the flag, both positions ══════════════════════════════════

describe("the flag is an equality against \"true\" (done-when 3)", () => {
  it("arms on the exact string and nothing else", () => {
    expect(isWorkspaceEnabled("true")).toBe(true);
    for (const v of ["false", "TRUE", "True", "1", "yes", "", " true", undefined]) {
      // An operator who writes `=false` while debugging must get OFF, and every
      // truthy-string reading arms it instead (auth.ts:163's idiom).
      expect(isWorkspaceEnabled(v as string | undefined)).toBe(false);
    }
  });

  it("makes the host parser inert for BOTH hosts when off", () => {
    for (const host of ["app.metorite.com", "acme.metorite.com"]) {
      expect(workspaceHostSlug(undefined, host, BASE)).toBeNull();
      expect(workspaceHostSlug("false", host, BASE)).toBeNull();
    }
    // …and non-inert for a workspace host when on, so the check above is not
    // passing for the wrong reason.
    expect(workspaceHostSlug("true", "acme.metorite.com", BASE)).toBe("acme");
    expect(workspaceHostSlug("true", "app.metorite.com", BASE)).toBeNull();
  });
});

// ══ done-when 3-6 · the decision matrix, driven end to end ══════════════════

/**
 * `proxy.ts`'s composition, replayed: parse the host under the flag, then
 * decide. Kept identical in shape to the proxy so the table below is a
 * statement about the shipped path and not about a convenient rearrangement.
 */
function decide(args: {
  flag?: string;
  host: string;
  signedIn: boolean;
  callerSlug?: string | null;
  path?: string;
}): string | null {
  const hostSlug = workspaceHostSlug(args.flag, args.host, BASE);
  return workspaceRedirect({
    hostSlug,
    signedIn: args.signedIn,
    callerSlug: args.signedIn ? (args.callerSlug ?? null) : null,
    baseDomain: BASE,
    pathWithQuery: args.path ?? "/projects?view=board",
  });
}

describe("the workspace decision (done-when 3, 4, 5, 6)", () => {
  it("flag OFF: byte-identical for app.metorite.com AND acme.metorite.com", () => {
    for (const host of ["app.metorite.com", "acme.metorite.com"]) {
      expect(decide({ host, signedIn: true, callerSlug: "other" })).toBeNull();
      expect(decide({ host, signedIn: false })).toBeNull();
      expect(decide({ flag: "false", host, signedIn: true, callerSlug: "other" })).toBeNull();
    }
  });

  it("flag ON, the apex app host: unchanged — it is not a workspace", () => {
    expect(
      decide({ flag: "true", host: "app.metorite.com", signedIn: true, callerSlug: "acme" }),
    ).toBeNull();
  });

  it("flag ON + signed in + the slug MATCHES: pass through unchanged (4)", () => {
    expect(
      decide({ flag: "true", host: "acme.metorite.com", signedIn: true, callerSlug: "acme" }),
    ).toBeNull();
  });

  it("flag ON + signed in + the slug DIFFERS: 302 to the apex, carrying the path (5)", () => {
    expect(
      decide({
        flag: "true",
        host: "acme.metorite.com",
        signedIn: true,
        callerSlug: "globex",
        path: "/projects?view=board",
      }),
    ).toBe("https://app.metorite.com/projects?view=board");
  });

  it("flag ON + signed in + an UNRESOLVABLE org: treated as a mismatch (5)", () => {
    // Failing towards the neutral apex discloses nothing; failing towards
    // "serve the workspace host" would serve a tenant hostname to somebody we
    // could not place.
    expect(
      decide({ flag: "true", host: "acme.metorite.com", signedIn: true, callerSlug: null }),
    ).toBe("https://app.metorite.com/projects?view=board");
  });

  it("the redirect NAMES NO ORGANIZATION — neither the host's nor the caller's (5)", () => {
    const location = decide({
      flag: "true",
      host: "acme.metorite.com",
      signedIn: true,
      callerSlug: "globex",
      path: "/settings",
    });
    expect(location).toBe("https://app.metorite.com/settings");
    expect(location).not.toContain("acme");
    expect(location).not.toContain("globex");
  });

  it("flag ON + signed OUT: identical for a real and an invented slug (6, ruling B4)", () => {
    // The shape of `test_the_subdomain_cases_are_indistinguishable`: the two
    // answers are compared to EACH OTHER, not each to a constant, so a future
    // change that starts distinguishing them fails here even if both stay
    // "falsy". Nothing was looked up on this path — that is what makes the
    // indistinguishability structural rather than a coincidence of two lookups
    // returning the same thing.
    const existing = decide({ flag: "true", host: "acme.metorite.com", signedIn: false });
    const invented = decide({
      flag: "true",
      host: "no-such-tenant-anywhere.metorite.com",
      signedIn: false,
    });
    expect(existing).toEqual(invented);
    expect(existing).toBeNull();
  });

  it("a signed-out caller's slug is never consulted even if one is handed over", () => {
    // Guards the ORDER inside `workspaceRedirect`: the signed-out return sits
    // before the comparison, so a caller slug that leaked in cannot change the
    // answer for anybody.
    expect(
      workspaceRedirect({
        hostSlug: "acme",
        signedIn: false,
        callerSlug: "globex",
        baseDomain: BASE,
        pathWithQuery: "/",
      }),
    ).toBeNull();
  });
});

// ══ done-when 2 · one host reader ═══════════════════════════════════════════

describe("proxy.ts is the ONLY host reader in this tier (done-when 2)", () => {
  const HOST_READS = [
    /\.get\(\s*["'`]host["'`]\s*\)/i,
    /nextUrl\.host\b/,
    /["'`]x-forwarded-host["'`]/i,
  ];

  it("sweeps a real file list, so the scan cannot pass by finding nothing", () => {
    expect(FILES.length).toBeGreaterThan(80);
    // Non-vacuity from the other side: the one legitimate reader must still be
    // in the tree, or the scan below is asserting the absence of a thing that
    // no longer exists anywhere.
    expect(HOST_READS.some((re) => re.test(PROXY))).toBe(true);
  });

  it("finds no second reader outside proxy.ts", () => {
    // A hostname is request input. Read in two places it becomes two opinions
    // about which tenant you are looking at, and the losing one is a
    // cross-tenant surface. A new reader belongs in proxy.ts or nowhere.
    const offenders = FILES.filter(
      (f) =>
        f.rel !== "proxy.ts" &&
        f.rel !== "lib/subdomain.test.ts" &&
        HOST_READS.some((re) => re.test(f.src)),
    ).map((f) => f.rel);
    expect(offenders).toEqual([]);
  });
});

// ══ done-when 8 · no browser-side gateway call ══════════════════════════════

describe("the browser never learns a gateway URL (done-when 8)", () => {
  it("no module reads a NEXT_PUBLIC_GATEWAY* variable", () => {
    // A top-level navigation or a browser fetch to `api.metorite.com` carries
    // no Bearer and no `X-User-Email` — the six-day mailbox-connect outage
    // (`workbench/AGENTS.md`). Under MT-1f it would also mean the browser
    // choosing which host answers for a tenant, which is R11 wearing a URL.
    const offenders = FILES.filter(
      (f) =>
        f.rel !== "lib/subdomain.test.ts" &&
        /process\.env\.NEXT_PUBLIC_GATEWAY/.test(f.src),
    ).map((f) => f.rel);
    expect(offenders).toEqual([]);
  });
});

// ══ the wiring, pinned by shape (nothing here can execute proxy.ts) ═════════

describe("proxy.ts consumes the pure module rather than re-deciding", () => {
  // ⚠️ Only the SEAM is pinned here. Everything about how the proxy BEHAVES —
  // the 302, the ordering against PUBLIC_PAGES and the auth passthroughs, and
  // the "no lookup when signed out" guard — is executed in
  // `proxyWorkspace.test.ts`, which drives `proxy()` itself. A source pin for
  // any of those would be a weaker statement of a thing already measured, and
  // would go stale against a harmless refactor.
  it("imports both halves and holds no local copy of the rules", () => {
    expect(PROXY).toMatch(
      /import \{ workspaceHostSlug, workspaceRedirect \} from "@\/lib\/subdomain"/,
    );
    // The reserved set, the slug shape and the apex host belong to the pure
    // module; a literal here would be the second vocabulary rule 4 forbids.
    expect(PROXY).not.toContain('"app.metorite.com"');
    expect(PROXY).not.toMatch(/\[a-z0-9\]\(\[a-z0-9-\]/);
    expect(PROXY).not.toMatch(/RESERVED|"api",/);
  });

  it("reaches the gateway through the ONE bearer seam, never its own", () => {
    // `gateway.test.ts` sweeps this file for `GATEWAY_INTERNAL_TOKEN` too; the
    // positive half — that it goes through `headersActingAs` — is stated here,
    // beside the module it belongs to.
    expect(PROXY).toContain("headersActingAs(email)");
    expect(PROXY).not.toContain("GATEWAY_INTERNAL_TOKEN");
  });
});
