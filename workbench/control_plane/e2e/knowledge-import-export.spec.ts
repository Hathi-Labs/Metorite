import { expect, test, type Page } from "@playwright/test";
import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";
import { readFileSync } from "node:fs";

import { stubApi } from "./visual/harness";

test.describe.configure({ timeout: 150_000 });

/**
 * Import and export of Knowledge pages (WS-61 KB-2m, `knowledge_base.md`
 * §21.7): Markdown and HTML files and a Notion-style zip in, a page or a
 * space as Markdown or HTML out.
 *
 * The BFF at `/api/knowledge/**` is a small mock that keeps state. It has its
 * own copy, as the other Knowledge specs do. The gateway half of a create is
 * `tests/unit/test_kb_pages_r8.py`.
 */

const SPACE_ID = "ks-company";

type Made = { id: string; title: string; parent_id: string | null; body_md: string; space_id: string };
type Existing = { id: string; title: string; parent_id: string | null; body_md: string };

const EXISTING: Existing[] = [
  { id: "kp-home", title: "Home page", parent_id: null, body_md: "Welcome. See [the sub-page](/knowledge/pages/kp-sub) and [away](/knowledge/pages/kp-away)." },
  { id: "kp-sub", title: "Sub page", parent_id: "kp-home", body_md: "Back to [home](/knowledge/pages/kp-home).\n\n- one\n- two" },
];

async function setup(page: Page, opts: { canWrite?: boolean } = {}) {
  const canWrite = opts.canWrite ?? true;
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, {
    "auth/me": {
      authenticated: true,
      email: "asha@example.com",
      name: "Asha Rao",
      is_admin: false,
      features: ["knowledge"],
      permissions: [],
      capabilities: [],
      roles: ["member"],
      organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
    },
  });
  const now = new Date().toISOString();
  const made: Made[] = [];
  const patches: Array<{ id: string; body: { title?: string; body_md?: string } }> = [];
  const summary = (id: string, title: string, parent: string | null, position = 0) => ({
    id, space_id: SPACE_ID, parent_id: parent, position, title, icon: null, state: "published",
    author_email: "asha@example.com", reviewers: [], review_at: null, source_ref: null, published_at: null,
    archived_at: null, history_from: null, archived_from_state: null, updated_at: now, created_at: now,
  });
  const json = (status: number, payload: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(payload) });
  await page.route("**/api/knowledge/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/knowledge\//, "");
    if (path === "spaces") {
      return route.fulfill(json(200, { spaces: [{ id: SPACE_ID, slug: "company", name: "Company", purpose: "How we work.",
        icon: "", kind: "company", owner_email: null, archived_at: null, created_at: now, level: canWrite ? "write" : "read", page_count: EXISTING.length }] }));
    }
    if (path === "pages" && req.method() === "GET") {
      const all = [
        ...EXISTING.map((p, i) => summary(p.id, p.title, p.parent_id, i)),
        ...made.map((p, i) => ({ ...summary(p.id, p.title, p.parent_id, 10 + i), state: "draft" })),
      ];
      return route.fulfill(json(200, { pages: all }));
    }
    if (path === "pages" && req.method() === "POST") {
      const body = JSON.parse(req.postData() ?? "{}") as { title: string; parent_id?: string; body_md?: string; space_id: string };
      const id = `new-${made.length + 1}`;
      made.push({ id, title: body.title, parent_id: body.parent_id ?? null, body_md: body.body_md ?? "", space_id: body.space_id });
      return route.fulfill(json(201, { ...summary(id, body.title, body.parent_id ?? null), state: "draft", body_md: body.body_md ?? "", origin: null, version: 1 }));
    }
    const m = /^pages\/([^/]+)$/.exec(path);
    if (m) {
      const id = decodeURIComponent(m[1]);
      const found = EXISTING.find((p) => p.id === id) ?? made.find((p) => p.id === id);
      if (!found) return route.fulfill(json(404, { detail: "Not found" }));
      if (req.method() === "PATCH") {
        const patch = JSON.parse(req.postData() ?? "{}") as { title?: string; body_md?: string };
        patches.push({ id, body: patch });
        if (typeof patch.body_md === "string") found.body_md = patch.body_md;
        return route.fulfill(json(200, { ...summary(id, found.title, found.parent_id), body_md: found.body_md, origin: null, version: 2 }));
      }
      return route.fulfill(json(200, {
        ...summary(found.id, found.title, found.parent_id), body_md: found.body_md, origin: null,
        level: canWrite ? "write" : "read", can_write: canWrite, links: [], versions: [],
      }));
    }
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  return { made, patches };
}

