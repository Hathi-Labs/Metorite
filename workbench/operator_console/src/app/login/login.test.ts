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

import { IDENTITY_FLAG } from "@/lib/identity";
import LoginPage from "./page";
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

async function render(params: { origin?: string; error?: string } = {}) {
  return await LoginPage({ searchParams: Promise.resolve(params) });
}

const RECOVERY = "restart the console";

beforeEach(() => {
  vi.unstubAllEnvs();
  vi.stubEnv("OPERATOR_SUPABASE_URL", "");
  vi.stubEnv("OPERATOR_CONSOLE_ORIGIN", "");
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
