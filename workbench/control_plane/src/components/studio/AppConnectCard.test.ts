// WS-STU I8c: the setup card of an app page. Source fences, the lines as markup, and the return path.
//
// R7: M1, build the link from the claim payload (`installNeeds`) in the card. "reads the I8a route only"
// goes red. M2, add an Input to the card. "holds no input" goes red. M3, drop the `back` pass in
// `connectHref`. "returns to the app page" goes red.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ConnectLines } from "@/components/studio/ConnectSheet";
import { connectHref, connectLines, getConnections, type ConnectionRow } from "@/lib/studio/connect";
import { mapAnswer, NEEDS_CONNECTION_SENTENCE } from "@/lib/studio/realDoor";
import { previewHref } from "@/lib/studio/packagePreview";

const SRC = resolve(__dirname, "../..");
const read = (p: string) => readFileSync(resolve(SRC, p), "utf-8").replace(/\r\n/g, "\n");
const PKG = "0f8e2a4c-1b3d-4e5f-8a9b-0c1d2e3f4a5b";
const row = (over: Partial<ConnectionRow> = {}): ConnectionRow => ({
  name: "zoho", label: "Zoho CRM", met: false, checkable: true, setupHref: "/crm", canConnect: true, ...over,
});
const card = read("components/studio/AppConnectCard.tsx");

describe("AppConnectCard", () => {
  it("reads the I8a route only, and no claim of the package", () => {
    expect(card).toMatch(/getConnections\(pkg\)/);
    expect(card).toMatch(/revalidateOnFocus: true/);
    expect(card).not.toMatch(/installNeeds|packageManifest|setup_href/);
  });

  it("holds no Input and no textarea: a key is never typed here", () => {
    expect(card).not.toMatch(/<Input|<textarea|<input|type="password"/i);
  });

  it("draws one card with one line for each unmet name", () => {
    const lines = connectLines([row(), row({ name: "mcp:docs", label: "docs", setupHref: "/integrations?add=mcp" }), row({ name: "gmail", label: "Gmail", met: true })], PKG, previewHref(PKG));
    const html = renderToStaticMarkup(createElement(ConnectLines, { lines }));
    expect(html.match(/aria-label="Connect /g)?.length).toBe(2);
    expect(html).toContain("is connected.");
  });

  it("a member who may not connect reads the sentence and gets no button", () => {
    const lines = connectLines([row({ canConnect: false })], PKG, previewHref(PKG));
    const html = renderToStaticMarkup(createElement(ConnectLines, { lines }));
    expect(html).toContain("An admin must connect Zoho CRM.");
    expect(html).not.toContain("<button");
  });

  it("a 404 gives no rows, so nothing draws", async () => {
    const rows = await getConnections(PKG, (async () => new Response("{}", { status: 404 })) as typeof fetch);
    expect(rows).toEqual([]);
  });

  it("the sheet opens from the card: the lines hold the one ConnectSheet", () => {
    expect(card).toContain('from "@/components/studio/ConnectSheet"');
    expect(read("components/studio/ConnectSheet.tsx")).toMatch(/<ConnectSheet line=\{open\}/);
  });

  it("both views mount the card, for the owner only", () => {
    const page = read("app/studio/package-preview/page.tsx");
    expect(page).toContain("above={controls.owner ? <AppConnectCard pkg={pkg} /> : undefined}");
    expect(page).toContain("{controls.owner ? <AppConnectCard pkg={pkg} /> : null}");
    expect(read("app/studio/package-preview/AppFrame.tsx")).toContain("{above ?");
  });
});

describe("connectHref with a back path", () => {
  const r = row();
  it("returns to the app page", () => {
    const href = connectHref(r, PKG, previewHref(PKG))!;
    expect(new URL(href, "https://x.invalid").searchParams.get("return")).toBe(`/studio/package-preview?pkg=${PKG}`);
  });
  it("keeps the build page as the default", () => {
    const href = connectHref(r, PKG)!;
    expect(new URL(href, "https://x.invalid").searchParams.get("return")).toBe(`/studio/build?pkg=${PKG}`);
  });
  it.each(["//host", "/.//host", "/studio/%2f%2fhost", "javascript:alert(1)", "/admin", "/studio/../admin", "https://evil.test/studio/x"])(
    "refuses a back of %s", (bad) => {
      const href = connectHref(r, PKG, bad)!;
      expect(new URL(href, "https://x.invalid").searchParams.has("return")).toBe(false);
    });
  it("refuses a link with an encoded slash or a second host", () => {
    expect(connectHref({ setupHref: "/%2fhost" }, PKG, previewHref(PKG))).toBeNull();
    expect(connectHref({ setupHref: "/.//host" }, PKG, previewHref(PKG))).toBeNull();
  });
});

describe("the 409 of a missing connection", () => {
  it("reads as one plain sentence", () => {
    expect(mapAnswer(409, { detail: { code: "needs_connection" } })).toEqual({ ok: false, message: NEEDS_CONNECTION_SENTENCE });
  });
});