async function openSpaceMenu(page: Page, item: string) {
  await page.goto(`/knowledge?space=${SPACE_ID}`);
  await page.getByRole("button", { name: "Space actions" }).click({ timeout: 30_000 });
  await page.getByRole("menu").getByText(item, { exact: true }).click();
}

const input = (page: Page) => page.getByTestId("kb-import-input");
const file = (name: string, text: string) => ({ name, mimeType: "text/plain", buffer: Buffer.from(text, "utf8") });

test.describe("Knowledge import", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test("two Markdown files make two drafts and show the report", async ({ page }) => {
    const kb = await setup(page);
    await openSpaceMenu(page, "Import pages");
    await input(page).setInputFiles([
      file("refund.md", "# Refund policy\n\nWithin 30 days.\n"),
      file("Plain notes.markdown", "No heading here.\n\n![Team photo](team.png)"),
    ]);
    await expect(page.getByRole("list", { name: "Chosen files" })).toContainText("refund.md");
    await page.getByRole("button", { name: "Import", exact: true }).click();
    const report = page.getByTestId("kb-import-report");
    await expect(report).toContainText("Import finished", { timeout: 30_000 });
    await expect(report).toContainText("Created 2 pages as drafts");
    await expect(report).toContainText("1 image became text");
    expect(kb.made.map((p) => [p.title, p.parent_id, p.space_id])).toEqual([
      ["Plain notes", null, SPACE_ID],
      ["Refund policy", null, SPACE_ID],
    ]);
    expect(kb.made.find((p) => p.title === "Refund policy")?.body_md).toBe("Within 30 days.");
    expect(kb.made.find((p) => p.title === "Plain notes")?.body_md).toBe("No heading here.\n\n*Team photo*");
  });

  test("a Notion-style zip makes the parent first, and links point to the new pages", async ({ page }) => {
    const kb = await setup(page);
    const ID1 = "0123456789abcdef0123456789abcdef";
    const ID2 = "fedcba9876543210fedcba9876543210";
    const zip = zipSync({
      [`Handbook ${ID1}.md`]: strToU8(`# Handbook\n\nSee [leave](Handbook%20${ID1}/Leave%20${ID2}.md).`),
      [`Handbook ${ID1}/Leave ${ID2}.md`]: strToU8(`# Leave\n\nBack to [the handbook](../Handbook%20${ID1}.md).`),
      [`Handbook ${ID1}/diagram.png`]: new Uint8Array([1, 2, 3]),
    });
    await openSpaceMenu(page, "Import pages");
    await input(page).setInputFiles({ name: "Export.zip", mimeType: "application/zip", buffer: Buffer.from(zip) });
    await page.getByRole("button", { name: "Import", exact: true }).click();
    const report = page.getByTestId("kb-import-report");
    await expect(report).toContainText("Created 2 pages", { timeout: 30_000 });
    await expect(report).toContainText("1 file that is not a page was skipped");
    await expect(report).toContainText("2 links now point to an imported page");
    expect(kb.made.map((p) => [p.id, p.title, p.parent_id])).toEqual([
      ["new-1", "Handbook", null],
      ["new-2", "Leave", "new-1"],
    ]);
    expect(kb.patches).toEqual([
      { id: "new-1", body: { body_md: "See [leave](/knowledge/pages/new-2)." } },
      { id: "new-2", body: { body_md: "Back to [the handbook](/knowledge/pages/new-1)." } },
    ]);
  });

  test("an HTML file goes through the editor schema, with a script and an onerror removed", async ({ page }) => {
    const kb = await setup(page);
    const html = `<!doctype html><html><head><title>Pricing</title></head><body>
      <h1>Pricing</h1><h2>Plans</h2>
      <ul><li>Core</li><li>Pro</li></ul>
      <table><thead><tr><th>Plan</th><th>Cost</th></tr></thead><tbody><tr><td>Core</td><td>500</td></tr></tbody></table>
      <p>See <a href="https://example.com/p">the site</a>.</p>
      <script>alert("x")</script>
      <img src="x.png" alt="Chart" onerror="alert(1)">
      <p onclick="alert(2)">Safe <b>text</b></p></body></html>`;
    await openSpaceMenu(page, "Import pages");
    await input(page).setInputFiles(file("pricing.html", html));
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByTestId("kb-import-report")).toContainText("Created 1 page", { timeout: 30_000 });
    expect(kb.made).toHaveLength(1);
    const body = kb.made[0].body_md;
    expect(kb.made[0].title).toBe("Pricing");
    expect(body).toContain("## Plans");
    expect(body).toMatch(/^- Core$/m);
    expect(body).toContain("| Plan | Cost |");
    expect(body).toContain("[the site](https://example.com/p)");
    expect(body).toContain("*Chart*");
    expect(body).toContain("Safe **text**");
    expect(body).not.toMatch(/script|alert|onerror|onclick/i);
    expect(body).not.toContain("# Pricing");
  });

  test("a zip over the entry limit is refused and nothing is made", async ({ page }) => {
    const kb = await setup(page);
    const files: Record<string, Uint8Array> = {};
    for (let i = 0; i < 501; i++) files[`p${i}.md`] = strToU8(`# P${i}`);
    await openSpaceMenu(page, "Import pages");
    await input(page).setInputFiles({ name: "big.zip", mimeType: "application/zip", buffer: Buffer.from(zipSync(files)) });
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("more than 500 entries");
    await expect(page.getByRole("alert")).toContainText("Nothing was imported");
    expect(kb.made).toHaveLength(0);
  });

  test("a zip with a path that leaves its folder is refused", async ({ page }) => {
    const kb = await setup(page);
    await openSpaceMenu(page, "Import pages");
    await input(page).setInputFiles({
      name: "evil.zip", mimeType: "application/zip", buffer: Buffer.from(zipSync({ "../evil.md": strToU8("# Evil") })),
    });
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("leaves its folder");
    expect(kb.made).toHaveLength(0);
  });

  test("an import from a page's menu puts the pages under that page", async ({ page }) => {
    const kb = await setup(page);
    await page.goto("/knowledge/pages/kp-home");
    await page.getByRole("button", { name: "Page actions" }).click({ timeout: 30_000 });
    await page.getByRole("menu").getByText("Import into this page", { exact: true }).click();
    await input(page).setInputFiles(file("child.md", "# Child\n\nText"));
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByTestId("kb-import-report")).toContainText("Created 1 page", { timeout: 30_000 });
    expect(kb.made.map((p) => [p.title, p.parent_id])).toEqual([["Child", "kp-home"]]);
  });

  test("a reader sees no Import", async ({ page }) => {
    await setup(page, { canWrite: false });
    await page.goto(`/knowledge?space=${SPACE_ID}`);
    await page.getByRole("button", { name: "Space actions" }).click({ timeout: 30_000 });
    await expect(page.getByRole("menu").getByText("Export space", { exact: true })).toBeVisible();
    await expect(page.getByRole("menu").getByText("Import pages", { exact: true })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await page.goto("/knowledge/pages/kp-home");
    await page.getByRole("button", { name: "Page actions" }).click({ timeout: 30_000 });
    await expect(page.getByRole("menu").getByText("Export page", { exact: true })).toBeVisible();
    await expect(page.getByRole("menu").getByText("Import into this page", { exact: true })).toHaveCount(0);
  });
});

