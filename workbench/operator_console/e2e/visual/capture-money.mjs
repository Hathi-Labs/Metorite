// WS-50: the customer money panels, with an Explain opened.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const OUT = process.argv[2];
const BASE = "http://localhost:3102";
const ROUTE = process.argv[3] ?? "/customers/hathi-labs-llp";
mkdirSync(OUT, { recursive: true });

const CONTEXTS = [
  { name: "dark-1440", w: 1440, light: false },
  { name: "light-1440", w: 1440, light: true },
  { name: "mobile-390", w: 390, light: false },
];

const browser = await chromium.launch();
const errors = [];
for (const ctx of CONTEXTS) {
  const c = await browser.newContext({ viewport: { width: ctx.w, height: 1000 } });
  await c.addCookies([{ name: "operator_staff", value: "rig-secret", url: BASE }]);
  const page = await c.newPage();
  page.on("pageerror", (e) => errors.push(`${ctx.name} PAGEERROR ${e.message}`));
  page.on("console", (m) => m.type() === "error" && errors.push(`${ctx.name} CONSOLE ${m.text().slice(0, 200)}`));
  await page.goto(BASE + ROUTE, { waitUntil: "networkidle", timeout: 90000 });
  if (ctx.light) await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await page.waitForTimeout(3000);
  const money = page.locator("section.panel", { hasText: "Money — last" });
  await money.scrollIntoViewIfNeeded();
  await money.screenshot({ path: `${OUT}/money__${ctx.name}.png` });
  const brk = page.locator("section.panel", { hasText: "Where it went" });
  await brk.screenshot({ path: `${OUT}/breakdown__${ctx.name}.png` });
  // Open the "We charged" explanation, by tap on mobile and hover elsewhere.
  const btn = money.getByRole("button", { name: "What is We charged?" });
  if (ctx.w < 600) await btn.click(); else await btn.hover();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/explain__${ctx.name}.png` });
  // And one in the table, at the right edge, where clipping would show.
  await page.mouse.move(0, 0);
  await page.keyboard.press("Escape");
  const th = brk.getByRole("button", { name: "What is Margin?" }).first();
  await th.scrollIntoViewIfNeeded();
  if (ctx.w < 600) await th.click(); else await th.hover();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/explain-table__${ctx.name}.png` });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  console.log(ctx.name, "horizontal overflow:", overflow);
  await c.close();
}
await browser.close();
for (const e of [...new Set(errors)]) console.log(" ", e);
