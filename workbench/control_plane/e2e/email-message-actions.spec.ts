import { expect, test, type Locator, type Page } from "@playwright/test";

import { ACCOUNT, IDS, installEmailMocks } from "./email-message-fixtures";

/**
 * The reading pane, after the owner's two asks of 2026-10-10.
 *
 *   1. The same action row on a single email and on each message of a
 *      thread: Reply, Reply all, Forward and "More actions". An item of a
 *      thread card acts on THAT message, not on the open conversation.
 *   2. The body adapts to dark mode: the sender's own dark design, the app's
 *      tokens for simple HTML, or an invert with pictures re-inverted. The
 *      member can ask for the light version of one message.
 *
 * And the "Loading message…" that never ended: a fetch of the last mail left
 * its flag on, and the next mail showed the line for good.
 *
 * `src/app/email/lib/messageActions.test.ts` and `bodyLook.test.ts` hold the
 * pure rules. This file holds what only a browser can show.
 */

async function open(page: Page, id: string) {
  await page.goto(`/email?email=${id}&account=${ACCOUNT.id}`);
  await expect(page.locator(`[data-message-actions="${id}"]`)).toBeVisible({ timeout: 60_000 });
}

const row = (page: Page, id: string) => page.locator(`[data-message-actions="${id}"]`);
const frame = (page: Page) => page.locator('iframe[title="Email content"]').first();

