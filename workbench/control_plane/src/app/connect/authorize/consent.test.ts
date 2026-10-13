// The consent page's decisions (WS-55 EA-4a, spec §10.5).
//
// The load-bearing rule: the approval sends the SAME request that `check`
// saw, because the consent ticket binds six of its fields. And a member can
// narrow the scopes, never widen them.
import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import {
  OFFLINE_ACCESS,
  approveBody,
  canApprove,
  grantedScopes,
  initialChoice,
  isCheckView,
  organizationName,
  parseAuthorizeRequest,
  pinChoices,
  refusalFor,
  scopeRows,
  type CheckView,
} from "./consent";

const REQUEST = {
  client_id: "https://claude.ai/oauth/claude-code-client-metadata",
  redirect_uri: "http://localhost:53122/callback",
  response_type: "code",
  code_challenge: "c".repeat(43),
  code_challenge_method: "S256",
  resource: "https://mcp.metorite.com/mcp",
  scope: "projects:read projects:write offline_access",
  state: "st-1",
};

const VIEW: CheckView = {
  client: { client_id: REQUEST.client_id, name: "Claude Code", origin: "https://claude.ai" },
  redirect_uri: REQUEST.redirect_uri,
  redirect_display: "http://localhost:53122",
  redirect_app: false,
  consent_ticket: "t".repeat(43),
  scopes: ["projects:read", "projects:write", OFFLINE_ACCESS],
  resource: REQUEST.resource,
  state: "st-1",
  issuer: "https://mcp.metorite.com",
  deny_redirect_to: "http://localhost:53122/callback?error=access_denied&state=st-1",
};

describe("parseAuthorizeRequest", () => {
  it("keeps the eight OAuth keys and drops any other", () => {
    const parsed = parseAuthorizeRequest({ ...REQUEST, organization_id: "x", extra: "y" });
    expect(parsed).toEqual({ ok: true, request: REQUEST });
  });

  it("refuses a key sent twice", () => {
    const parsed = parseAuthorizeRequest({ ...REQUEST, state: ["a", "b"] });
    expect(parsed.ok).toBe(false);
  });

  it("refuses a request with no client_id or no redirect_uri", () => {
    expect(parseAuthorizeRequest({ redirect_uri: "x" }).ok).toBe(false);
    expect(parseAuthorizeRequest({ client_id: "x" }).ok).toBe(false);
  });

  it("leaves out a key the agent did not send", () => {
    const parsed = parseAuthorizeRequest({ client_id: "a", redirect_uri: "b" });
    expect(parsed).toEqual({ ok: true, request: { client_id: "a", redirect_uri: "b" } });
  });
});

describe("the scopes", () => {
  it("offline_access is fixed, and the others have a box", () => {
    expect(scopeRows(VIEW.scopes).map((r) => [r.scope, r.fixed])).toEqual([
      ["projects:read", false],
      ["projects:write", false],
      [OFFLINE_ACCESS, true],
    ]);
  });

  it("a new screen chooses every requested scope", () => {
    expect([...initialChoice(VIEW)]).toEqual(["projects:read", "projects:write"]);
  });

  it("narrowing drops a scope, and offline_access stays", () => {
    expect(grantedScopes(VIEW, new Set(["projects:read"]))).toEqual(["projects:read", OFFLINE_ACCESS]);
  });

  it("a choice can never add a scope the agent did not ask for", () => {
    expect(grantedScopes(VIEW, new Set(["projects:read", "context:read"]))).toEqual([
      "projects:read",
      OFFLINE_ACCESS,
    ]);
  });

  it("approve needs one resource scope, and offline_access alone is not one", () => {
    expect(canApprove(VIEW, new Set(["projects:read"]))).toBe(true);
    expect(canApprove(VIEW, new Set())).toBe(false);
  });
});

describe("approveBody", () => {
  it("sends the same request, the ticket and the choices, and nothing else", () => {
    const body = approveBody(REQUEST, VIEW, {
      scopes: new Set(["projects:read"]),
      pins: ["p1"],
      includePersonal: false,
    });
    expect(body).toEqual({
      ...REQUEST,
      consent_ticket: VIEW.consent_ticket,
      scopes: ["projects:read", OFFLINE_ACCESS],
      pinned_projects: ["p1"],
      include_personal: false,
    });
  });
});

describe("pinChoices", () => {
  const tree = {
    rows: [
      { id: "a", name: "Engineering", children: [{ id: "a1", name: "Platform", children: [] }] },
      { id: "b", name: "Old", archived_at: "2026-01-01", children: [{ id: "b1", name: "Hidden" }] },
      { id: "c", name: "Sales" },
      { id: 7, name: "Bad id" },
    ],
  };

  it("lists the tree in order with depth, and leaves out an archived subtree", () => {
    expect(pinChoices(tree)).toEqual([
      { id: "a", name: "Engineering", depth: 0 },
      { id: "a1", name: "Platform", depth: 1 },
      { id: "c", name: "Sales", depth: 0 },
    ]);
  });

  it("lists at most 50", () => {
    const many = { rows: Array.from({ length: 80 }, (_, i) => ({ id: `p${i}`, name: `P${i}` })) };
    expect(pinChoices(many)).toHaveLength(50);
  });

  it("reads a shape it does not know as no projects", () => {
    expect(pinChoices(null)).toEqual([]);
    expect(pinChoices({ rows: "x" })).toEqual([]);
  });
});

describe("the rest", () => {
  it("isCheckView accepts the gateway's answer and refuses a partial one", () => {
    expect(isCheckView(VIEW)).toBe(true);
    expect(isCheckView({ ...VIEW, consent_ticket: undefined })).toBe(false);
    expect(isCheckView(null)).toBe(false);
  });

  it("names the organization, or nothing", () => {
    expect(organizationName({ display_name: "Fracktal Works", slug: "fracktal" })).toBe("Fracktal Works");
    expect(organizationName({ slug: "fracktal" })).toBe("fracktal");
    expect(organizationName({})).toBeNull();
    expect(organizationName(undefined)).toBeNull();
  });

  it("a ticket refusal says to start again, with no way back", () => {
    const r = refusalFor(400, {
      detail: {
        error: "invalid_request",
        error_description: "The consent ticket is missing or not valid.",
        redirect_to: null,
      },
    });
    expect(r.message).toMatch(/expired or was already used/);
    expect(r.redirectTo).toBeNull();
  });

  it("a verified refusal offers the agent's own error redirect", () => {
    const r = refusalFor(400, {
      detail: {
        error: "invalid_target",
        error_description: "resource must name this server's MCP endpoint.",
        redirect_to: "http://localhost:5/cb?error=invalid_target",
      },
    });
    expect(r.redirectTo).toBe("http://localhost:5/cb?error=invalid_target");
  });

  it("every icon the screen draws exists", () => {
    for (const name of ["Bot", "ShieldCheck", "Lock", "AlertCircle", "ArrowLeft", "ArrowUpRight", "Check"]) {
      expect(isKnownIcon(name), name).toBe(true);
    }
  });
});
