// WS-17 EM-T3c — the public landing page of an admin-consent return.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.3, "EM-T3c".
//
// vitest here runs in the node environment. The decision lives in `view.ts`
// and these cases run it. The card renders to static markup. The page itself
// is an async server component, so a source scan (comments stripped) holds
// what it may not do: no session, no fetch, no request value beyond `result`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { featureForPath } from "@/lib/access";
import { isChromeless } from "@/lib/nav";

import ApprovedCard from "./ApprovedCard";
import OAuthApprovedPage from "./page";
import { APPROVED_COPY, approvedResult } from "./view";

/** Render the REAL async page, with the query a browser would send. */
async function pageHtml(query: { [key: string]: string | string[] | undefined }): Promise<string> {
  const el = await OAuthApprovedPage({ searchParams: Promise.resolve(query) });
  return renderToStaticMarkup(el);
}

const HERE = __dirname;

function read(name: string): string {
  return readFileSync(join(HERE, name), { encoding: "utf-8" });
}

/** Source with `//` and block comments removed, so prose cannot pass a scan. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

function html(result: string | string[] | undefined): string {
  return renderToStaticMarkup(createElement(ApprovedCard, { result: approvedResult(result) }));
}

describe("the result token (EM-T3c)", () => {
  it("no result reads as approved", () => {
    expect(approvedResult(undefined)).toBe("approved");
  });

  it("reads the two other tokens of the fixed set", () => {
    expect(approvedResult("declined")).toBe("declined");
    expect(approvedResult("failed")).toBe("failed");
  });

  it("treats every value outside the set as failed", () => {
    for (const v of ["<script>", "", "Approved", "approved ", "DECLINED", "x", ["declined", "x"]]) {
      expect(approvedResult(v), JSON.stringify(v)).toBe("failed");
    }
  });

  it("an explicit approved token is approved", () => {
    expect(approvedResult("approved")).toBe("approved");
  });
});

describe("the page copy (EM-T3c)", () => {
  it("says Approved, and that members can now connect", () => {
    const out = html(undefined);
    expect(out).toContain("Approved");
    expect(APPROVED_COPY.approved.title).toBe("Approved");
    expect(APPROVED_COPY.approved.body).toMatch(/Microsoft reported/);
    expect(APPROVED_COPY.approved.body).toMatch(/members of your organization can now connect/i);
  });

  it("claims no more than a Microsoft report", () => {
    // Anyone can open the page by hand. It must not say that Metorite checked.
    const all = Object.values(APPROVED_COPY)
      .map((c) => `${c.title} ${c.body} ${c.note}`)
      .join(" ");
    expect(all).not.toMatch(/verified|confirmed|we checked/i);
  });

  it("shows the failed copy for result=<script>, and never the value", () => {
    const out = html("<script>");
    expect(out).toContain(APPROVED_COPY.failed.title);
    expect(out).not.toContain("<script>");
    expect(out).not.toContain("&lt;script&gt;");
  });

  it("shows the declined copy for result=declined", () => {
    expect(html("declined")).toContain(APPROVED_COPY.declined.title);
  });

  it("uses status tokens, never a raw palette class", () => {
    for (const r of ["approved", "declined", "failed"]) {
      expect(html(r)).not.toMatch(/\b(?:bg|text|border)-(?:red|green|emerald|amber|sky|blue|zinc|slate|gray)-\d/);
    }
  });
});

describe("the real page renders the token, never the value (EM-T3c, fix round 1)", () => {
  it("no result renders Approved", async () => {
    const out = await pageHtml({});
    expect(out).toContain(`>${APPROVED_COPY.approved.title}<`);
    expect(out).toContain(APPROVED_COPY.approved.body);
  });

  it("result=declined renders the declined copy", async () => {
    const out = await pageHtml({ result: "declined" });
    expect(out).toContain(APPROVED_COPY.declined.title);
    expect(out).toContain(APPROVED_COPY.declined.body);
    expect(out).not.toContain(APPROVED_COPY.approved.body);
  });

  it("result=<script> renders the failed copy and no trace of the value", async () => {
    const out = await pageHtml({ result: "<script>" });
    expect(out).toContain(APPROVED_COPY.failed.title);
    expect(out).not.toContain("<script>");
    expect(out).not.toContain("&lt;script&gt;");
    expect(out).not.toContain("script");
  });

  it("a repeated result renders the failed copy", async () => {
    const out = await pageHtml({ result: ["declined", "approved"] });
    expect(out).toContain(APPROVED_COPY.failed.title);
    expect(out).not.toContain(APPROVED_COPY.approved.body);
  });

  it("ignores every other query value", async () => {
    const out = await pageHtml({
      tenant: "evil-tenant-value",
      error_description: "evil-description-value",
    });
    expect(out).toContain(APPROVED_COPY.approved.title);
    expect(out).not.toContain("evil-");
  });
});

describe("the page holds no session and makes no fetch (EM-T3c)", () => {
  const PAGE = codeOnly(read("page.tsx"));
  const CARD = codeOnly(read("ApprovedCard.tsx"));
  const VIEW = codeOnly(read("view.ts"));

  it("reads result and nothing else from the query", () => {
    expect(PAGE).toMatch(/const \{ result \} = await searchParams;/);
    expect(PAGE.match(/searchParams/g)).toHaveLength(3);
    expect(PAGE).not.toMatch(/tenant|error_description|admin_consent/);
  });

  it("calls no session, no gateway and no fetch", () => {
    for (const src of [PAGE, CARD, VIEW]) {
      expect(src).not.toMatch(/\bfetch\(|@\/auth|@\/lib\/gateway|useSession|next-auth|cookies\(|headers\(/);
    }
  });
});

describe("the door of the page (EM-T3c)", () => {
  it("is chromeless and under no feature gate", () => {
    expect(isChromeless("/oauth/approved")).toBe(true);
    expect(featureForPath("/oauth/approved")).toBeNull();
  });
});
