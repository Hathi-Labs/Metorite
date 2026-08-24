/**
 * WS-30 SC-2c — the invite hop: `POST /api/admin/members/invite`.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2c done-whens 1, 3, 4,
 * 5, 6 · `work_plan.md` §3 **D49** · R11.
 *
 * The `manage.test.ts` / `checkout.test.ts` idiom: mock `@/auth`, stub the
 * gateway `fetch`, invoke the real handler through its `@/` specifier. The
 * Resend transport is stubbed at the same `fetch`, so **"zero transport calls"
 * is observable** rather than argued — the dark-default clause is the one this
 * file exists for and it is the one a reader would otherwise take on trust.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const session = vi.hoisted(() => ({ email: null as string | null }));

vi.mock("@/auth", () => ({
  auth: async () => (session.email ? { user: { email: session.email } } : null),
  isAuthEnabled: true,
}));

const GATEWAY_URL = "http://127.0.0.1:8000";
const RESEND = "https://api.resend.com/emails";

/** Every outbound request the stub saw, in order. */
let calls: { url: string; init?: RequestInit }[] = [];

type Plan = {
  /** Status for the gateway `POST /admin/members` hop. */
  inviteStatus?: number;
  inviteBody?: unknown;
  /** Status for the Resend send. */
  resendStatus?: number;
  /** Body for `/auth/me`. */
  me?: unknown;
};

function stubFetch(plan: Plan = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, init });
      if (url.startsWith(RESEND)) {
        return new Response("{}", { status: plan.resendStatus ?? 200 });
      }
      if (url.endsWith("/auth/me")) {
        return new Response(
          JSON.stringify(
            plan.me ?? {
              organization: { slug: "fracktal", display_name: "Fracktal Works" },
            },
          ),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify(
          plan.inviteBody ?? { email: "new@customer.example", status: "invited" },
        ),
        {
          status: plan.inviteStatus ?? 200,
          headers: { "content-type": "application/json" },
        },
      );
    }),
  );
}

const inviteCalls = () =>
  calls.filter((c) => c.url === `${GATEWAY_URL}/admin/members`);
const sends = () => calls.filter((c) => c.url.startsWith(RESEND));

async function invoke(body: Record<string, unknown>) {
  const { NextRequest } = await import("next/server");
  const { POST } = await import("@/app/api/admin/members/invite/route");
  return POST(
    new NextRequest("http://localhost:3001/api/admin/members/invite", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "content-type": "application/json" },
    }),
  );
}

const VALID = { email: "new@customer.example", display_name: "Ada", roles: ["member"] };

beforeEach(() => {
  calls = [];
  session.email = "admin@customer.example";
  vi.unstubAllEnvs();
  vi.stubEnv("GATEWAY_INTERNAL_TOKEN", "internal");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

// ── done-when 1 · the dark default ──────────────────────────────────────────

describe("with the flag unset (the shipped default)", () => {
  it("forwards the invite and makes ZERO transport calls", async () => {
    stubFetch();
    const res = await invoke(VALID);
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ invited: true, email_sent: false, email_channel: "disabled" });
    expect(sends()).toHaveLength(0);
    // And exactly ONE gateway call: the `/auth/me` org read lives inside the
    // lit arm, so a dark box behaves as it did before this route existed.
    expect(calls).toHaveLength(1);
    expect(inviteCalls()).toHaveLength(1);
  });

  it.each(["false", "TRUE", "1"])(
    "stays dark for a flag that is not exactly \"true\" (%j)",
    async (flag) => {
      vi.stubEnv("MEMBER_INVITE_EMAIL_ENABLED", flag);
      vi.stubEnv("RESEND_API_KEY", "re_test");
      stubFetch();
      await invoke(VALID);
      expect(sends()).toHaveLength(0);
    },
  );

  it("stays dark with the flag on but no key", async () => {
    vi.stubEnv("MEMBER_INVITE_EMAIL_ENABLED", "true");
    stubFetch();
    await invoke(VALID);
    expect(sends()).toHaveLength(0);
  });
});

// ── done-when 2 · the lit path ──────────────────────────────────────────────

