// Capture the operator console's surfaces. H-122: it has never had a rig.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const OUT = process.argv[2];
const BASE = "http://localhost:3102";
mkdirSync(OUT, { recursive: true });

// The console's own contexts. 1440 is the width that matters most: a row that
// wraps there fits at 1920 and is already broken at 1280.
const CONTEXTS = [
  { name: "dark-1440", w: 1440, light: false },
  { name: "light-1440", w: 1440, light: true },
  { name: "mobile-390", w: 390, light: false },
];

const ROUTES = process.env.ROUTES
  ? process.env.ROUTES.split(",")
  : ["/tiers", "/pricing", "/models", "/providers", "/", "/activity"];

const browser = await chromium.launch();
const errors = [];

for (const ctx of CONTEXTS) {
  const c = await browser.newContext({
    viewport: { width: ctx.w, height: 1200 },
    deviceScaleFactor: 1,
  });
  // The interim door: the staff secret, straight into the cookie jar.
  await c.addCookies([
    { name: "operator_staff", value: "rig-secret", url: BASE },
  ]);
  const page = await c.newPage();
  page.on("pageerror", (e) => errors.push(`${ctx.name} PAGEERROR ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`${ctx.name} CONSOLE ${m.text().slice(0, 200)}`);
  });

  for (const route of ROUTES) {
    try {
      await page.goto(BASE + route, { waitUntil: "domcontentloaded", timeout: 60000 });
      if (ctx.light) {
        await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
      }
      await page.waitForTimeout(2500);
      const slug = route === "/" ? "home" : route.replace(/\//g, "_").replace(/^_/, "");
      await page.screenshot({
        path: `${OUT}/${slug}__${ctx.name}.png`,
        fullPage: true,
      });
      console.log("shot", slug, ctx.name);
    } catch (e) {
      console.log("FAILED", route, ctx.name, String(e).slice(0, 160));
    }
  }
  await c.close();
}
await browser.close();
if (errors.length) {
  console.log("\n=== page errors ===");
  for (const e of [...new Set(errors)].slice(0, 20)) console.log(" ", e);
}
