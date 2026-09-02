/**
 * The login page's RECOVERY NOTE — WS-31 CP-12g.
 *
 * Spec: `project-docs/specs/operator_identity_and_access.md` §8 · D64.
 *
 * ⚠️ **What is under test is text, never a second door.** `identity.test.ts`
 * pins the gate: while `OPERATOR_IDENTITY_ENABLED` is on, a passphrase cookie
 * is refused (done-when 29). This suite pins the other half — that the page
 * NAMES the env line which returns the console to the passphrase path, in the
 * two states that strand a reader, and that it renders no passphrase form
 * there.
 *
 * ⚠️ vitest here is node-env with no DOM, so nothing renders. The page is a
 * server component: calling it returns a React ELEMENT TREE, and these cases
 * walk that tree for its text. That is weaker than a browser and stronger than
 * a source scan — a note deleted from the JSX reds every case below.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  IDENTITY_FLAG,
  PROVIDER_LABELS,
  SIGNIN_PROVIDER_FLAG,
  signinProvider,
} from "@/lib/identity";
import {
  ANON_KEY_FLAG,
  DIRECTORY_SIGNIN_FLAG,
  EMAIL_OTP_FLAG,
} from "@/lib/otp";
import LoginPage from "./page";
import EmailCodeForm from "./EmailCodeForm";
import InterimForm from "./InterimForm";

const SUPABASE = "https://project.supabase.co";
const ORIGIN = "https://operator.metorite.com";

type Node = unknown;

//: Collect the visible text of a React element tree. `<code>` children come
//: back inline, so a flag named inside one is found by a plain substring test.
function text(node: Node): string {
  if (node === null || node === undefined || typeof node === "boolean") {
    return "";
  }
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(text).join("");
  }
  const el = node as { props?: { children?: Node }; type?: unknown };
  if (el.props && "children" in el.props) {
    return text(el.props.children);
  }
  return "";
}

//: The element types on the page, so a case can say "the interim FORM is what
//: rendered" without rendering a client component.
function types(node: Node, out: unknown[] = []): unknown[] {
  if (node === null || node === undefined || typeof node !== "object") {
    return out;
  }
  if (Array.isArray(node)) {
    node.forEach((child) => types(child, out));
    return out;
  }
  const el = node as { props?: { children?: Node }; type?: unknown };
  if (el.type !== undefined) out.push(el.type);
  if (el.props && "children" in el.props) types(el.props.children, out);
  return out;
}

//: Every `className` in the tree. A divider carries no text worth asserting
//: on, so its class is what identifies it.
function classes(node: Node, out: string[] = []): string[] {
  if (node === null || node === undefined || typeof node !== "object") {
    return out;
  }
  if (Array.isArray(node)) {
    node.forEach((child) => classes(child, out));
    return out;
  }
  const el = node as { props?: { children?: Node; className?: unknown } };
  if (typeof el.props?.className === "string") out.push(el.props.className);
  if (el.props && "children" in el.props) classes(el.props.children, out);
  return out;
}

//: The `href` of every anchor in the tree. The provider slug rides in a PROP,
//: not in text, so the text walker above cannot see it.
function hrefs(node: Node, out: string[] = []): string[] {
  if (node === null || node === undefined || typeof node !== "object") {
    return out;
  }
  if (Array.isArray(node)) {
    node.forEach((child) => hrefs(child, out));
    return out;
  }
  const el = node as { props?: { children?: Node; href?: unknown } };
  if (typeof el.props?.href === "string") out.push(el.props.href);
  if (el.props && "children" in el.props) hrefs(el.props.children, out);
  return out;
}

async function render(params: { origin?: string; error?: string } = {}) {
  return await LoginPage({ searchParams: Promise.resolve(params) });
}

const RECOVERY = "restart the console";

beforeEach(() => {
  vi.unstubAllEnvs();
  vi.stubEnv("OPERATOR_SUPABASE_URL", "");
  vi.stubEnv("OPERATOR_CONSOLE_ORIGIN", "");
  // Unset means `azure`, which is what the six cases below describe. Stubbed
  // rather than assumed, so a variable on the developer's box cannot change
  // what these cases are testing.
  vi.stubEnv(SIGNIN_PROVIDER_FLAG, "");
  // D71.3 ships dark too. Every case above this line describes a box with no
  // email fallback, and a variable on somebody's machine must not change that.
  vi.stubEnv(EMAIL_OTP_FLAG, "");
  vi.stubEnv(ANON_KEY_FLAG, "");
});

describe("flag ON, Supabase not configured", () => {
  beforeEach(() => {
    vi.stubEnv(IDENTITY_FLAG, "1");
  });

  it("shows the configuration banner AND names the flag to unset", async () => {
    const body = text(await render());
    expect(body).toContain("Sign-in is not configured on this deployment");
    expect(body).toContain("OPERATOR_SUPABASE_URL");
    // The recovery half. This is the state most likely to strand somebody:
    // there is no Microsoft button to press and no passphrase box either.
    expect(body).toContain(IDENTITY_FLAG);
    expect(body).toContain(RECOVERY);
    expect(body).toContain("H-56");
  });

  it("⚠️ renders NO passphrase form on this path", async () => {
    // A passphrase box here would 400 on submit: with the flag on, `POST
    // /api/operator/session` wants a Supabase `access_token` and reads no
    // `secret`. The note is the whole remedy, on purpose.
    const kinds = types(await render());
    expect(kinds).not.toContain(InterimForm);
    // No form of any kind: no `<form>`, no `<input>`, no submit button. The
    // note mentions the passphrase, so a text probe would pass on the note
    // itself — the element types are what tell a hint from a door.
    expect(kinds).not.toContain("form");
    expect(kinds).not.toContain("input");
    expect(kinds).not.toContain("button");
  });
});

describe("flag ON, Microsoft refused the sign-in", () => {
  beforeEach(() => {
    vi.stubEnv(IDENTITY_FLAG, "1");
    vi.stubEnv("OPERATOR_SUPABASE_URL", SUPABASE);
    vi.stubEnv("OPERATOR_CONSOLE_ORIGIN", ORIGIN);
  });

  it("shows the refusal AND names the flag to unset", async () => {
    const body = text(await render({ error: "operator not in the registry" }));
    expect(body).toContain("operator not in the registry");
    expect(body).toContain(IDENTITY_FLAG);
    expect(body).toContain(RECOVERY);
  });

  it("stays quiet while sign-in is configured and nothing failed", async () => {
    // The note is a recovery hint, not a standing instruction to turn the
    // new sign-in off. A working page must not advertise the way back.
    const body = text(await render());
    expect(body).toContain("Sign in with Microsoft");
    expect(body).not.toContain(IDENTITY_FLAG);
  });

  it("prints the refusal once, not twice", async () => {
    // The guard is a boolean. `params.error && <Note/>` would render the
    // message itself, because JSX prints a truthy string.
    const message = "sign-in refused";
    const body = text(await render({ error: message }));
    expect(body.split(message).length - 1).toBe(1);
  });
});

describe("flag OFF", () => {
  it("⚠️ renders the interim form and NO recovery note", async () => {
    // Naming a flag that is already unset would send the reader to change an
    // env line that is not set, and away from the box in front of them.
    const page = await render({ error: "whatever" });
    expect(types(page)).toContain(InterimForm);
    expect(text(page)).not.toContain(IDENTITY_FLAG);
    expect(text(page)).not.toContain(RECOVERY);
  });
});

// ── Which directory the button sends you to — D70 ─────────────────────────
//
// Spec: `operator_identity_and_access.md` §4.1 check 1 · D70.1.
//
// ⚠️ **The slug and the label are ONE decision.** A page that said "Sign in
// with Google" over a link carrying `?provider=azure` would send the reader to
// Microsoft and then report a Google failure. Every case below asserts both.

describe("the sign-in provider", () => {
  beforeEach(() => {
    vi.stubEnv(IDENTITY_FLAG, "1");
    vi.stubEnv("OPERATOR_SUPABASE_URL", SUPABASE);
    vi.stubEnv("OPERATOR_CONSOLE_ORIGIN", ORIGIN);
  });

  it("defaults to Microsoft, so an unset variable changes nothing", async () => {
    // ⚠️ This is the ship-dark property. D70 moves a console that was told to
    // move, and no other.
    const page = await render();
    expect(text(page)).toContain("Sign in with Microsoft");
    expect(hrefs(page).join(" ")).toContain("provider=azure");
    expect(signinProvider({})).toBe("azure");
  });

  it("names Google, and links to Google, when told to", async () => {
    vi.stubEnv(SIGNIN_PROVIDER_FLAG, "google");
    const page = await render();
    expect(text(page)).toContain(`Sign in with ${PROVIDER_LABELS.google}`);
    expect(text(page)).not.toContain("Sign in with Microsoft");

    const link = hrefs(page).find((h) => h.includes("/auth/v1/authorize"));
    expect(link).toBeDefined();
    expect(link).toContain("provider=google");
    expect(link).not.toContain("provider=azure");
    // The redirect target is unchanged by the provider. It is the console's
    // own callback, and it must stay on the Supabase allowlist (H-54).
    expect(link).toContain(encodeURIComponent(`${ORIGIN}/login/callback`));
  });

  it("⚠️ falls back to the default on a value it does not know", async () => {
    // The Console answers 503 for an unknown name, so the page cannot sign
    // anybody in either way. Rendering the default keeps the recovery note
    // reachable rather than throwing inside a server component.
    vi.stubEnv(SIGNIN_PROVIDER_FLAG, "entra");
    expect(signinProvider()).toBe("azure");
    expect(hrefs(await render()).join(" ")).toContain("provider=azure");
  });

  it("reads the value case-insensitively and ignores padding", () => {
    expect(signinProvider({ [SIGNIN_PROVIDER_FLAG]: "  GOOGLE " })).toBe(
      "google",
    );
  });

  it("⚠️ still renders no second door on the Google path", async () => {
    // Done-when 29 does not weaken because the directory moved.
    vi.stubEnv(SIGNIN_PROVIDER_FLAG, "google");
    const kinds = types(await render({ error: "refused" }));
    expect(kinds).not.toContain(InterimForm);
    expect(kinds).not.toContain("form");
    expect(kinds).not.toContain("input");
    expect(kinds).not.toContain("button");
  });
});

// ══════════════════════════════════════════════════════════════════════════
// The EMAIL CODE fallback — WS-31 CP-12j, D71.3, spec §8 done-when 41.
// ══════════════════════════════════════════════════════════════════════════

//: A legacy Supabase key, whose payload names a role.
function key(role: string): string {
  const head = Buffer.from(JSON.stringify({ alg: "HS256" })).toString(
    "base64url",
  );
  const body = Buffer.from(JSON.stringify({ role })).toString("base64url");
  return `${head}.${body}.sig`;
}

describe("the email-code fallback", () => {
  beforeEach(() => {
    vi.stubEnv(IDENTITY_FLAG, "1");
    vi.stubEnv("OPERATOR_SUPABASE_URL", SUPABASE);
    vi.stubEnv("OPERATOR_CONSOLE_ORIGIN", ORIGIN);
  });

  it("ships dark — no form until the flag is on", async () => {
    // ⚠️ The whole D71 slice defaults to off, and this is that property on
    // the page. Deleting the flag check would show a code box on every box.
    expect(types(await render())).not.toContain(EmailCodeForm);
  });

  it("shows the form BESIDE the directory button, never instead of it", async () => {
    vi.stubEnv(EMAIL_OTP_FLAG, "1");
    vi.stubEnv(ANON_KEY_FLAG, key("anon"));
    const tree = await render();

    // Both doors. D71.4 keeps the directory the strong one, and a page that
    // dropped the button would move every operator onto the weaker method —
    // including the admin, who is the person that adds operators.
    expect(types(tree)).toContain(EmailCodeForm);
    expect(hrefs(tree).some((h) => h.includes("/auth/v1/authorize"))).toBe(true);
    expect(text(tree)).toContain("or");
  });

  it("🔴 renders NO form for a service_role key", async () => {
    // Rendering the form PUBLISHES the key to every visitor. `otp.ts` refuses
    // a key that is not publishable, and this is that refusal at the page.
    vi.stubEnv(EMAIL_OTP_FLAG, "1");
    vi.stubEnv(ANON_KEY_FLAG, key("service_role"));
    const tree = await render();

    expect(types(tree)).not.toContain(EmailCodeForm);
    // The secret must not reach the tree by any other route either.
    expect(JSON.stringify(tree)).not.toContain(key("service_role"));
  });

  it("still shows the code form when the DIRECTORY is unconfigured", async () => {
    // A box with the fallback on and no Supabase origin is exactly the box
    // this slice exists for. The old page printed a configuration banner and
    // nothing else, which would strand a person who has a working code path.
    vi.stubEnv("OPERATOR_CONSOLE_ORIGIN", "");
    vi.stubEnv(EMAIL_OTP_FLAG, "1");
    vi.stubEnv(ANON_KEY_FLAG, key("anon"));
    const tree = await render();

    expect(types(tree)).toContain(EmailCodeForm);
    expect(text(tree)).not.toContain("Sign-in is not configured");
    // And the recovery note stays OFF, because this reader is not stranded.
    expect(text(tree)).not.toContain(RECOVERY);
  });

  it("keeps the recovery note when NEITHER door works", async () => {
    vi.stubEnv("OPERATOR_CONSOLE_ORIGIN", "");
    const body = text(await render());
    expect(body).toContain("Sign-in is not configured");
    expect(body).toContain(RECOVERY);
  });

  it("never renders the passphrase form beside the code form", async () => {
    // §8 done-when 29 — one door at a time for the PASSPHRASE. D71.3 adds a
    // second identity method, and it does not reopen the interim path.
    vi.stubEnv(EMAIL_OTP_FLAG, "1");
    vi.stubEnv(ANON_KEY_FLAG, key("anon"));
    expect(types(await render())).not.toContain(InterimForm);
  });
});

describe("email-only sign-in — the owner's 2026-09-02 shape", () => {
  beforeEach(() => {
    vi.stubEnv(IDENTITY_FLAG, "1");
    vi.stubEnv("OPERATOR_SUPABASE_URL", SUPABASE);
    vi.stubEnv("OPERATOR_CONSOLE_ORIGIN", ORIGIN);
    vi.stubEnv(EMAIL_OTP_FLAG, "1");
    vi.stubEnv(ANON_KEY_FLAG, key("anon"));
    vi.stubEnv(DIRECTORY_SIGNIN_FLAG, "0");
  });

  it("offers the code form and NO directory button", async () => {
    const tree = await render();
    expect(types(tree)).toContain(EmailCodeForm);
    expect(hrefs(tree).some((h) => h.includes("/auth/v1/authorize"))).toBe(false);
  });

  it("prints no configuration banner and no recovery note", async () => {
    // A person on an email-only box is not stranded and not misconfigured.
    // Either message here would send them looking for a problem they do not
    // have.
    const body = text(await render());
    expect(body).not.toContain("Sign-in is not configured");
    expect(body).not.toContain(RECOVERY);
  });

  it("drops the 'or' divider, because there is nothing to choose between", async () => {
    // ⚠️ Asserted on the className in the TREE, not on visible text. An
    // earlier version of this case read `text()` for "or-rule" — a class name
    // is never text, so it could not have failed and proved nothing.
    expect(classes(await render())).not.toContain("or-rule");
    // And the divider IS there when both doors are offered, which is what
    // proves the walker above can see it at all.
    vi.stubEnv(DIRECTORY_SIGNIN_FLAG, "1");
    expect(classes(await render())).toContain("or-rule");
  });

  it("still shows the banner when the code form is off TOO", async () => {
    vi.stubEnv(EMAIL_OTP_FLAG, "0");
    const body = text(await render());
    expect(body).toContain("Sign-in is not configured");
    expect(body).toContain(RECOVERY);
  });
});
