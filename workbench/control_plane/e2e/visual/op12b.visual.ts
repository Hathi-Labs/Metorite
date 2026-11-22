import { test } from "@playwright/test";
import fs from "node:fs";
const OUT = "ux-shots/op12";
const P = "55457274-0be6-416f-9f3b-86f2277981a3";
const log: string[] = [];
const note = (s: string) => { log.push(s); console.log(s); };
test("op12b", async ({ page }) => {
  test.setTimeout(200_000);
  page.setDefaultTimeout(15000);
  const info = () => page.evaluate(() => {
    const b = document.querySelector("[data-view-tabs]") as HTMLElement | null;
    if (!b) return "none";
    const inner = b.firstElementChild as HTMLElement;
    const rects = [...b.querySelectorAll("button")].map(x => Math.round(x.getBoundingClientRect().top));
    return `box ${b.clientWidth} inner.scrollW ${inner.scrollWidth} innerClient ${inner.clientWidth} tops=${[...new Set(rects)]} names=${[...b.querySelectorAll("button")].map(x => x.getAttribute("aria-label") || x.textContent).join("|")}`;
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/projects?project=${P}`); await page.waitForTimeout(6000);
  const skip = page.getByRole("button", { name: "Skip for now" }).first();
  if (await skip.isVisible().catch(() => false)) { await skip.click(); await page.waitForTimeout(800); }
  note("1440 " + await info());
  // keyboard
  await page.locator("[data-view-tabs] button").first().focus();
  const seen: string[] = [];
  for (let i = 0; i < 14; i++) {
    const d = await page.evaluate(() => { const a = document.activeElement as HTMLElement; const inRow = !!a?.closest("[data-view-tabs]"); const cs = getComputedStyle(a); return `${inRow ? "ROW" : "out"}:${a.getAttribute("aria-label") || a.textContent?.slice(0,20)} outline=${cs.outlineStyle}/${cs.outlineWidth} shadow=${cs.boxShadow.slice(0,40)}`; });
    seen.push(d);
    if (i === 3) await page.screenshot({ path: `${OUT}/focus-ring.png`, clip: { x: 500, y: 80, width: 800, height: 60 } });
    await page.keyboard.press("Tab");
  }
  note("tab walk:\n  " + seen.join("\n  "));
  // More at 1440
  for (const w of [1440, 1024, 768]) {
    await page.setViewportSize({ width: w, height: 900 }); await page.waitForTimeout(1200);
    note(`${w} ` + await info());
    const more = page.locator("[data-view-tabs]").getByRole("button", { name: "More views" });
    note(`${w} more visible ${await more.isVisible().catch(()=>false)}`);
    if (await more.isVisible().catch(() => false)) {
      await more.click(); await page.waitForTimeout(600);
      await page.screenshot({ path: `${OUT}/more-open-${w}.png` });
      note(`${w} menu items: ` + await page.locator("[role=menu]").first().innerText().catch(()=>"?"));
      await page.keyboard.press("Escape"); await page.waitForTimeout(400);
      note(`${w} menu after Esc: ${await page.locator("[role=menu]").count()}`);
    }
    const add = page.locator("[data-view-tabs]").getByRole("button", { name: "New saved view" });
    note(`${w} add visible ${await add.isVisible().catch(()=>false)}`);
  }
  // 768 pick Calendar from More
  const more = page.locator("[data-view-tabs]").getByRole("button", { name: "More views" });
  await more.click(); await page.waitForTimeout(500);
  const cal = page.locator("[role=menu]").getByRole("button", { name: /Calendar/ }).first();
  note("768 calendar in menu " + await cal.isVisible().catch(()=>false));
  if (await cal.isVisible().catch(()=>false)) { await cal.click(); await page.waitForTimeout(1500); await page.screenshot({ path: `${OUT}/768-calendar.png` }); note("768 after calendar " + await info()); }
  await page.setViewportSize({ width: 1440, height: 900 }); await page.waitForTimeout(1000);
  await page.mouse.click(900, 60);
  await page.keyboard.press("b"); await page.waitForTimeout(1200);
  note("pressed b: " + await page.evaluate(() => [...document.querySelectorAll("[data-view-tabs] button[aria-pressed=true]")].map(x => x.getAttribute("aria-label")||x.textContent).join(",")));
  await page.keyboard.press("l"); await page.waitForTimeout(1200);
  note("pressed l: " + await page.evaluate(() => [...document.querySelectorAll("[data-view-tabs] button[aria-pressed=true]")].map(x => x.getAttribute("aria-label")||x.textContent).join(",")));
  // phone
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/projects?project=${P}`); await page.waitForTimeout(6000);
  await page.screenshot({ path: `${OUT}/phone.png` });
  note("phone row " + await page.evaluate(() => !!document.querySelector("[data-view-tabs]")) + " sw=" + await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]));
  fs.writeFileSync(`${OUT}/log.txt`, log.join("\n"));
});
