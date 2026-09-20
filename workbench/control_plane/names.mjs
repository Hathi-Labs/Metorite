import { chromium } from "@playwright/test";
const NL = String.fromCharCode(10);
const log = (...a) => console.log(...a);
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1700, height: 1000 } });
p.on("response", (r) => { if (r.url().includes("people/names")) log("   [net]", r.status(), r.url().split("/api/")[1]); });
await p.goto("http://localhost:3001/projects", { waitUntil: "domcontentloaded" });
await p.waitForTimeout(11000);
const c = p.getByRole("button", { name: /Metorite/ });
for (let i = 0; i < (await c.count()); i++) {
  const x = c.nth(i);
  if (!(await x.isVisible())) continue;
  const box = await x.boundingBox();
  if (box && box.x < 420 && (await x.innerText()).split(NL).join(" ").trim() === "Metorite") { await x.click(); break; }
}
await p.waitForTimeout(7000);
const n = await p.locator("[data-card]").count();
log("cards:", n);
for (let i = 0; i < n; i++) log("  " + (await p.locator("[data-card]").nth(i).innerText()).split(NL).join(" | "));
log(NL + "assignee filter options:");
// open a task panel, where the full address used to be printed
await p.locator('[data-card]').filter({ hasText: 'If a tag already' }).first().click();
await p.waitForTimeout(3500);
const panel = await p.locator('body').innerText();
const shown = panel.split(NL).filter((l) => /Priya|Ada|nobody|@fracktal|@elsewhere/.test(l));
log('panel mentions of people:'); shown.slice(0, 6).forEach((l) => log('   ' + l.trim()));
await p.waitForTimeout(1500);

await p.screenshot({ path: "names.png" });
await b.close();