describe("with the flag and the key set", () => {
  beforeEach(() => {
    vi.stubEnv("MEMBER_INVITE_EMAIL_ENABLED", "true");
    vi.stubEnv("RESEND_API_KEY", "re_test");
    vi.stubEnv("WORKBENCH_PUBLIC_URL", "https://app.example.com");
  });

  it("sends exactly ONE message, to the invited address alone", async () => {
    stubFetch();
    const res = await invoke(VALID);
    expect(await res.json()).toMatchObject({ invited: true, email_sent: true, email_channel: "sent" });
    expect(sends()).toHaveLength(1);
    const body = JSON.parse(String(sends()[0].init?.body ?? "{}"));
    expect(body.to).toBe("new@customer.example");
    // Never the acting admin, and never a second recipient.
    expect(JSON.stringify(body)).not.toContain("admin@customer.example");
  });

  it("carries the org name and the sign-in link, and no token", async () => {
    stubFetch();
    await invoke(VALID);
    const body = JSON.parse(String(sends()[0].init?.body ?? "{}"));
    expect(body.subject).toContain("Fracktal Works");
    expect(body.text).toContain("https://app.example.com/signin");
    const urls = [
      ...`${body.text} ${body.html}`.matchAll(/https?:\/\/[^\s"'<>)]+/g),
    ].map((m) => m[0]);
    for (const url of urls) expect(url).not.toContain("?");
  });

  it("takes the org name from the GATEWAY, never from the request body", async () => {
    stubFetch();
    await invoke({ ...VALID, org: "Attacker Inc", organization_id: "x" });
    const body = JSON.parse(String(sends()[0].init?.body ?? "{}"));
    // An org name from the browser would let an authenticated admin put
    // arbitrary text in a message sent from our own verified sender.
    expect(body.subject).toContain("Fracktal Works");
    expect(JSON.stringify(body)).not.toContain("Attacker Inc");
  });

  it("does not send when the organization cannot be established", async () => {
    // Inventing a name for the one thing the email is about is worse than not
    // sending it — the membership is written either way.
    stubFetch({ me: {} });
    const res = await invoke(VALID);
    // The deployment IS armed and no mail went out — "failed", not "disabled",
    // so the surface tells the admin to notify the colleague themselves.
    expect(await res.json()).toMatchObject({
      invited: true, email_sent: false, email_channel: "failed",
    });
    expect(sends()).toHaveLength(0);
  });
});

// ── done-when 3 · a refused invite mails nobody ─────────────────────────────

describe("when the gateway refuses", () => {
  beforeEach(() => {
    vi.stubEnv("MEMBER_INVITE_EMAIL_ENABLED", "true");
    vi.stubEnv("RESEND_API_KEY", "re_test");
  });

  it.each([400, 403, 409, 502])("mails nobody on %i", async (status) => {
    stubFetch({ inviteStatus: status, inviteBody: { detail: "no" } });
    const res = await invoke(VALID);
    expect(res.status).toBe(status);
    expect(sends()).toHaveLength(0);
    // The refusal body is relayed verbatim, so the dialog can render the
    // gateway's own reason.
    expect(await res.json()).toEqual({ detail: "no" });
  });

  it("401s a signed-out caller and reaches nothing", async () => {
    session.email = null;
    stubFetch();
    const res = await invoke(VALID);
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });
});

// ── done-when 4 · a failed send never un-invites ────────────────────────────

describe("when the send fails", () => {
  it("answers 200 with email_sent:false, once, and does not retry", async () => {
    vi.stubEnv("MEMBER_INVITE_EMAIL_ENABLED", "true");
    vi.stubEnv("RESEND_API_KEY", "re_test");
    stubFetch({ resendStatus: 422 });

    const res = await invoke(VALID);
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({
      invited: true, email_sent: false, email_channel: "failed",
    });
    // One attempt, not two: the membership is already written on both planes
    // and cannot be repeated, so a retry risks a duplicate message for a write
    // that never happens twice.
    expect(sends()).toHaveLength(1);
    expect(inviteCalls()).toHaveLength(1);
  });
});

// ── done-when 5 · R11, the outbound body is rebuilt ─────────────────────────

describe("R11", () => {
  it("forwards only {email, display_name, roles}", async () => {
    stubFetch();
    await invoke({
      ...VALID,
      org: "other-co",
      organization_id: "00000000-0000-0000-0000-000000000000",
      actor_email: "ceo@victim.example",
      status: "active",
    });
    const sent = JSON.parse(String(inviteCalls()[0].init?.body ?? "{}"));
    expect(Object.keys(sent).sort()).toEqual(["display_name", "email", "roles"]);
    // The acting identity rides the session header, never the body.
    const headers = (inviteCalls()[0].init?.headers ?? {}) as Record<string, string>;
    expect(headers["X-User-Email"]).toBe("admin@customer.example");
  });

  it("cannot be made to invite as somebody else", async () => {
    stubFetch();
    await invoke({ ...VALID, actor_email: "ceo@victim.example" });
    const headers = (inviteCalls()[0].init?.headers ?? {}) as Record<string, string>;
    expect(headers["X-User-Email"]).toBe("admin@customer.example");
    expect(JSON.stringify(inviteCalls()[0].init?.body)).not.toContain(
      "ceo@victim.example",
    );
  });
});

// ── done-when 6 · no new credential in the route ────────────────────────────

describe("the route holds no credential of its own", () => {
  const src = readFileSync(
    fileURLToPath(new URL("./route.ts", import.meta.url)),
    "utf8",
  );

  it("mints no Authorization header", () => {
    // `gateway.test.ts`'s repo-wide allow-list is three names and must stay
    // three; this re-asserts it at source so a regression is local and named.
    expect(src).not.toMatch(/Authorization:\s*`Bearer/);
  });

  it("reads no Console or deployment credential", () => {
    expect(src).not.toContain("CUSTOMER_CONSOLE_ORG_KEY");
    expect(src).not.toContain("CUSTOMER_CONSOLE_DEPLOYMENT_KEY");
    expect(src).not.toContain("GATEWAY_INTERNAL_TOKEN");
  });

  it("does not special-case the catch-all proxy", async () => {
    const catchAll = readFileSync(
      fileURLToPath(new URL("../../[...path]/route.ts", import.meta.url)),
      "utf8",
    );
    // The whole reason this file exists is that the catch-all stays a proxy.
    expect(catchAll).not.toContain("invite");
    expect(catchAll).not.toContain("RESEND");
  });
});