// ── Documents as pages (WS-61 KB-AI-1) ──────────────────────────────────────
//
// Every file below is built here, in the test: a DOCX, a PPTX and an XLSX are
// zips of XML (`fflate`), and the PDF is a hand-written minimal PDF. No binary
// fixture is in the repo.

const xml = (body: string) => `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>${body}`;
const W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"';
const A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"';

function zipBuffer(parts: Record<string, string>): Buffer {
  const tree: Record<string, Uint8Array> = {};
  for (const [name, text] of Object.entries(parts)) tree[name] = strToU8(text);
  return Buffer.from(zipSync(tree));
}

const wp = (text: string, style?: string, num?: boolean) =>
  `<w:p><w:pPr>${style ? `<w:pStyle w:val="${style}"/>` : ""}${num ? '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' : ""}</w:pPr><w:r><w:t>${text}</w:t></w:r></w:p>`;

function docx(): Buffer {
  const body = [
    wp("Travel policy", "Heading1"),
    wp("Book early.", undefined),
    wp("Allowed costs", "Heading2"),
    wp("Flights", undefined, true),
    wp("Hotels", undefined, true),
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Limit</w:t></w:r></w:p></w:tc></w:tr>' +
      '<w:tr><w:tc><w:p><w:r><w:t>Meal</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>40</w:t></w:r></w:p></w:tc></w:tr></w:tbl>',
  ].join("");
  return zipBuffer({
    "[Content_Types].xml": xml(
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>' +
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/></Types>',
    ),
    "_rels/.rels": xml(
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
    ),
    "word/_rels/document.xml.rels": xml(
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' +
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>',
    ),
    "word/styles.xml": xml(
      `<w:styles ${W}><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style><w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style></w:styles>`,
    ),
    "word/numbering.xml": xml(
      `<w:numbering ${W}><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="-"/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>`,
    ),
    "word/document.xml": xml(`<w:document ${W}><w:body>${body}</w:body></w:document>`),
  });
}

