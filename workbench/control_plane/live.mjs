import { chromium } from "@playwright/test";
export const BASE = "http://localhost:3423";
export async function open(width = 1440, height = 900) {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.addInitScript(() => { try { localStorage.setItem("cc-shell-nav", "1"); } catch {} });
  return { browser, page };
}
export async function newChat(page, thread) {
  await page.goto(BASE + "/chat");
  await page.waitForTimeout(6000);
  const skip = page.getByText("Skip for now");
  if (await skip.isVisible()) await skip.click();
  const no = page.getByRole("button", { name: "No thanks" });
  if (await no.isVisible()) await no.click();
  const box = page.getByRole("textbox", { name: /^Message / });
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

// The dev stack has no login session, and /api/agent/respond-input refuses with 401 (it reads auth(), not the dev identity).
// This sends the same body to the gateway as the dev member, so the card approves.
export async function forwardApprove(page) {
  await page.route("**/api/agent/respond-input", async (route) => {
    const r = await fetch("http://127.0.0.1:8423/agent/respond-input", { method: "POST",
      headers: { "content-type": "application/json", Authorization: "Bearer dev-27931666b90babed9037f960844e73b4", "X-User-Email": "dev@fracktal.in", "X-User-Role": "executive" },
      body: route.request().postData() });
    await route.fulfill({ status: r.status, contentType: "application/json", body: await r.text() });
  });
}