test.describe("The action row of a message", () => {
  test.describe.configure({ timeout: 120_000 });

  test("a single email has Reply, Reply all, Forward and More actions", async ({ page }) => {
    await installEmailMocks(page, [IDS.text]);
    await open(page, IDS.text);
    const r = row(page, IDS.text);
    for (const name of ["Reply", "Reply all", "Forward", "More actions"]) {
      await expect(r.getByRole("button", { name, exact: true })).toBeVisible();
    }
    // The big capture button left the header. It is the first menu item now.
    await expect(page.getByRole("button", { name: /^Add to My Tasks/ }).filter({ hasText: "Add to My Tasks" })).toHaveCount(0);
    await r.getByRole("button", { name: "Reply", exact: true }).click();
    await expect(page.getByPlaceholder("Write your reply…")).toBeVisible();
  });

  test("the menu works by keyboard, and Escape gives focus back", async ({ page }) => {
    await installEmailMocks(page, [IDS.text]);
    await open(page, IDS.text);
    const more = row(page, IDS.text).getByRole("button", { name: "More actions" });
    await more.focus();
    await page.keyboard.press("Enter");
    const menu = page.getByRole("menu", { name: "More actions" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "Add to My Tasks" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "Archive" })).toBeFocused();
    await page.keyboard.press("End");
    await expect(menu.getByRole("menuitem", { name: "Report spam / phishing" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "Add to My Tasks" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(more).toBeFocused();
    await expect(more).toHaveAttribute("aria-expanded", "false");
  });

  test("an item of a thread card acts on its own message", async ({ page }) => {
    const rec = await installEmailMocks(page, [IDS.threadNew]);
    await open(page, IDS.threadNew);
    // The older message is collapsed. Open it, and its row appears.
    await page.getByText("Can we run the trial on Friday?").first().click();
    const older = row(page, IDS.threadOld);
    await expect(older).toBeVisible();
    await older.getByRole("button", { name: "More actions" }).click();
    await page.getByRole("menu").getByRole("menuitem", { name: "Flag", exact: true }).click();
    await expect.poll(() => rec.patches.length).toBe(1);
    expect(rec.patches[0]).toEqual({ id: IDS.threadOld, body: { is_flagged: true } });
    // The card shows it at once: its menu now offers Unflag.
    await older.getByRole("button", { name: "More actions" }).click();
    await expect(page.getByRole("menu").getByRole("menuitem", { name: "Unflag" })).toBeVisible();
    await page.keyboard.press("Escape");

    // Archive from the menu of the OPEN message names the open message.
    await row(page, IDS.threadNew).getByRole("button", { name: "More actions" }).click();
    await page.getByRole("menu").getByRole("menuitem", { name: "Archive" }).click();
    await expect.poll(() => rec.patches.length).toBe(2);
    expect(rec.patches[1]).toEqual({ id: IDS.threadNew, body: { folder: "archive" } });
  });

  test("a narrow pane moves Reply all and Forward into the menu", async ({ page }) => {
    await installEmailMocks(page, [IDS.text]);
    await page.setViewportSize({ width: 390, height: 844 });
    // A phone opens the list first. A tap on the row opens the mail.
    await page.goto(`/email?account=${ACCOUNT.id}`);
    await page.locator(`[data-email-row="${IDS.text}"]`).click({ timeout: 60_000 });
    const r = row(page, IDS.text);
    await expect(r).toBeVisible();
    await expect(r).toHaveAttribute("data-tier", "collapsed");
    await expect(r.getByRole("button", { name: "Reply all" })).toHaveCount(0);
    await r.getByRole("button", { name: "More actions" }).click();
    await expect(page.getByRole("menu").getByRole("menuitem", { name: "Reply all" })).toBeVisible();
    await expect(page.getByRole("menu").getByRole("menuitem", { name: "Forward" })).toBeVisible();
  });
});

test.describe("The body in dark mode", () => {
  test.describe.configure({ timeout: 120_000 });

  test("simple HTML takes the tokens, and a newsletter is inverted", async ({ page }) => {
    await installEmailMocks(page, [IDS.plain, IDS.newsletter]);
    await open(page, IDS.plain);
    await expect(frame(page)).toHaveAttribute("data-body-look", "tokens");
    // The frame's body draws on the card colour, not on white.
    const bg = await frame(page).evaluate((f: HTMLIFrameElement) =>
      getComputedStyle(f.contentDocument!.body).backgroundColor);
    expect(bg).not.toBe("rgb(255, 255, 255)");

    await open(page, IDS.newsletter);
    await expect(frame(page)).toHaveAttribute("data-body-look", "invert");
    const filters = await frame(page).evaluate((f: HTMLIFrameElement) => {
      const doc = f.contentDocument!;
      const img = doc.querySelector('img[alt="Autumn sale"]')!;
      return { root: getComputedStyle(doc.documentElement).filter, img: getComputedStyle(img).filter };
    });
    expect(filters.root).toContain("invert(1)");
    expect(filters.img).toContain("invert(1)");
  });

  test("a mail with its own dark design gets it, untransformed", async ({ page }) => {
    await installEmailMocks(page, [IDS.darkAware]);
    await open(page, IDS.darkAware);
    await expect(frame(page)).toHaveAttribute("data-body-look", "native");
    // The sender's dark rule applies even on a light OS: the frame takes its
    // scheme from the OS, so the pane turns the dark media query on itself.
    await page.emulateMedia({ colorScheme: "light" });
    const seen = await frame(page).evaluate((f: HTMLIFrameElement) => {
      const wrap = f.contentDocument!.querySelector(".wrap")!;
      return {
        bg: getComputedStyle(wrap).backgroundColor,
        filter: getComputedStyle(f.contentDocument!.documentElement).filter,
      };
    });
    expect(seen.bg).toBe("rgb(15, 23, 42)");
    expect(seen.filter).toBe("none");
  });

  test("the light version shows the original, and stays after a reload", async ({ page }) => {
    await installEmailMocks(page, [IDS.newsletter]);
    await open(page, IDS.newsletter);
    const toggle = row(page, IDS.newsletter).getByRole("button", { name: "View light version" });
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await toggle.click();
    await expect(frame(page)).toHaveAttribute("data-body-look", "original");
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await page.reload();
    await expect(frame(page)).toHaveAttribute("data-body-look", "original", { timeout: 60_000 });
  });

  test("light mode shows the original, with no toggle", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("theme", "light"));
    await installEmailMocks(page, [IDS.newsletter]);
    await open(page, IDS.newsletter);
    await expect(frame(page)).toHaveAttribute("data-body-look", "original");
    await expect(row(page, IDS.newsletter).getByRole("button", { name: "View light version" })).toHaveCount(0);
  });

  test("the invert holds after Show images", async ({ page }) => {
    await installEmailMocks(page, [IDS.newsletter]);
    await open(page, IDS.newsletter);
    await page.getByRole("button", { name: "Show images" }).click();
    await expect(frame(page)).toHaveAttribute("data-body-look", "invert");
    const logo = await frame(page).evaluate((f: HTMLIFrameElement) => {
      const img = f.contentDocument!.querySelector('img[alt="Fracktal Works"]') as HTMLImageElement;
      return { src: img.getAttribute("src") ?? "", filter: getComputedStyle(img).filter };
    });
    expect(logo.src).toContain("/api/email/image-proxy?url=");
    expect(logo.filter).toContain("invert(1)");
  });
});