const ap = (text: string, lvl?: number) => `<a:p>${lvl ? `<a:pPr lvl="${lvl}"/>` : ""}<a:r><a:t>${text}</a:t></a:r></a:p>`;
const asp = (ph: string | null, ...paras: string[]) =>
  `<p:sp><p:nvSpPr><p:cNvPr id="1" name="s"/><p:cNvSpPr/><p:nvPr>${ph ? `<p:ph type="${ph}"/>` : ""}</p:nvPr></p:nvSpPr><p:txBody><a:bodyPr/>${paras.join("")}</p:txBody></p:sp>`;
const slideXml = (...shapes: string[]) => xml(`<p:sld ${A}><p:cSld><p:spTree>${shapes.join("")}</p:spTree></p:cSld></p:sld>`);

function pptx(): Buffer {
  return zipBuffer({
    "[Content_Types].xml": xml('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'),
    "ppt/slides/slide1.xml": slideXml(asp("title", ap("Roadmap")), asp("body", ap("Ship search"), ap("Tune ranking", 1))),
    "ppt/slides/slide2.xml": slideXml(asp("title", ap("Risks")), asp(null, ap("Budget"))),
    "ppt/slides/_rels/slide2.xml.rels": xml(
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>',
    ),
    "ppt/notesSlides/notesSlide1.xml": xml(`<p:notes ${A}><p:cSld><p:spTree>${asp("body", ap("Mention the vendor"))}</p:spTree></p:cSld></p:notes>`),
  });
}

function xlsx(): Buffer {
  const S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
  return zipBuffer({
    "[Content_Types].xml": xml('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'),
    "xl/workbook.xml": xml(`<workbook ${S}><sheets><sheet name="Prices" sheetId="1" r:id="rId1"/><sheet name="Empty" sheetId="2" r:id="rId2"/></sheets></workbook>`),
    "xl/_rels/workbook.xml.rels": xml(
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="x/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="x/worksheet" Target="worksheets/sheet2.xml"/></Relationships>',
    ),
    "xl/sharedStrings.xml": xml(`<sst ${S}><si><t>Plan</t></si><si><t>Cost</t></si><si><t>Core</t></si></sst>`),
    "xl/worksheets/sheet1.xml": xml(
      `<worksheet ${S}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>500</v></c></row></sheetData></worksheet>`,
    ),
    "xl/worksheets/sheet2.xml": xml(`<worksheet ${S}><sheetData/></worksheet>`),
  });
}

