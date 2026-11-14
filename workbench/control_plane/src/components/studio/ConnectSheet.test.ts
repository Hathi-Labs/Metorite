// WS-STU I8a, Connect in place: the source fences of the Studio half, and the lines as markup.
//
// Studio copies no connect flow (`ai_studio.md` §8.1 rule 1). It calls the server-given setup_href
// of each row, and it imports no part of the console: not `_SETUP_HREF`, not `APP_OWNED_TILES` and
// not `McpFormFields`. The runner has no DOM, so the lines render to a string.
//
// R7: M1, import `McpFormFields` into ConnectSheet.tsx. "imports no part of the console" goes red.
// M2, write `href="/crm"` in ConnectSheet.tsx. "the link comes from the row" goes red. M3, swap the
// shared Modal for a hand-rolled overlay. "the sheet is the AF-6 pattern" goes red.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { connectLines, type ConnectionRow } from "@/lib/studio/connect";

import { ConnectLines } from "./ConnectSheet";

const SRC = resolve(__dirname, "../..");
const read = (p: string) => readFileSync(resolve(SRC, p), "utf-8").replace(/\r\n/g, "\n");

const STUDIO_FILES = [
  "components/studio/ConnectSheet.tsx",
  "lib/studio/connect.ts",
  "lib/studio/returnPath.ts",
  "app/studio/build/components/ProblemsPanel.tsx",
  "app/studio/build/page.tsx",
];

const PKG = "0f8e2a4c-1b3d-4e5f-8a9b-0c1d2e3f4a5b";
const row = (over: Partial<ConnectionRow> = {}): ConnectionRow => ({
  name: "zoho", label: "Zoho CRM", met: false, checkable: true, setupHref: "/crm", canConnect: true, ...over,
});

describe("Studio copies no connect flow", () => {
  it("imports no part of the console", () => {
    for (const file of STUDIO_FILES) {
      const src = read(file);
      expect(src, file).not.toMatch(/_SETUP_HREF|APP_OWNED_TILES|McpFormFields|AddMcpModal/);
      expect(src, file).not.toMatch(/from "@\/app\/integrations/);
      expect(src, file).not.toMatch(/from "@\/app\/(email|crm|calendar|knowledge|whatsapp)\//);
    }
  });

  it("the link comes from the row: setup_href of the gateway, and no path of an app", () => {
    const model = read("lib/studio/connect.ts");
    expect(model).toMatch(/safeLocalPath\(item\.setup_href\)/);
    expect(model).toMatch(/new URL\(href, BASE\)/);
    for (const file of STUDIO_FILES.slice(0, 3)) {
      const src = read(file);
      expect(src, file).not.toMatch(/["'`]\/(crm|email|whatsapp|calendar|knowledge|integrations)\b/);
    }
    const sheet = read("components/studio/ConnectSheet.tsx");
    expect(sheet).toMatch(/href=\{line\.href \?\? undefined\}/);
    expect(sheet).not.toMatch(/window\.location|router\.push/);
  });

  it("the sheet is the AF-6 pattern: the shared Modal, a sheet on a phone, and no raw control", () => {
    const sheet = read("components/studio/ConnectSheet.tsx");
    expect(sheet).toMatch(/import Modal from "@\/components\/ui\/Modal"/);
    expect(sheet).toMatch(/placement=\{phone \? "sheet" : "center"\}/);
    expect(sheet).toMatch(/useBelowSm/);
    expect(sheet).not.toMatch(/<(button|a|input|dialog)[\s>]/);
    expect(sheet).not.toMatch(/fixed inset-0|lucide-react|#[0-9a-fA-F]{3,6}\b/);
  });

  it("the IDE reads the one route and passes the lines, the failure and Retry to the Problems panel", () => {
    const page = read("app/studio/build/page.tsx");
    expect(page).toMatch(/getConnections\(pkg\)/);
    // Review P1 #1: the error of the read reaches the panel, on the desktop and on the phone.
    expect(page).toContain("connectPanel(conns.data, conns.error, pkg)");
    const passes = /connections=\{panel\.lines\} missing=\{panel\.missing\} failure=\{panel\.failure\} onRetry=\{refreshConns\}/g;
    expect(page.match(passes)).toHaveLength(2);
    expect(page).toMatch(/phoneProblemsCount\(problems\.length, conns\.data\)/);
    const panel = read("app/studio/build/components/ProblemsPanel.tsx");
    expect(panel).toContain("<ConnectLines lines={connections} failure={failure} onRetry={onRetry} />");
  });
});

describe("the lines", () => {
  const html = (rows: ConnectionRow[]) =>
    renderToStaticMarkup(createElement(ConnectLines, { lines: connectLines(rows, PKG) }));

  it("an unmet line has Connect, named for the provider", () => {
    const out = html([row()]);
    expect(out).toContain("This app needs Zoho CRM.");
    expect(out).toContain('aria-label="Connect Zoho CRM"');
    expect(out).toContain(">Connect<");
  });

  it("a member who may not connect sees the one sentence and no button", () => {
    const out = html([row({ canConnect: false })]);
    expect(out).toContain("An admin must connect Zoho CRM. Ask one, then come back.");
    expect(out).not.toContain("<button");
  });

  it("a met line reads connected, with no button", () => {
    const out = html([row({ met: true })]);
    expect(out).toContain("Zoho CRM is connected.");
    expect(out).not.toContain("<button");
  });

  it("no connection draws nothing", () => {
    expect(html([])).toBe("");
  });

  it("a read that failed draws its line with Retry, never nothing (review P1 #1)", () => {
    const out = renderToStaticMarkup(createElement(ConnectLines, {
      lines: [], failure: "Metorite could not check the connections of this app. Try again in a moment.", onRetry: () => undefined,
    }));
    expect(out).toContain("Metorite could not check the connections of this app. Try again in a moment.");
    expect(out).toContain(">Retry<");
    expect(out).toContain('aria-label="Check the connections again"');
  });

  it("a name the gateway cannot check draws one sentence and no button (review P1 #2)", () => {
    const out = html([row({ name: "mailbox", label: "Email account", checkable: false, canConnect: false })]);
    expect(out).toContain("Metorite cannot check Email account yet. It will show here when it can.");
    expect(out).not.toContain("<button");
  });
});