test.describe("Loading message…", () => {
  test.describe.configure({ timeout: 120_000 });

  // Mutation caught: with the old boolean, the second mail showed "Loading
  // message…" for good, because the first fetch was cancelled and never
  // cleared the flag.
  test("a mail with a body opened during another fetch draws at once", async ({ page }) => {
    await installEmailMocks(page, [IDS.slow, IDS.text], { slowMs: 6000 });
    await page.goto(`/email?account=${ACCOUNT.id}`);
    const slowRow = page.locator(`[data-email-row="${IDS.slow}"]`);
    await expect(slowRow).toBeVisible({ timeout: 60_000 });
    await slowRow.click();
    await expect(page.getByText("Loading message…")).toBeVisible();
    await page.locator(`[data-email-row="${IDS.text}"]`).click();
    await expect(page.getByText("This is a plain text mail.").first()).toBeVisible({ timeout: 3000 });
    await expect(page.getByText("Loading message…")).toHaveCount(0);
  });
});

/**
 * A capture of an element in the mail frame, cut to the frame. A wide row of
 * a newsletter runs past the frame's edge, and the page beside the frame is
 * not part of the mail.
 */
async function shotInFrame(page: Page, el: Locator): Promise<string> {
  await el.scrollIntoViewIfNeeded();
  const box = await el.boundingBox();
  const fr = await frame(page).boundingBox();
  if (!box || !fr) throw new Error("no box to capture");
  const x = Math.max(box.x, fr.x + 2);
  const y = Math.max(box.y, fr.y + 2);
  const right = Math.min(box.x + box.width, fr.x + fr.width - 2);
  const bottom = Math.min(box.y + box.height, fr.y + fr.height - 2);
  const clip = { x, y, width: right - x, height: bottom - y };
  return (await page.screenshot({ clip })).toString("base64");
}

/**
 * The contrast of the text of a captured element against what lies under it.
 * The median pixel is the backing, because text covers little of a line. The
 * text is the pixel farthest from it. The browser decodes the capture, so
 * the spec needs no image library.
 */
async function pixelContrast(page: Page, el: Locator): Promise<number> {
  const png = await shotInFrame(page, el);
  return page.evaluate(async (data) => {
    const img = new Image();
    img.src = `data:image/png;base64,${data}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(img, 0, 0);
    const px = ctx.getImageData(0, 0, c.width, c.height).data;
    const lum = (r: number, g: number, b: number) => {
      const ch = (v: number) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
    };
    const all: number[] = [];
    for (let i = 0; i < px.length; i += 4) all.push(lum(px[i], px[i + 1], px[i + 2]));
    all.sort((a, b) => a - b);
    const med = all[Math.floor(all.length / 2)];
    const lo = all[0];
    const hi = all[all.length - 1];
    const text = hi - med > med - lo ? hi : lo;
    return (Math.max(text, med) + 0.05) / (Math.min(text, med) + 0.05);
  }, png);
}

/** The mean colour of a captured element, 0 to 255 per channel. */
async function meanColour(page: Page, el: Locator): Promise<number[]> {
  const png = await shotInFrame(page, el);
  return page.evaluate(async (data) => {
    const img = new Image();
    img.src = `data:image/png;base64,${data}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(img, 0, 0);
    const px = ctx.getImageData(0, 0, c.width, c.height).data;
    const sum = [0, 0, 0];
    for (let i = 0; i < px.length; i += 4) for (let k = 0; k < 3; k++) sum[k] += px[i + k];
    return sum.map((v) => v / (px.length / 4));
  }, png);
}

