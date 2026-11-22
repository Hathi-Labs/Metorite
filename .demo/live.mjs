import { chromium } from "@playwright/test";
export const BASE = "http://localhost:3423";
export async function open(width = 1440, height = 900) {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.addInitScript(() => { try { localStorage.setItem("cc-shell-nav", "1"); } catch {} });
  return { browser, page };
}
export async function newChat(page) {
  await page.goto(BASE + "/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await picker.or(box).first().waitFor({ timeout: 60000 });
  if (await picker.isVisible()) await page.getByText("General-purpose AI company brain", { exact: false }).first().click();
  await box.waitFor({ state: "visible", timeout: 30000 });
  return box;
}
export async function send(page, box, text) {
  await box.fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
}
export async function shot(page, path) { await page.screenshot({ path }); }
export async function both(page, base) { // dark then light
  await page.screenshot({ path: `${base}-dark.png` });
  await page.evaluate(() => document.documentElement.classList.add("light"));
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${base}-light.png` });
  await page.evaluate(() => document.documentElement.classList.remove("light"));
}
