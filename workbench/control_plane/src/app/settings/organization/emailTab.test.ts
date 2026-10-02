// WS-17 EM-T3d — Organisation → Email, drawn.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.3, "EM-T3d".
//
// vitest here runs in the node environment and reads `*.test.ts` only, so
// this file draws the pure `EmailTabView` with `createElement` and
// `renderToStaticMarkup`. The container's wiring (which helper it calls,
// which path it reads) is held by source scans with comments stripped.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { adminConsentUrl, type MailAppInfo } from "@/app/email/lib/connect";
import { isKnownIcon } from "@/lib/icons";

import { EmailTabView } from "./EmailTab";
import {
  COUNT_LABELS,
  EMAIL_TAB_COPY,
  mapConnectionCounts,
  type CountsRead,
} from "./lib/emailConnections";

const HERE = __dirname;

function read(rel: string): string {
  return readFileSync(join(HERE, rel), { encoding: "utf-8" });
}

/** Source with `//` and block comments removed, so prose cannot pass a scan. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

const APP: MailAppInfo = {
  clientId: "00000000-1111-4222-8333-444444444444",
  redirectUri: "https://app.metorite.test/api/email/oauth/microsoft/callback",
};

const READY: CountsRead = {
  state: "ready",
  counts: mapConnectionCounts({
    members: 7,
    mailboxes: 9,
    microsoft: 6,
    gmail: 2,
    imap: 1,
    sync_errors: 3,
    first_sync_pending: 4,
  })!,
};

function html(app: MailAppInfo | null | undefined, counts: CountsRead): string {
  return renderToStaticMarkup(createElement(EmailTabView, { app, counts, onRetry: () => {} }));
}

/** The markup of the count section only. "Microsoft 365" is also a title above. */
function countsPart(markup: string): string {
  const at = markup.indexOf(EMAIL_TAB_COPY.counts.title);
  expect(at).toBeGreaterThan(-1);
  return markup.slice(at);
}

/** Every `<a …>` opening tag, with its attributes. */
function anchors(markup: string): Record<string, string>[] {
  return [...markup.matchAll(/<a\b([^>]*)>/g)].map((m) => {
    const attrs: Record<string, string> = {};
    for (const a of m[1].matchAll(/([\w-]+)="([^"]*)"/g)) {
      attrs[a[1]] = a[2].replace(/&amp;/g, "&");
    }
    return attrs;
  });
}

describe("the pre-approval link (EM-T3d)", () => {
  it("links to adminConsentUrl(app), in a new tab, with no opener", () => {
    const links = anchors(html(APP, READY));
    expect(links).toHaveLength(1);
    expect(links[0].href).toBe(adminConsentUrl(APP));
    expect(links[0].target).toBe("_blank");
    expect(links[0].rel).toBe("noopener noreferrer");
  });

  it("draws no link with no app, and a fixed sentence instead", () => {
    const out = html(null, READY);
    expect(anchors(out)).toEqual([]);
    expect(out).not.toContain("login.microsoftonline.com");
    expect(out).toContain(EMAIL_TAB_COPY.approval.unavailable);
  });

  it("draws no link while the app is loading", () => {
    const out = html(undefined, READY);
    expect(anchors(out)).toEqual([]);
    expect(out).not.toContain(EMAIL_TAB_COPY.approval.unavailable);
  });

  it("never says the organization approved the app", () => {
    for (const app of [APP, null, undefined]) {
      expect(html(app, READY)).not.toMatch(/\bapproved\b/i);
    }
  });
});

describe("the counts (EM-T3d)", () => {
  it("draws the seven counts", () => {
    const out = countsPart(html(APP, READY));
    for (const label of Object.values(COUNT_LABELS)) expect(out).toContain(label);
    for (const n of [7, 9, 6, 2, 1, 3, 4]) expect(out).toContain(`>${n}<`);
  });

  it("holds no @ for a fixture with counts", () => {
    expect(html(APP, READY)).not.toContain("@");
    expect(html(null, READY)).not.toContain("@");
  });

  it("a failed read draws an error sentence, never '0 members'", () => {
    const whole = html(APP, { state: "failed" });
    const out = countsPart(whole);
    expect(out).toContain(EMAIL_TAB_COPY.counts.failed);
    expect(whole).not.toContain(">0<");
    expect(whole).not.toMatch(/\b0 members?\b/i);
    for (const label of Object.values(COUNT_LABELS)) expect(out).not.toContain(label);
  });

  it("a read in flight draws no number", () => {
    const out = countsPart(html(APP, { state: "loading" }));
    expect(out).toContain('aria-busy="true"');
    expect(out).not.toMatch(/>\d+</);
    for (const label of Object.values(COUNT_LABELS)) expect(out).not.toContain(label);
    expect(out).not.toContain(EMAIL_TAB_COPY.counts.failed);
  });
});

describe("the container's wiring (EM-T3d)", () => {
  const src = codeOnly(read("EmailTab.tsx"));

  it("reuses getMailAppInfo and adminConsentUrl, and copies neither", () => {
    expect(src).toMatch(/import\s*\{[^}]*\bgetMailAppInfo\b[^}]*\}\s*from\s*"@\/app\/email\/lib\/api"/);
    expect(src).toMatch(/import\s*\{[^}]*\badminConsentUrl\b[^}]*\}\s*from\s*"@\/app\/email\/lib\/connect"/);
    expect(src).not.toMatch(/function\s+(adminConsentUrl|getMailAppInfo)\b/);
    expect(src).not.toContain("login.microsoftonline.com");
    expect(src).not.toContain("/email/oauth/microsoft/app");
  });

  it("reads the counts through the email BFF catch-all, with no new route", () => {
    expect(src).toContain('"/api/email/admin/connections"');
    expect(existsSync(join(HERE, "../../api/email/admin"))).toBe(false);
    expect(existsSync(join(HERE, "../../api/email/[...path]/route.ts"))).toBe(true);
  });

  it("maps the payload through the one mapper, never by hand", () => {
    expect(src).toMatch(/\breadConnectionCounts\(/);
    expect(src).not.toMatch(/\bsync_errors\b|\bfirst_sync_pending\b/);
  });

  it("names only icons that exist, so none falls back to Zap", () => {
    const names = [...src.matchAll(/name="([A-Za-z0-9]+)"/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(0);
    for (const n of names) expect(isKnownIcon(n), n).toBe(true);
  });
});

describe("Organisation has five tabs (EM-T3d)", () => {
  const admin = read("OrganizationAdmin.tsx");
  const code = codeOnly(admin);

  it("the strip carries an Email tab that draws EmailTab", () => {
    expect(code).toMatch(/id:\s*"email"/);
    expect(code).toMatch(/label:\s*"Email"/);
    expect(code).toMatch(/tab === "email"\s*\?\s*\(?\s*<EmailTab\s*\/>/);
  });

  it("the header comment says five tabs", () => {
    expect(admin).toContain("Five tabs, one surface");
    expect(admin).not.toContain("Four tabs");
  });
});
