import { test } from "@playwright/test";
const P = "55457274-0be6-416f-9f3b-86f2277981a3";
test("op12c", async ({ page }) => {
  test.setTimeout(100_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/projects?project=${P}`); await page.waitForTimeout(6000);
  const skip = page.getByRole("button", { name: "Skip for now" }).first();
  if (await skip.isVisible().catch(() => false)) { await skip.click(); await page.waitForTimeout(800); }
  const st = () => page.evaluate(() => [...document.querySelectorAll("[data-view-tabs] button[aria-pressed=true]")].map(x => x.getAttribute("aria-label")||x.textContent).join(",") + " url=" + location.search.slice(0,80) + " active=" + document.activeElement?.tagName);
  console.log("start " + await st());
  for (const k of ["t", "b", "l", "c", "o"]) { await page.keyboard.press("v"); await page.keyboard.press(k); await page.waitForTimeout(1200); console.log(`key ${k}: ` + await st()); }
  console.log("views: " + JSON.stringify(await page.evaluate(async () => (await (await fetch("/api/projects/nodes/55457274-0be6-416f-9f3b-86f2277981a3/views")).json()).map?.((v:any)=>v.name))));
});
