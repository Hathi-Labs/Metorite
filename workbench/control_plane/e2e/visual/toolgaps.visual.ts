import { test } from "@playwright/test";
import { capture, captureContexts, gotoAndSettle, stubApi, watchErrors, writeNotes } from "./harness";

const OUT = process.env.UX_OUT || "ux-shots/toolgaps";
const FULL = {
  days: 30,
  counts: { by_outcome: { no_match: 7, compose: 3, reuse: 2, existing: 1 }, by_category: { finance: 6, crm: 4, files: 3 } },
  gaps: [
    { need: "Convert the quote total to INR at today's rate", category: "finance", outcome: "no_match", created_at: "2026-11-25T10:00:00+00:00" },
    { need: "Merge two duplicate leads and keep the newest phone number", category: "crm", outcome: "compose", created_at: "2026-11-24T09:00:00+00:00" },
    { need: "<b>Read</b> a PDF invoice https://example.com/a/very/long/path/that/should/wrap/and/not/widen/the/page/at/all", category: "files", outcome: "reuse", created_at: "2026-11-20T09:00:00+00:00" },
  ],
};
const BASE = { "auth/me": { email: "dev@fracktal.in", name: "Dev" }, "studio/skills": { skills: [] } };

test("tool gaps, with rows, in every context", async ({ page }) => {
  test.setTimeout(600_000);
  const seen = new Set<string>();
  const errors = watchErrors(page);
  await stubApi(page, { ...BASE, "studio/tool-gaps": FULL }, { seen });
  await gotoAndSettle(page, "/studio", 15000);
  await page.getByRole("region", { name: "Tool gaps" }).scrollIntoViewIfNeeded();
  await captureContexts(page, "rows", { out: OUT });
  writeNotes(OUT, seen, errors);
});

test("tool gaps, the other states", async ({ page }) => {
  test.setTimeout(600_000);
  await stubApi(page, { ...BASE, "studio/tool-gaps": { days: 30, counts: { by_outcome: {}, by_category: {} }, gaps: [] } });
  await gotoAndSettle(page, "/studio", 15000);
  await page.getByRole("region", { name: "Tool gaps" }).scrollIntoViewIfNeeded();
  await capture(page, "empty", OUT);

  await page.route("**/api/studio/tool-gaps", (r) => r.fulfill({ status: 404, json: { detail: "Not found" } }));
  await gotoAndSettle(page, "/studio", 8000);
  await capture(page, "off-404", OUT);

  await page.unroute("**/api/studio/tool-gaps");
  await page.route("**/api/studio/tool-gaps", (r) => r.abort("failed"));
  await gotoAndSettle(page, "/studio", 8000);
  await page.getByRole("region", { name: "Tool gaps" }).scrollIntoViewIfNeeded();
  await capture(page, "error", OUT);
});
