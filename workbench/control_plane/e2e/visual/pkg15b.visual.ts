import { test, type Page } from "@playwright/test";
import fs from "node:fs";
import { CONTEXTS, SAFE_EMPTY, capture, gotoAndSettle, stubApi, watchErrors } from "./harness";

const OUT = "/home/dev/pkg15b-shots";
const SYS = [
  { key: "/chat", source: "system", install_id: null, needs_setup: false, package: "chat" },
  { key: "/crm", source: "system", install_id: null, needs_setup: false, package: "crm" },
  { key: "/projects", source: "system", install_id: null, needs_setup: false, package: "projects" },
];
const SUB = (label = "Installations", extra: object = {}) => ({
  key: "installations", source: "org", install_id: "i-1", needs_setup: false,
  href: "/installations", label, icon: "Box", extends: "crm", ...extra,
});
const FEATS = ["tasks", "projects", "chat", "people", "crm", "email", "calendar", "agents"];
const me = (features: string[]) => ({
  authenticated: true, email: "member@example.com", is_admin: false, features,
  permissions: [], roles: ["employee"], organization: { id: "org1", slug: "acme", display_name: "Acme" },
});
const man = (nav: unknown[]) => ({ version: "v1", contributions: { nav } });

async function setup(page: Page, manifest: unknown, features: string[]) {
  await stubApi(page, { "auth/me": me(features), "shell/manifest": manifest });
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.setItem("cc-shell-chat", "1");
  });
}
const ctxs = ["dark-1440", "light-1440", "compact-1440", "accent-violet-1440", "mobile-390"];

async function shoot(page: Page, name: string, only = ctxs) {
  const log: string[] = [];
  for (const c of CONTEXTS.filter((x) => only.includes(x.name))) {
    await page.setViewportSize({ width: c.width, height: c.height });
    if (c.apply) await c.apply(page);
    if (c.name === "mobile-390") {
      const m = page.getByRole("button", { name: /menu|navigation/i });
      for (let i = 0; i < (await m.count()); i++) if (await m.nth(i).isVisible()) { await m.nth(i).click(); break; }
      await page.waitForTimeout(600);
    }
    // measurements
    const meas = await page.evaluate(() => {
      const r = (t: string) => {
        const a = [...document.querySelectorAll("a")].filter((e) => e.textContent?.trim() === t && (e as HTMLElement).offsetParent)[0] as HTMLElement | undefined;
        if (!a) return null;
        const b = a.getBoundingClientRect();
        const ic = a.querySelector("svg")?.getBoundingClientRect();
        const lab = [...a.querySelectorAll("span")].find((s) => s.textContent?.trim() === t)?.getBoundingClientRect();
        const sub = a.closest("[data-nav-sub]") as HTMLElement | null;
        const cs = sub ? getComputedStyle(sub) : null;
        return { x: b.x, y: b.y, h: b.height, w: b.width, iconX: ic?.x, labelX: lab?.x, bl: cs?.borderLeftWidth + " " + cs?.borderLeftColor, sub: !!sub, cls: a.className.slice(0, 60) };
      };
      return { crm: r("CRM"), inst: r("Installations") };
    });
    log.push(`${c.name} ${JSON.stringify(meas)}`);
    await capture(page, `${name}--${c.name}`, OUT);
    if (c.name === "mobile-390") { await page.keyboard.press("Escape"); await page.waitForTimeout(300); }
    if (c.undo) await c.undo(page);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  fs.appendFileSync(OUT + "/measure.txt", `## ${name}\n` + log.join("\n") + "\n");
}

test("A host present", async ({ page }) => {
  const errs = watchErrors(page);
  await setup(page, man([...SYS, SUB()]), FEATS);
  await gotoAndSettle(page, "/tasks", 6000);
  await shoot(page, "A-host-present");
  fs.appendFileSync(OUT + "/measure.txt", "errors A: " + errs.join(" | ") + "\n");
});
test("B host missing", async ({ page }) => {
  await setup(page, man([SYS[0], SYS[2], SUB()]), FEATS);
  await gotoAndSettle(page, "/tasks", 6000);
  await shoot(page, "B1-no-crm-entry", ["dark-1440", "light-1440", "mobile-390"]);
});
test("B2 no crm feature", async ({ page }) => {
  await setup(page, man([...SYS, SUB()]), FEATS.filter((f) => f !== "crm"));
  await gotoAndSettle(page, "/tasks", 6000);
  await shoot(page, "B2-no-crm-feature", ["dark-1440", "light-1440"]);
});
test("C on installations", async ({ page }) => {
  await setup(page, man([...SYS, SUB()]), FEATS);
  await gotoAndSettle(page, "/installations", 6000);
  await shoot(page, "C-on-sub", ["dark-1440", "light-1440", "compact-1440", "accent-violet-1440"]);
});
test("D on crm", async ({ page }) => {
  await setup(page, man([...SYS, SUB()]), FEATS);
  await gotoAndSettle(page, "/crm", 6000);
  await shoot(page, "D-on-crm", ["dark-1440", "light-1440", "compact-1440", "accent-violet-1440"]);
});
test("E empty manifest", async ({ page }) => {
  await setup(page, { version: "", contributions: {} }, FEATS);
  await gotoAndSettle(page, "/tasks", 6000);
  await shoot(page, "E-empty", ["dark-1440", "light-1440"]);
});
test("F long label + tab + rail", async ({ page }) => {
  await setup(page, man([...SYS, SUB("Installations of the regional field service partners")]), FEATS);
  await gotoAndSettle(page, "/tasks", 6000);
  await shoot(page, "F-long", ["dark-1440", "light-1440", "compact-1440"]);
  // Tab walk
  await page.setViewportSize({ width: 1440, height: 900 });
  const crm = page.locator("a", { hasText: /^CRM$/ }).first();
  await crm.focus();
  await page.keyboard.press("Tab"); // may land on row menu
  const seq: string[] = [];
  await crm.focus();
  for (let i = 0; i < 5; i++) {
    seq.push(await page.evaluate(() => { const a = document.activeElement as HTMLElement; return (a.tagName + " " + (a.getAttribute("aria-label") || a.textContent || "").trim().slice(0, 40)); }));
    await capture(page, `F-tab-${i}--dark-1440`, OUT);
    await page.keyboard.press("Tab");
  }
  fs.appendFileSync(OUT + "/measure.txt", "tab order: " + seq.join(" -> ") + "\n");
  // light-mode tab on sub
  await page.evaluate(() => document.documentElement.classList.add("light"));
  const sub = page.locator("[data-nav-sub] a").first();
  await sub.focus(); await page.keyboard.press("Shift+Tab"); await page.keyboard.press("Tab");
  await capture(page, "F-tab-sub-focus--light-1440", OUT);
  await page.evaluate(() => document.documentElement.classList.remove("light"));
  // collapsed rail
  const col = page.getByRole("button", { name: /collapse|expand|rail|sidebar/i });
  const n = await col.count();
  fs.appendFileSync(OUT + "/measure.txt", "collapse buttons: " + n + "\n");
  for (let i = 0; i < n; i++) if (await col.nth(i).isVisible()) { await col.nth(i).click(); await page.waitForTimeout(700); await capture(page, "G-rail--dark-1440", OUT); break; }
});
