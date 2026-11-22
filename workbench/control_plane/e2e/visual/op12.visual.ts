import { test, type Page } from "@playwright/test";
import fs from "node:fs";
import { captureContexts } from "./harness";
const OUT = "ux-shots/op12";
const P = "55457274-0be6-416f-9f3b-86f2277981a3";
const log: string[] = [];
const note = (s: string) => { log.push(s); console.log(s); };
async function settle(page: Page, ms = 5000) {
  await page.waitForTimeout(ms);
  const skip = page.getByRole("button", { name: "Skip for now" }).first();
  if (await skip.isVisible().catch(() => false)) { await skip.click(); await page.waitForTimeout(800); }
}
async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ([m, p, b]) => {
    const r = await fetch(`/api/projects/${p}`, { method: m as string, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined });
    const t = await r.text(); let j: any = null; try { j = JSON.parse(t); } catch { j = t; }
    return { status: r.status, body: j };
  }, [method, path, body] as const);
}
async function rowInfo(page: Page) {
  return page.evaluate(() => {
    const el = document.querySelector("[data-view-tabs]") as HTMLElement | null;
    if (!el) return "no [data-view-tabs]";
    return `scrollW=${el.scrollWidth} clientW=${el.clientWidth} tabs=${el.querySelectorAll("button,[role=tab]").length} text=${el.innerText.replace(/\n/g, " | ").slice(0, 200)}`;
  });
}
test("op12", async ({ page }) => {
  test.setTimeout(280_000);
  page.setDefaultTimeout(25000);
  fs.mkdirSync(OUT, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/projects"); await settle(page);
  const names = ["Overdue this week", "Client review", "Blocked work", "Design tasks", "Site visits", "Procurement"];
  // Create only the views that are missing, so a rerun does not add copies.
  const have = await api(page, "GET", `nodes/${P}/views`);
  const list: any[] = Array.isArray(have.body) ? have.body : (have.body?.rows ?? have.body?.views ?? []);
  const existing = new Set(list.map((v: any) => v.name));
  note(`views before: ${list.length}`);
  for (const [i, n] of names.entries()) {
    if (existing.has(n)) continue;
    const r = await api(page, "POST", `nodes/${P}/views`, { name: n, view_type: i % 2 ? "board" : "list", config: {} });
    note(`view ${n}: ${r.status}`);
  }
  await page.goto(`/projects?project=${P}`); await settle(page, 6000);
  // The tabs are buttons with aria-pressed, not role=tab.
  const boardTab = page.locator("[data-view-tabs]").getByRole("button", { name: "Board" }).first();
  note("board tab visible: " + (await boardTab.isVisible().catch(() => false)));
  note("1440 row: " + (await rowInfo(page)));
  await captureContexts(page, "tabs", { out: OUT, only: ["dark-1440", "light-1440", "compact-1440", "accent-violet-1440"] });
  for (const w of [1024, 768]) {
    await page.setViewportSize({ width: w, height: 900 }); await page.waitForTimeout(1200);
    note(`${w} row: ` + (await rowInfo(page)));
    await page.screenshot({ path: `${OUT}/tabs-${w}.png` });
  }
  await page.setViewportSize({ width: 1024, height: 900 }); await page.waitForTimeout(800);
  const more = page.locator("[data-view-tabs]").getByRole("button", { name: "More views" });
  if (await more.isVisible().catch(() => false)) { await more.click(); await page.waitForTimeout(600); await page.screenshot({ path: `${OUT}/more-open-1024.png` }); await page.keyboard.press("Escape"); }
  await page.setViewportSize({ width: 1440, height: 900 }); await page.waitForTimeout(800);
  await page.keyboard.press("b"); await page.waitForTimeout(1500);
  note("after key b url: " + page.url().slice(-60));
  await page.keyboard.press("l"); await page.waitForTimeout(1500);
  note("after key l url: " + page.url().slice(-60));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/projects?project=${P}`); await settle(page, 6000);
  await page.screenshot({ path: `${OUT}/phone.png` });
  note("phone row present: " + (await page.evaluate(() => !!document.querySelector("[data-view-tabs]"))) + " sideways=" + JSON.stringify(await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth])));
  fs.writeFileSync(`${OUT}/log.txt`, log.join("\n"));
});
