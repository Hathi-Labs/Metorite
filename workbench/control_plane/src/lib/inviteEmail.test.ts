/**
 * WS-30 SC-2c — the invite notification builder.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2c done-whens 2 and 6.
 *
 * The clause that needs a real fence rather than a reading is **"no token, no
 * query-string secret"** (D49.1). It is asserted as a SCAN of the rendered
 * message — every URL in the text and the HTML must be the bare sign-in URL,
 * with no query string and no path segment beyond `/signin` — rather than as a
 * spot check for the word "token", because the failure mode is somebody adding
 * `?invite=<uuid>` and a spot check agreeing with them.
 */
import { describe, expect, it } from "vitest";

import { EMAIL_OTP_FROM_DEFAULT, type ResendSendArgs } from "@/lib/emailOtp";
import {
  INVITE_SIGNIN_FALLBACK_ORIGIN,
  type InviteEmailEnv,
  inviteEmail,
  inviteSignInUrl,
  isInviteEmailConfigured,
  sendInviteEmail,
} from "@/lib/inviteEmail";

const LIT: InviteEmailEnv = {
  MEMBER_INVITE_EMAIL_ENABLED: "true",
  RESEND_API_KEY: "re_test",
};

function collect(): { sent: ResendSendArgs[]; send: (a: ResendSendArgs) => Promise<void> } {
  const sent: ResendSendArgs[] = [];
  return {
    sent,
    send: async (a) => {
      sent.push(a);
    },
  };
}

describe("isInviteEmailConfigured", () => {
  it("is OFF with nothing set", () => {
    expect(isInviteEmailConfigured({})).toBe(false);
  });

  it("is OFF with the flag alone — there would be nothing to send through", () => {
    expect(
      isInviteEmailConfigured({ MEMBER_INVITE_EMAIL_ENABLED: "true" }),
    ).toBe(false);
  });

  it("is OFF with the key alone", () => {
    expect(isInviteEmailConfigured({ RESEND_API_KEY: "re_test" })).toBe(false);
  });

  it.each(["false", "TRUE", "1", "yes", " true "])(
    "compares to the exact string, not truthiness (%j)",
    (flag) => {
      // The `isEmailOtpConfigured` idiom: an operator who writes
      // `MEMBER_INVITE_EMAIL_ENABLED=false` while debugging must get OFF, and a
      // truthiness check would give them ON.
      expect(
        isInviteEmailConfigured({
          MEMBER_INVITE_EMAIL_ENABLED: flag,
          RESEND_API_KEY: "re_test",
        }),
      ).toBe(false);
    },
  );

  it("is ON only with the exact flag AND the key", () => {
    expect(isInviteEmailConfigured(LIT)).toBe(true);
  });
});

describe("inviteSignInUrl", () => {
  it("uses the deployment's own public origin", () => {
    expect(
      inviteSignInUrl({ WORKBENCH_PUBLIC_URL: "https://app.example.com" }),
    ).toBe("https://app.example.com/signin");
  });

  it("tolerates a trailing slash", () => {
    expect(
      inviteSignInUrl({ WORKBENCH_PUBLIC_URL: "https://app.example.com/" }),
    ).toBe("https://app.example.com/signin");
  });

  it("falls back rather than mailing a relative link", () => {
    expect(inviteSignInUrl({})).toBe(
      `${INVITE_SIGNIN_FALLBACK_ORIGIN}/signin`,
    );
  });
});

describe("the message body", () => {
  const built = inviteEmail("Fracktal Works", "https://app.example.com/signin");

  it("names the organization in the subject and both bodies", () => {
    expect(built.subject).toContain("Fracktal Works");
    expect(built.text).toContain("Fracktal Works");
    expect(built.html).toContain("Fracktal Works");
  });

  it("carries the sign-in link", () => {
    expect(built.text).toContain("https://app.example.com/signin");
    expect(built.html).toContain('href="https://app.example.com/signin"');
  });

  it("carries NO token and NO query string — D49.1, scanned not spot-checked", () => {
    const urls = [
      ...`${built.text} ${built.html}`.matchAll(/https?:\/\/[^\s"'<>)]+/g),
    ].map((m) => m[0]);
    expect(urls.length).toBeGreaterThan(0);
    for (const url of urls) {
      expect(url, `${url} carries a query string`).not.toContain("?");
      expect(url, `${url} carries a fragment`).not.toContain("#");
      expect(new URL(url).pathname).toBe("/signin");
    }
  });

  it("writes no colour — an email renders outside the theme system", () => {
    expect(built.html).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(built.html).not.toMatch(/\b(rgb|hsl)a?\(/);
  });

  it("escapes the organization name, which is customer-chosen", () => {
    const nasty = inviteEmail('<img src=x onerror="alert(1)">', "https://a/signin");
    expect(nasty.html).not.toContain("<img");
    expect(nasty.html).toContain("&lt;img");
  });
});

describe("sendInviteEmail", () => {
  it("sends exactly ONE message, to the invitee alone", async () => {
    const { sent, send } = collect();
    await sendInviteEmail(send, {
      to: "new@customer.example",
      orgName: "Fracktal Works",
      env: { ...LIT, WORKBENCH_PUBLIC_URL: "https://app.example.com" },
    });
    expect(sent).toHaveLength(1);
    expect(sent[0].to).toBe("new@customer.example");
  });

  it("sends from `emailOtpFrom(env)` — one verified sender, not a second", async () => {
    const { sent, send } = collect();
    await sendInviteEmail(send, {
      to: "new@customer.example",
      orgName: "Org",
      env: LIT,
    });
    expect(sent[0].from).toBe(EMAIL_OTP_FROM_DEFAULT);
  });

  it("honours a deployment's own EMAIL_OTP_FROM override", async () => {
    const { sent, send } = collect();
    await sendInviteEmail(send, {
      to: "new@customer.example",
      orgName: "Org",
      env: { ...LIT, EMAIL_OTP_FROM: "Acme <no-reply@acme.test>" },
    });
    expect(sent[0].from).toBe("Acme <no-reply@acme.test>");
  });

  it("propagates a transport failure rather than swallowing it", async () => {
    // The route turns this into `email_sent: false`; a builder that swallowed
    // it would report every send as successful.
    await expect(
      sendInviteEmail(
        async () => {
          throw new Error("Resend send failed (422): bad sender");
        },
        { to: "a@b.example", orgName: "Org", env: LIT },
      ),
    ).rejects.toThrow(/Resend send failed/);
  });
});

describe("the transport is imported, never re-implemented", () => {
  it("this module builds no Authorization header of its own", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const src = readFileSync(
      fileURLToPath(new URL("./inviteEmail.ts", import.meta.url)),
      "utf8",
    );
    // A second Resend transport would be root CLAUDE.md §5's defect by name,
    // and a second bearer mint is what `gateway.test.ts`'s allow-list refuses.
    expect(src).not.toMatch(/Authorization:\s*`Bearer/);
    expect(src).not.toContain("api.resend.com");
    expect(src).toMatch(/from "@\/lib\/emailOtp"/);
  });
});
