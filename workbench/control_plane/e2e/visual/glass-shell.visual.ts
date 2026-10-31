import { test } from "@playwright/test";
import { captureContexts, capture, gotoAndSettle, setLook, stubApi, firstVisible } from "./harness";

/** GL-3 throwaway rig. GL3_LOOK=classic|glass, GL3_OUT=<dir>. Not committed. */
const LOOK = (process.env.GL3_LOOK || "glass") as "glass" | "classic";
const OUT = process.env.GL3_OUT || "ux-shots/gl3";
const ROUTES: [string, string][] = [["home", "/"], ["projects", "/projects"], ["email", "/email"], ["appearance", "/settings/appearance"]];
const CTX = LOOK === "glass"
  ? ["light-1440", "compact-1440", "accent-violet-1440", "mobile-390", "dark-1440"]
  : ["dark-1440", "light-1440", "mobile-390"];

for (const [name, route] of ROUTES) {
  test(`shell ${name} ${LOOK}`, async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await stubApi(page, {
      "auth/me": { authenticated: true, email: "admin@example.com", name: "Asha Rao", is_admin: true,
        features: ["tasks","email","whatsapp","projects","people","chat","approvals","crm","integrations","artifacts","knowledge","calendar"],
        permissions: [], capabilities: [], roles: ["owner"], organization: { id: "org1", slug: "acme", display_name: "Acme Works" } },
    });
    await gotoAndSettle(page, route, 6000);
    await page.addStyleTag({ content: "nextjs-portal{display:none!important}*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" });
    await setLook(page, LOOK);
    await captureContexts(page, name, { out: OUT, only: CTX, look: LOOK === "glass" ? "glass" : undefined });
    if (name === "projects") {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(800);
      const menu = await firstVisible(page, "Menu");
      if (menu) {
        await menu.click();
        await page.waitForTimeout(1500);
        await capture(page, `${name}-drawer--${LOOK === "glass" ? "glass-" : ""}mobile-390`, OUT);
      }
    }
  });
}