/** A minimal one-page PDF. `text` lines are drawn in Helvetica at the given sizes, or none for a scan. */
function pdf(lines: Array<{ text: string; size: number; y: number }>): Buffer {
  const content = lines.map((l) => `BT /F1 ${l.size} Tf 72 ${l.y} Td (${l.text}) Tj ET`).join("\n");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((o, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) out += `${String(off).padStart(10, "0")} 00000 n \n`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

const binary = (name: string, buffer: Buffer) => ({ name, mimeType: "application/octet-stream", buffer });

async function runImportOf(page: Page, files: ReturnType<typeof binary> | ReturnType<typeof binary>[]) {
  await openSpaceMenu(page, "Import pages");
  await expect(page.getByRole("dialog", { name: "Import pages" })).toContainText("Markdown, HTML, Word, PDF, PowerPoint, Excel, or a zip of them");
  await input(page).setInputFiles(files);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  const report = page.getByTestId("kb-import-report");
  await expect(report).toContainText(/Import (finished|stopped)/, { timeout: 60_000 });
  return report;
}

test.describe("Knowledge import of documents", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test("a Word file keeps its title, heading, list and table", async ({ page }) => {
    const kb = await setup(page);
    const report = await runImportOf(page, binary("Travel.docx", docx()));
    await expect(report).toContainText("Created 1 page as a draft");
    expect(kb.made).toHaveLength(1);
    expect(kb.made[0].title).toBe("Travel policy");
    const body = kb.made[0].body_md;
    expect(body).toContain("Book early.");
    expect(body).toContain("## Allowed costs");
    expect(body).toMatch(/^- Flights$/m);
    expect(body).toMatch(/^- Hotels$/m);
    expect(body).toContain("| Item | Limit |");
    expect(body).toContain("| Meal | 40 |");
    expect(body).not.toContain("# Travel policy");
  });

  test("a PowerPoint file is one page with a section for each slide and the notes", async ({ page }) => {
    const kb = await setup(page);
    const report = await runImportOf(page, binary("Plan.pptx", pptx()));
    await expect(report).toContainText("Created 1 page as a draft");
    expect(kb.made[0].title).toBe("Plan");
    const body = kb.made[0].body_md;
    expect(body).toContain("## Slide 1: Roadmap");
    expect(body).toMatch(/^- Ship search$/m);
    expect(body).toMatch(/^ {2}- Tune ranking$/m);
    expect(body).toContain("## Slide 2: Risks");
    expect(body).toContain("**Notes**");
    expect(body).toContain("Mention the vendor");
  });

  test("an Excel file is one page with a table for each sheet, and the report says dates stay numbers", async ({ page }) => {
    const kb = await setup(page);
    const report = await runImportOf(page, binary("Prices.xlsx", xlsx()));
    await expect(report).toContainText("Created 1 page as a draft");
    await expect(report).toContainText("Dates in Excel files stay as the numbers that Excel stores");
    const body = kb.made[0].body_md;
    expect(body).toContain("## Prices");
    expect(body).toContain("| Plan | Cost |");
    expect(body).toContain("| Core | 500 |");
    expect(body).toContain("## Empty");
    expect(body).toContain("This sheet is empty");
  });

  test("a PDF with a text layer keeps its text and its heading", async ({ page }) => {
    const kb = await setup(page);
    const file = pdf([
      { text: "Quarterly Report", size: 24, y: 720 },
      { text: "Revenue grew in the third quarter of the year.", size: 12, y: 680 },
      { text: "Costs stayed flat across every region we serve.", size: 12, y: 664 },
    ]);
    const report = await runImportOf(page, binary("Report.pdf", file));
    await expect(report).toContainText("Created 1 page as a draft");
    expect(kb.made[0].title).toBe("Quarterly Report");
    expect(kb.made[0].body_md).toContain("Revenue grew in the third quarter of the year. Costs stayed flat across every region we serve.");
  });

  test("a PDF with no text layer reports a scan and makes no page", async ({ page }) => {
    const kb = await setup(page);
    const report = await runImportOf(page, binary("Scan.pdf", pdf([])));
    await expect(report).toContainText("Created 0 pages");
    await expect(page.getByTestId("kb-import-unreadable")).toContainText("Scan.pdf");
    await expect(page.getByTestId("kb-import-unreadable")).toContainText("This PDF has no text layer (a scan). OCR is not available yet");
    expect(kb.made).toHaveLength(0);
  });

  test("one bad file is reported and the others still import, also inside a zip", async ({ page }) => {
    const kb = await setup(page);
    const zip = Buffer.from(zipSync({ "docs/Prices.xlsx": new Uint8Array(xlsx()), "docs/broken.docx": strToU8("not a zip at all") }));
    const report = await runImportOf(page, [binary("Pack.zip", zip), file("note.md", "# Note\n\nHi")]);
    await expect(report).toContainText("Created 3 pages");
    await expect(page.getByTestId("kb-import-unreadable")).toContainText("broken.docx");
    expect(kb.made.map((p) => p.title).sort()).toEqual(["Note", "Prices", "docs"]);
  });
});

test.describe("Knowledge export", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  async function exportPage(page: Page, format: "Markdown (.md)" | "HTML (.html)", withSub = true) {
    await page.goto("/knowledge/pages/kp-home");
    await page.getByRole("button", { name: "Page actions" }).click({ timeout: 30_000 });
    await page.getByRole("menu").getByText("Export page", { exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Export page" });
    await expect(dialog).toContainText("Attachments and images are not in the file yet");
    if (format !== "Markdown (.md)") {
      await dialog.getByRole("button", { name: /Format/ }).click();
      await page.getByRole("option", { name: format }).click();
    }
    if (!withSub) await dialog.getByRole("checkbox", { name: "With sub-pages" }).uncheck();
    const download = page.waitForEvent("download");
    await dialog.getByRole("button", { name: "Export", exact: true }).click();
    return download;
  }

  test("a page with a sub-page downloads as a zip with the right names and contents", async ({ page }) => {
    await setup(page);
    const download = await exportPage(page, "Markdown (.md)");
    expect(download.suggestedFilename()).toBe("Home page.zip");
    const files = unzipSync(new Uint8Array(readFileSync((await download.path()) as string)));
    expect(Object.keys(files).sort()).toEqual(["Home page.md", "Home page/Sub page.md"]);
    expect(strFromU8(files["Home page.md"])).toBe(
      "# Home page\n\nWelcome. See [the sub-page](Home%20page/Sub%20page.md) and [away](/knowledge/pages/kp-away).\n",
    );
    expect(strFromU8(files["Home page/Sub page.md"])).toBe("# Sub page\n\nBack to [home](../Home%20page.md).\n\n- one\n- two\n");
  });

  test("a page without its sub-pages downloads as one file", async ({ page }) => {
    await setup(page);
    const download = await exportPage(page, "Markdown (.md)", false);
    expect(download.suggestedFilename()).toBe("Home page.md");
    expect(readFileSync((await download.path()) as string, "utf8")).toContain("# Home page");
  });

  test("HTML export is a document with a title and no script", async ({ page }) => {
    await setup(page);
    const download = await exportPage(page, "HTML (.html)", false);
    expect(download.suggestedFilename()).toBe("Home page.html");
    const html = readFileSync((await download.path()) as string, "utf8");
    expect(html).toContain("<title>Home page</title>");
    expect(html).toContain("<h1>Home page</h1>");
    expect(html.toLowerCase()).not.toContain("<script");
    expect(html).toContain("/knowledge/pages/kp-sub");
  });

  test("the space exports as a zip of its tree", async ({ page }) => {
    await setup(page);
    await openSpaceMenu(page, "Export space");
    const dialog = page.getByRole("dialog", { name: "Export space" });
    const download = page.waitForEvent("download");
    await dialog.getByRole("button", { name: "Export", exact: true }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("Company.zip");
    const files = unzipSync(new Uint8Array(readFileSync((await file.path()) as string)));
    expect(Object.keys(files).sort()).toEqual(["Home page.md", "Home page/Sub page.md"]);
  });

  test("an export then an import gives the same tree", async ({ page }) => {
    const kb = await setup(page);
    const download = await exportPage(page, "Markdown (.md)");
    const zip = readFileSync((await (await download).path()) as string);
    await page.keyboard.press("Escape");
    await openSpaceMenu(page, "Import pages");
    await input(page).setInputFiles({ name: "Home page.zip", mimeType: "application/zip", buffer: zip });
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByTestId("kb-import-report")).toContainText("Created 2 pages", { timeout: 30_000 });
    expect(kb.made.map((p) => [p.title, p.parent_id])).toEqual([
      ["Home page", null],
      ["Sub page", "new-1"],
    ]);
    expect(kb.made[1].body_md).toBe("Back to [home](/knowledge/pages/new-1).\n\n- one\n- two");
  });
});
