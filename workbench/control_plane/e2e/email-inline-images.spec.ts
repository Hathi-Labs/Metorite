import { expect, test, type Page, type Route } from "@playwright/test";

import { ACCOUNT, CIDS, IDS, installEmailMocks } from "./email-message-fixtures";

/**
 * Inline `cid:` pictures in the reading pane (WS-17, the inline images fix,
 * `email_app_master_plan.md` §16.6). The owner's report of 2026-10-13: a
 * pasted screenshot showed as a broken picture with the alt text "image.png".
 *
 * The pane writes `cid:X` as `/api/email/messages/{id}/inline?cid=X`, and the
 * frame's CSP lets that one path in. This file stubs that route with a real
 * PNG and checks that the picture LOADS (`naturalWidth > 0`) in a real frame,
 * in each look. `src/app/email/lib/inlineImages.test.ts` holds the pure rules.
 *
 * Mutations caught, each one red: no rewrite of `cid:` (the frame cannot
 * fetch it), an absolute rewritten URL (the dark look leaves it inverted),
 * and a CSP with no inline path (the frame refuses the request).
 */

/** A 24 x 12 red PNG. */
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAABgAAAAMCAIAAAD3UuoiAAAAF0lEQVR4nGO4aaVNFcQwatCoQaMG4UMAAZdl0KrP06AAAAAASUVORK5CYII=",
  "base64",
);

const frame = (page: Page) => page.locator('iframe[title="Email content"]').first();

async function open(page: Page, id: string) {
  await page.goto(`/email?email=${id}&account=${ACCOUNT.id}`);
  await expect(page.locator(`[data-message-actions="${id}"]`)).toBeVisible({ timeout: 60_000 });
}

/**
 * Stub the inline route. It serves the PNG for a Content-ID of the message,
 * and 404 for anything else. Returns the cids that the frame asked for.
 */
async function stubInline(page: Page, served: Record<string, string>): Promise<string[]> {
  const asked: string[] = [];
  await page.route(/.*\/api\/email\/messages\/[^/]+\/inline\?.*/, (r: Route) => {
    const url = new URL(r.request().url());
    const id = url.pathname.split("/").slice(-2)[0];
    const cid = url.searchParams.get("cid") ?? "";
    asked.push(`${id}|${cid}`);
    if (served[id] === cid) {
      return r.fulfill({ status: 200, contentType: "image/png", body: PNG });
    }
    return r.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"Picture not found"}' });
  });
  return asked;
}

/** The picture with this alt text: its natural width, and its filter. */
async function picture(page: Page, alt: string) {
  const img = frame(page);
  await expect
    .poll(
      () =>
        img.evaluate(
          (f: HTMLIFrameElement, a: string) => {
            const el = f.contentDocument?.querySelector(`img[alt="${a}"]`) as HTMLImageElement | null;
            return el ? el.complete && el.naturalWidth > 0 : false;
          },
          alt,
        ),
      { timeout: 15_000 },
    )
    .toBe(true);
  return img.evaluate(
    (f: HTMLIFrameElement, a: string) => {
      const el = f.contentDocument!.querySelector(`img[alt="${a}"]`) as HTMLImageElement;
      return { width: el.naturalWidth, src: el.getAttribute("src") ?? "", filter: getComputedStyle(el).filter };
    },
    alt,
  );
}

test.describe("Inline cid: pictures", () => {
  test.describe.configure({ timeout: 120_000 });

  test("the owner's mail: the pasted screenshot loads", async ({ page }) => {
    await installEmailMocks(page, [IDS.inlineSimple]);
    const asked = await stubInline(page, { [IDS.inlineSimple]: CIDS.outlook });
    await open(page, IDS.inlineSimple);
    const shot = await picture(page, "image.png");
    expect(shot.width).toBe(24);
    expect(shot.src).toBe(`/api/email/messages/${IDS.inlineSimple}/inline?cid=${encodeURIComponent(CIDS.outlook)}`);
    expect(asked).toContain(`${IDS.inlineSimple}|${CIDS.outlook}`);
    // An inline picture is part of the mail, so no "Show images" banner.
    await expect(page.getByRole("button", { name: "Show images" })).toHaveCount(0);
  });

  test("a styled mail in dark mode: the cid picture loads, re-inverted, while the remote logo stays blocked", async ({ page }) => {
    await installEmailMocks(page, [IDS.inlineStyled]);
    await stubInline(page, { [IDS.inlineStyled]: CIDS.gmail });
    // Chromium reports a request that the CSP then refuses, so the check is
    // on the answer: no response, and a "csp" failure (the same on main).
    const answered: string[] = [];
    const refused: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("cdn.example.test") || res.url().includes("image-proxy")) answered.push(res.url());
    });
    page.on("requestfailed", (req) => {
      if (req.url().includes("cdn.example.test")) refused.push(`${req.failure()?.errorText} ${req.url()}`);
    });
    await open(page, IDS.inlineStyled);
    await expect(frame(page)).toHaveAttribute("data-body-look", "invert");
    const chart = await picture(page, "chart.png");
    expect(chart.width).toBe(24);
    // The picture gets the filter again, so it looks as sent.
    expect(chart.filter).toContain("invert(1)");
    // The remote gate still holds the remote logo.
    await expect(page.getByRole("button", { name: "Show images" })).toBeVisible();
    await expect.poll(() => refused).toContain("csp https://cdn.example.test/logo.png");
    expect(answered).toEqual([]);
  });

  test("light mode: the cid picture loads, with no filter", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("theme", "light"));
    await installEmailMocks(page, [IDS.inlineStyled]);
    await stubInline(page, { [IDS.inlineStyled]: CIDS.gmail });
    await open(page, IDS.inlineStyled);
    await expect(frame(page)).toHaveAttribute("data-body-look", "original");
    const chart = await picture(page, "chart.png");
    expect(chart.width).toBe(24);
    expect(chart.filter).toBe("none");
  });

  test("after Show images the cid picture still loads", async ({ page }) => {
    await installEmailMocks(page, [IDS.inlineStyled]);
    await stubInline(page, { [IDS.inlineStyled]: CIDS.gmail });
    await open(page, IDS.inlineStyled);
    await page.getByRole("button", { name: "Show images" }).click();
    const chart = await picture(page, "chart.png");
    expect(chart.width).toBe(24);
    expect(chart.src.startsWith("/api/email/messages/")).toBe(true);
  });
});
