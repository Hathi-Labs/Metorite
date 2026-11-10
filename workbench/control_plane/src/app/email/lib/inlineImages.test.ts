import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { BLOCKED_REMOTE_CSS } from "./bodyLook";
import { imgSrcDirective, inlineImagePath, inlineImageUrl } from "./inlineImages";

/**
 * `email-inline-cid` — the inline pictures of a mail body (WS-17, the inline
 * images fix, `email_app_master_plan.md` §16.6). The pure rules. The e2e
 * `e2e/email-inline-images.spec.ts` loads a real picture in a real frame.
 */

const ID = "88888888-8888-4888-8888-888888888888";

describe("email-inline-cid", () => {
  it("names the inline route of the message, as a relative path", () => {
    expect(inlineImagePath(ID)).toBe(`/api/email/messages/${ID}/inline`);
    expect(inlineImagePath(null)).toBeNull();
    expect(inlineImagePath("")).toBeNull();
    // An id that is not a plain UUID cannot break out of the path or the CSP.
    expect(inlineImagePath('a/b"; img-src *')).toBe("/api/email/messages/a%2Fb%22%3B%20img-src%20*/inline");
  });

  it("writes a cid: URL as the URL of its picture", () => {
    const path = inlineImagePath(ID);
    expect(inlineImageUrl("cid:image001.png@01DD5A75.A7B8EA10", path)).toBe(
      `${path}?cid=image001.png%4001DD5A75.A7B8EA10`,
    );
    expect(inlineImageUrl("  CID:ii_mv5obra50 ", path)).toBe(`${path}?cid=ii_mv5obra50`);
    // An escape of RFC 2392 goes through as it is. The gateway decodes it.
    expect(inlineImageUrl("cid:a%40b", path)).toBe(`${path}?cid=a%2540b`);
  });

  it("leaves every other URL, and a cid: with no message, alone", () => {
    const path = inlineImagePath(ID);
    for (const v of ["https://cdn.test/a.png", "data:image/png;base64,AA", "cid:", "cid: a b", "xcid:a"]) {
      expect(inlineImageUrl(v, path), v).toBeNull();
    }
    expect(inlineImageUrl("cid:logo", null)).toBeNull();
  });

  it("the rewritten URL is never a remote picture", () => {
    // `BLOCKED_REMOTE_CSS` drops the re-invert of a picture whose src starts
    // with http. An absolute URL would draw the inline picture inverted.
    const url = inlineImageUrl("cid:logo", inlineImagePath(ID)) ?? "";
    expect(BLOCKED_REMOTE_CSS).toContain('img[src^="http" i]');
    expect(/^https?:/i.test(url)).toBe(false);
    expect(url.startsWith("/api/email/messages/")).toBe(true);
  });

  it("the CSP lets in data: and the ONE inline path while remote pictures are blocked", () => {
    const origin = "https://app.metorite.com";
    const path = inlineImagePath(ID);
    expect(imgSrcDirective(false, origin, path)).toBe(`img-src data: ${origin}/api/email/messages/${ID}/inline;`);
    expect(imgSrcDirective(false, origin, null)).toBe("img-src data:;");
    expect(imgSrcDirective(false, "", path)).toBe("img-src data:;");
    // A path with no trailing slash, so no other route of our origin matches.
    expect(imgSrcDirective(false, origin, path).endsWith("/inline;")).toBe(true);
    expect(imgSrcDirective(true, origin, path)).toBe(`img-src 'self' ${origin} data:;`);
  });

  it("the frame rewrites cid: before the remote check, and uses the directive", () => {
    const src = readFileSync(join(__dirname, "../components/MessageContent.tsx"), "utf-8").replace(/\r\n/g, "\n");
    expect(src).toContain("const inline = v ? inlineImageUrl(v, inlinePath) : null;");
    expect(src).toContain("const imgSrc = imgSrcDirective(showImages, origin, inlinePath);");
    expect(src).toContain("<InlineImageContext.Provider value={inlineImagePath(messageId)}>");
  });

  it("each reading surface passes the id of its message", () => {
    const root = join(__dirname, "../../..");
    const sites: Array<[string, string]> = [
      ["app/email/components/EmailDetail.tsx", "messageId={view.id}"],
      ["app/email/components/ConversationView.tsx", "messageId={view.id}"],
      ["app/email/components/EmailPreviewModal.tsx", "messageId={e.id}"],
      ["components/email/EmailToolCards.tsx", "messageId={row.id}"],
    ];
    for (const [file, prop] of sites) {
      const text = readFileSync(join(root, file), "utf-8");
      expect(text, file).toContain(prop);
    }
  });
});