test.describe("Fix round 1", () => {
  test.describe.configure({ timeout: 120_000 });

  // P1-b, round 2: the light island. Mutations caught, each one red:
  // - no white backing: the dark text of a box whose picture never loads drew
  //   dark on the dark card (round 0);
  // - no re-invert: the photo drew inverted, and white text on it turned dark
  //   on a dark photo (round 1).
  test("a box with a background picture is a light island in dark mode", async ({ page }) => {
    await installEmailMocks(page, [IDS.newsletter]);
    await open(page, IDS.newsletter);
    await expect(frame(page)).toHaveAttribute("data-body-look", "invert");
    const filters = await frame(page).evaluate((f: HTMLIFrameElement) => {
      const doc = f.contentDocument!;
      const of = (sel: string) => getComputedStyle(doc.querySelector(sel)!).filter;
      const invoice = doc.querySelector('[class="probe-invoice"]')!;
      return {
        box: getComputedStyle(invoice.parentElement!).filter,
        table: of('[class="probe-table-bg"]'),
        photo: of('[class="probe-photo-box"]'),
        // A box inside a re-inverted box gets no second filter.
        tableText: of('[class="probe-table-text"]'),
      };
    });
    expect(filters.box).toContain("invert(1)");
    expect(filters.table).toContain("invert(1)");
    expect(filters.photo).toContain("invert(1)");
    expect(filters.tableText).toBe("none");
    const body = page.frameLocator('iframe[title="Email content"]').first();
    expect(await pixelContrast(page, body.locator('[class="probe-invoice"]'))).toBeGreaterThan(4.5);
    expect(await pixelContrast(page, body.locator('[class="probe-table-text"]'))).toBeGreaterThan(4.5);
    // White text over the photo, against the photo under it.
    expect(await pixelContrast(page, body.locator('[class="probe-photo-text"]'))).toBeGreaterThan(4.5);
    // The reverse case: the sender hid white text on white. It stays hidden.
    expect(await pixelContrast(page, body.locator('[class="probe-hidden"]'))).toBeLessThan(1.5);

    // The photo keeps its true colours: its mean colour is near the one that
    // light mode draws. An inverted photo is far from it.
    const dark = await meanColour(page, body.locator('[class="probe-photo-box"]'));
    const lightPage = await page.context().newPage();
    await lightPage.addInitScript(() => localStorage.setItem("theme", "light"));
    await installEmailMocks(lightPage, [IDS.newsletter]);
    await open(lightPage, IDS.newsletter);
    await expect(frame(lightPage)).toHaveAttribute("data-body-look", "original");
    const lightBody = lightPage.frameLocator('iframe[title="Email content"]').first();
    const light = await meanColour(lightPage, lightBody.locator('[class="probe-photo-box"]'));
    for (let k = 0; k < 3; k++) expect(Math.abs(dark[k] - light[k]), `channel ${k}`).toBeLessThan(6);
  });

  // Verifier F1. Mutation caught: with the handlers pointed at the open email
  // (`email ?? m`), Archive, Move, Delete, Label and Add to My Tasks of an
  // older card all acted on the open email, and only Flag was checked.
  test("every item of an older card's menu sends its request for that card", async ({ page }) => {
    await installEmailMocks(page, [IDS.threadNew]);
    const sent: Array<{ method: string; path: string; body: Record<string, unknown> }> = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (!path.startsWith("/api/") || r.method() === "GET") return;
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(r.postData() ?? "{}");
      } catch {
        /* not JSON */
      }
      sent.push({ method: r.method(), path, body });
    });
    await open(page, IDS.threadNew);
    await page.getByText("Can we run the trial on Friday?").first().click();
    const older = row(page, IDS.threadOld);
    const menu = page.getByRole("menu");
    const pick = async (name: string) => {
      await older.getByRole("button", { name: "More actions" }).click();
      await menu.getByRole("menuitem", { name, exact: true }).click();
    };
    const OLD = `/api/email/messages/${IDS.threadOld}`;
    const last = () => sent[sent.length - 1];

    await pick("Flag");
    await expect.poll(() => sent.length).toBe(1);
    expect(last()).toEqual({ method: "PATCH", path: OLD, body: { is_flagged: true } });

    await pick("Label…");
    await menu.getByRole("menuitemcheckbox").first().click();
    await expect.poll(() => sent.length).toBe(2);
    expect(last().method).toBe("PATCH");
    expect(last().path).toBe(OLD);
    expect(last().body).toHaveProperty("add_labels");
    await page.keyboard.press("Escape");

    await pick("Move to…");
    await menu.getByRole("menuitem", { name: "Archive", exact: true }).click();
    await expect.poll(() => sent.length).toBe(3);
    expect(last()).toEqual({ method: "PATCH", path: OLD, body: { folder: "archive" } });

    await pick("Archive");
    await expect.poll(() => sent.length).toBe(4);
    expect(last()).toEqual({ method: "PATCH", path: OLD, body: { folder: "archive" } });

    // Add to My Tasks opens the capture for this card, in its own mailbox.
    const preview = "/api/tasks/capture/from-email/preview";
    const beforeTasks = sent.length;
    await pick("Add to My Tasks");
    await expect.poll(() => sent.slice(beforeTasks).some((r) => r.path === preview)).toBe(true);
    expect(sent.slice(beforeTasks).find((r) => r.path === preview)).toEqual({
      method: "POST",
      path: preview,
      body: { account_id: ACCOUNT.id, email_id: IDS.threadOld },
    });
    await page.keyboard.press("Escape");

    const beforeDelete = sent.length;
    await pick("Delete");
    await expect.poll(() => sent.slice(beforeDelete).some((r) => r.method === "DELETE")).toBe(true);
    expect(sent.slice(beforeDelete).find((r) => r.method === "DELETE")).toEqual({ method: "DELETE", path: OLD, body: {} });

    // Not one request named the open email.
    expect(sent.filter((r) => r.path.includes(IDS.threadNew) || r.body.email_id === IDS.threadNew)).toEqual([]);
  });

  // Found by the screenshots of fix round 1: a wide newsletter overflows the
  // frame, and a sideways scroll showed a black strip where the multiply
  // layer did not reach. The layer's shadow spread now covers the overflow.
  test("a sideways scroll of a wide newsletter shows no black strip", async ({ page }) => {
    await installEmailMocks(page, [IDS.newsletter]);
    await open(page, IDS.newsletter);
    const iframe = frame(page);
    await expect(iframe).toHaveAttribute("data-body-look", "invert");
    const scrolled = await iframe.evaluate((f: HTMLIFrameElement) => {
      f.contentWindow!.scrollTo(80, 0);
      return f.contentWindow!.scrollX;
    });
    expect(scrolled).toBeGreaterThan(0);
    const png = (await iframe.screenshot()).toString("base64");
    const edge = await page.evaluate(async (data) => {
      const img = new Image();
      img.src = `data:image/png;base64,${data}`;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.width;
      c.height = img.height;
      const ctx = c.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      // The column of pixels near the right edge, top to bottom.
      const col = ctx.getImageData(img.width - 12, 0, 1, img.height).data;
      let black = 0;
      for (let i = 0; i < col.length; i += 4) {
        if (Math.max(col[i], col[i + 1], col[i + 2]) <= 5) black += 1;
      }
      return black / img.height;
    }, png);
    // Nothing in the inverted page is pure black but a strip the layer
    // missed. The card colour has a channel above 20.
    expect(edge).toBeLessThan(0.02);
  });

  // P2-a. Mutation caught: the menu of a hydrated older card read its stale
  // copy, so it offered "Mark as unread" again after the first click.
  test("the menu of a hydrated older card follows read both ways, and a label shows its check", async ({ page }) => {
    const rec = await installEmailMocks(page, [IDS.threadNew]);
    await open(page, IDS.threadNew);
    await page.getByText("Can we run the trial on Friday?").first().click();
    const older = row(page, IDS.threadOld);
    // The card hydrated: its file from the detail fetch shows.
    await expect(page.getByText("trial-plan.pdf")).toBeVisible();
    const menu = page.getByRole("menu");
    const more = older.getByRole("button", { name: "More actions" });

    await more.click();
    await menu.getByRole("menuitem", { name: "Mark as unread" }).click();
    await expect.poll(() => rec.patches.length).toBe(1);
    await more.click();
    await menu.getByRole("menuitem", { name: "Mark as read" }).click();
    await expect.poll(() => rec.patches.length).toBe(2);
    await more.click();
    await expect(menu.getByRole("menuitem", { name: "Mark as unread" })).toBeVisible();
    expect(rec.patches.map((p) => p.body)).toEqual([{ is_read: false }, { is_read: true }]);

    await menu.getByRole("menuitem", { name: "Label…" }).click();
    const label = menu.getByRole("menuitemcheckbox").first();
    await expect(label).toHaveAttribute("aria-checked", "false");
    await label.click();
    await expect(label).toHaveAttribute("aria-checked", "true");
    await expect.poll(() => rec.patches.length).toBe(3);
    expect(rec.patches[2].id).toBe(IDS.threadOld);
  });

  // P2-b. Mutation caught: the label toggles were plain buttons, so the
  // arrows of the menu skipped them and no key could reach one.
  test("the Label view works with the keyboard only", async ({ page }) => {
    const rec = await installEmailMocks(page, [IDS.text]);
    await open(page, IDS.text);
    const more = row(page, IDS.text).getByRole("button", { name: "More actions" });
    await more.focus();
    await page.keyboard.press("Enter");
    const menu = page.getByRole("menu", { name: "More actions" });
    for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "Label…" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(menu.getByRole("menuitem", { name: "Label" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    const first = menu.getByRole("menuitemcheckbox").first();
    await expect(first).toBeFocused();
    await page.keyboard.press("Space");
    await expect(first).toHaveAttribute("aria-checked", "true");
    await expect.poll(() => rec.patches.length).toBe(1);
    expect(rec.patches[0].body).toHaveProperty("add_labels");
    // Shift+Tab from the first stop reaches the text box at the end.
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("Shift+Tab");
    await expect(menu.getByRole("textbox", { name: "Create label" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(more).toBeFocused();
  });
});
