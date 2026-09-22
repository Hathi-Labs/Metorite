import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
const OUT = process.argv[2]; const BASE = "http://localhost:3102";
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();

async function shot(route, clicks, name, w = 1440, light = false) {
  const c = await browser.newContext({ viewport: { width: w, height: 1200 } });
  await c.addCookies([{ name: "operator_staff", value: "rig-secret", url: BASE }]);
  const p = await c.newPage();
  await p.goto(BASE + route, { waitUntil: "domcontentloaded", timeout: 60000 });
  if (light) await p.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await p.waitForTimeout(2000);
  for (const label of clicks) {
    const el = p.getByRole("button", { name: label, exact: false }).first();
    try { await el.click({ timeout: 8000 }); await p.waitForTimeout(1200); }
    catch (e) { console.log("  click failed:", label, String(e).slice(0, 90)); }
  }
  await p.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log("shot", name);
  await c.close();
}

await shot("/pricing", ["Change the credit price"], "open_creditprice");
await shot("/pricing", ["Set the price"], "open_setprice");
await shot("/pricing", ["Set the margin"], "open_setmargin");
await shot("/tiers", ["Add a backup"], "open_addbackup");
await shot("/pricing", ["Set the price"], "open_setprice_light", 1440, true);
await browser.close();
